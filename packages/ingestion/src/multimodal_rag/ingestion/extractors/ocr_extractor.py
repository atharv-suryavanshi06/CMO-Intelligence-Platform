"""
OCR Extractor Module
======================

OCR fallback path for content that native text extraction can't be
trusted for: scanned pages and broken-font-mapping pages flagged by the
Page Pre-Analyzer (Module 2), and figure regions from the Layout
Segmenter (Module 3) that turn out to be text-in-an-image rather than a
real diagram.

Chosen backend: RapidOCR (ONNX Runtime based). This corrects a detail in
the locked engineering decisions: that document named EasyOCR as
Docling's bundled OCR engine, based on external documentation that turned
out to describe an older Docling integration. Testing during Module 3
showed Docling actually ships RapidOCR, and it runs fully offline with
locally cached model weights - no Hugging Face dependency, unlike
Docling's own layout/table models. The architecture itself already
treats OCR as a swappable, config-driven stage (see
final_engineering_decisions.md), so this is a corrected implementation
detail, not a design change.

Adversarial cases this module explicitly addresses (per the "assume your
mentor will try to break this" requirement):
- Skewed/rotated scans: an optional deskew pass (OpenCV, minAreaRect-based)
  is available, disabled by default. Testing during development (rotated
  synthetic text at 8-35 degrees) showed RapidOCR's own detector already
  tolerates moderate skew well, and the deskew step sometimes slightly
  REDUCED confidence rather than improving it - likely interpolation blur
  outweighing a small alignment gain. This is evidence from clean
  synthetic images, not real scans, so it's kept as an easy config toggle
  to re-validate against real scanned PDFs during calibration, rather than
  defaulted on based on an assumption that turned out not to hold here.
  Documented limitation either way: this only corrects small skew angles
  (roughly +/-45 degrees), NOT full 90/180/270-degree orientation flips -
  that needs a separate page-orientation classifier, out of scope here.
- Illegible / handwritten text: confidence is surfaced per line and
  overall, never silently discarded or upgraded to "looks fine". Deciding
  pass/fail against a threshold is the Validator stage's job, not this
  module's - this module reports, it doesn't judge.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


class OCRExtractionError(Exception):
    """Raised when the OCR engine itself fails (corrupt image data,
    engine initialization failure, etc) - not raised for "no text found",
    which is a normal, valid result."""


@dataclass
class OCRConfig:
    deskew_enabled: bool = False
    # NOTE: defaulted to False based on actual measurement, not assumption.
    # Testing against synthetic clean-text images rotated 8-35 degrees
    # showed RapidOCR's own text detector is already fairly rotation-
    # tolerant, and this module's deskew step (OpenCV minAreaRect + cubic-
    # interpolation rotation) sometimes slightly REDUCED OCR confidence
    # versus doing nothing, likely because interpolation blur outweighed
    # the small alignment benefit. This is left in place and easy to
    # enable because real scanned documents (noise, uneven lighting,
    # compression artifacts) may behave differently than clean synthetic
    # test images - this should be re-validated against real scanned PDFs
    # during the calibration phase, not assumed either way.
    min_angle_to_correct_degrees: float = 0.5
    # Skip rotation for angles smaller than this - not worth the
    # interpolation blur for a barely-perceptible skew.
    max_correctable_angle_degrees: float = 45.0
    # Angles beyond this are treated as a detection failure (e.g. a
    # mostly-blank or non-text image confusing minAreaRect), not applied -
    # rotating a page 44 degrees because of a bad angle estimate would be
    # far worse than leaving it alone.


@dataclass
class OCRLineResult:
    text: str
    confidence: float
    bbox: list[tuple[float, float]]  # quadrilateral corner points


@dataclass
class OCRResult:
    text: str  # all detected lines joined with newlines
    lines: list[OCRLineResult] = field(default_factory=list)
    mean_confidence: float = 0.0
    was_deskewed: bool = False
    deskew_angle_degrees: float = 0.0
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Engine (lazy singleton - RapidOCR model init has real overhead, don't
# pay it once per region)
# --------------------------------------------------------------------------

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        from rapidocr import RapidOCR
        logger.info("Initializing RapidOCR engine (first use)")
        _engine = RapidOCR()
    return _engine


# --------------------------------------------------------------------------
# Deskew
# --------------------------------------------------------------------------

def deskew_image(image_np: np.ndarray, config: OCRConfig) -> tuple[np.ndarray, float]:
    """
    Correct small rotation skew in a scanned image before OCR.

    Returns (possibly-rotated image, angle actually applied in degrees).
    Angle is 0.0 if no correction was applied (already straight, or the
    estimate was out of the correctable range - see max_correctable_angle_degrees).
    """
    gray = cv2.cvtColor(image_np, cv2.COLOR_RGB2GRAY) if image_np.ndim == 3 else image_np
    thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]

    coords = np.column_stack(np.where(thresh > 0))
    if coords.shape[0] < 20:
        # Too little foreground content to estimate an angle reliably -
        # e.g. a near-blank page. Don't guess.
        return image_np, 0.0

    angle = cv2.minAreaRect(coords)[-1]
    # cv2.minAreaRect returns an angle in [-90, 0); normalize to a
    # signed small-rotation convention.
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle

    if abs(angle) < config.min_angle_to_correct_degrees:
        return image_np, 0.0
    if abs(angle) > config.max_correctable_angle_degrees:
        logger.warning(
            "Estimated skew angle %.1f degrees exceeds max_correctable_angle_degrees "
            "(%.1f) - treating as an unreliable estimate and skipping deskew.",
            angle, config.max_correctable_angle_degrees,
        )
        return image_np, 0.0

    (h, w) = image_np.shape[:2]
    center = (w // 2, h // 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(
        image_np, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )
    return rotated, angle


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------

def _to_numpy(image: "Image.Image | np.ndarray | str | Path") -> np.ndarray:
    if isinstance(image, np.ndarray):
        return image
    if isinstance(image, (str, Path)):
        image = Image.open(image)
    if isinstance(image, Image.Image):
        return np.array(image.convert("RGB"))
    raise TypeError(f"Unsupported image input type: {type(image)}")


def run_ocr(
    image: "Image.Image | np.ndarray | str | Path", config: OCRConfig | None = None
) -> OCRResult:
    """
    Run OCR on a single image (a full scanned page, or a cropped figure
    region suspected of containing text).

    Never raises for "no text found" - that's a valid, normal result
    (empty text, confidence 0.0). Raises OCRExtractionError only if the
    engine itself fails (e.g. genuinely corrupt image data).
    """
    config = config or OCRConfig()
    notes: list[str] = []

    try:
        image_np = _to_numpy(image)
    except Exception as e:
        raise OCRExtractionError(f"Could not load image for OCR: {e}") from e

    was_deskewed = False
    deskew_angle = 0.0
    if config.deskew_enabled:
        try:
            image_np, deskew_angle = deskew_image(image_np, config)
            was_deskewed = deskew_angle != 0.0
            if was_deskewed:
                notes.append(f"Deskewed by {deskew_angle:.1f} degrees before OCR")
        except Exception as e:
            # Deskew is a best-effort preprocessing step - a failure here
            # should fall back to running OCR on the original image, not
            # abort the whole extraction.
            logger.warning("Deskew step failed, proceeding with original image: %s", e)
            notes.append(f"Deskew step failed and was skipped: {e}")

    try:
        engine = _get_engine()
        result = engine(image_np)
    except Exception as e:
        raise OCRExtractionError(f"RapidOCR engine failed: {e}") from e

    txts = result.txts or ()
    scores = result.scores or ()
    boxes = result.boxes if result.boxes is not None else []

    if not txts:
        notes.append("No text detected by OCR engine")
        return OCRResult(
            text="", lines=[], mean_confidence=0.0,
            was_deskewed=was_deskewed, deskew_angle_degrees=deskew_angle, notes=notes,
        )

    lines = [
        OCRLineResult(
            text=txt,
            confidence=float(score),
            bbox=[tuple(pt) for pt in box],
        )
        for txt, score, box in zip(txts, scores, boxes)
    ]
    mean_confidence = sum(l.confidence for l in lines) / len(lines)

    return OCRResult(
        text="\n".join(l.text for l in lines),
        lines=lines,
        mean_confidence=mean_confidence,
        was_deskewed=was_deskewed,
        deskew_angle_degrees=deskew_angle,
        notes=notes,
    )
