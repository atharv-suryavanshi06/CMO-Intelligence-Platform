"""
Page Pre-Analyzer Module
=========================

Stage 2 of the ingestion pipeline. Consumes RawPage objects from the PDF
Loader (Module 1) and computes cheap, per-page signals that hint at how
a page should be handled downstream:

- Is the native text layer trustworthy, or is the font mapping broken (cid)?
- Does the page look scanned (little/no real text, mostly image area)?
- Does the page likely have a multi-column layout?
- Does the page likely contain one or more tables (vector line/rect density)?
- Is the page effectively blank (skip expensive processing)?

Design contract (per locked architecture):
- This module produces SIGNALS, not final extraction decisions. A page
  can simultaneously be multi-column AND contain a table AND have a
  broken-font suspect region - the Layout Segmenter (Module 3) and
  region-level routing make the actual extraction-path decisions. This
  module's output is a fast pre-filter and a set of hints, never the
  final word.
- All classification thresholds are configuration-driven (PreAnalyzerConfig),
  never hardcoded in the analysis logic, specifically so a future
  calibration phase can tune them against real documents without touching
  this module's code. Defaults are deliberately conservative: they bias
  toward "flag for closer inspection" over "confidently misclassify",
  since it is far cheaper to run full layout segmentation on a page that
  turns out simple than to skip it on a page that turns out complex.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from multimodal_rag.ingestion.loaders.pdf_loader import FontSpan, RawPage

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

@dataclass
class PreAnalyzerConfig:
    """
    All Pre-Analyzer thresholds, in one place, JSON-serializable.

    These defaults are conservative starting points, not tuned values.
    Per the agreed plan: implementation proceeds now with these defaults;
    a future calibration phase will fit better values against a labeled
    sample of real documents (see `calibrate_from_labeled_samples` below
    for the intended extension point).
    """

    # --- Blank page detection ---
    blank_page_max_chars: int = 3
    # A page with <= this many stripped text characters AND no images is
    # treated as blank and skipped from expensive downstream processing.

    # --- Scanned-page detection ---
    scanned_min_image_area_ratio: float = 0.5
    # Fraction of page area covered by images, above which a page is a
    # scan candidate (combined with low text char count below).
    scanned_max_text_chars: int = 40
    # A page is a scan candidate only if text_char_count is at or below
    # this AND image_area_ratio is at or above scanned_min_image_area_ratio.
    # Both conditions are required - a text-heavy page with one large
    # background image should NOT be flagged as scanned.

    # --- Broken font / cid detection ---
    cid_suspect_span_ratio_threshold: float = 0.05
    # Fraction of font spans on the page flagged cid-suspect (by Module 1's
    # per-span heuristic) above which the whole page is flagged as having
    # a broken font mapping. Page-level aggregation of the span-level
    # signal computed in pdf_loader._detect_cid_suspect.
    cid_min_spans_for_judgment: int = 3
    # Minimum number of font spans required before we trust the ratio at
    # all - a page with only 1-2 spans total shouldn't have its whole
    # classification swing on a single suspect span.

    # --- Multi-column detection ---
    column_min_spans_for_detection: int = 8
    # Minimum font spans on a page before attempting column detection at
    # all - too few spans make any gap analysis unreliable.
    column_gap_ratio_threshold: float = 0.08
    # Minimum gap between the two candidate columns, as a fraction of page
    # width, to call it a real column boundary rather than normal
    # word/sentence spacing variance.
    column_min_side_fraction: float = 0.25
    # Each candidate column must contain at least this fraction of all
    # spans on the page, so a single indented pull-quote or caption isn't
    # mistaken for a second column.

    # --- Table candidate detection ---
    table_min_line_rect_count: int = 4
    # Minimum count of vector line/rect drawing objects on a page before
    # it's flagged as a table candidate. Deliberately low/conservative -
    # false positives here just mean the Layout Segmenter double-checks a
    # page that turns out not to have a table, which is cheap; false
    # negatives mean a real table might be missed entirely.

    @classmethod
    def default(cls) -> "PreAnalyzerConfig":
        return cls()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PreAnalyzerConfig":
        """
        Build a config from a (possibly partial) dict, falling back to
        defaults for any missing keys. Partial configs are supported
        deliberately: a future calibration workflow may only tune a
        subset of thresholds and should not be required to re-specify
        every field.
        """
        valid_keys = {f.name for f in fields(cls)}
        unknown_keys = set(data) - valid_keys
        if unknown_keys:
            logger.warning(
                "Ignoring unknown PreAnalyzerConfig keys in supplied config: %s",
                sorted(unknown_keys),
            )
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**{**cls.default().to_dict(), **filtered})

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.write_text(json.dumps(self.to_dict(), indent=2))
        logger.info("Saved PreAnalyzerConfig to %s", path)

    @classmethod
    def load(cls, path: str | Path) -> "PreAnalyzerConfig":
        """
        Load a config from JSON. If the file does not exist, logs a
        warning and returns defaults rather than raising - a missing
        calibration file should degrade gracefully to "not yet
        calibrated", not crash the pipeline.
        """
        path = Path(path)
        if not path.exists():
            logger.warning(
                "PreAnalyzerConfig file not found at %s, using defaults", path
            )
            return cls.default()
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(
                "PreAnalyzerConfig file at %s could not be read (%s), using defaults",
                path, e,
            )
            return cls.default()
        return cls.from_dict(data)


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

@dataclass
class PageAnalysis:
    """
    Signals computed for a single page. Every boolean here is a HINT for
    downstream stages, not a final decision - see module docstring.
    `notes` is a human-readable log of which signals fired and why,
    intended to feed directly into the extraction audit (extracted_text_audit.md)
    produced later by the Output Writer stage.
    """
    page_number: int
    text_char_count: int
    image_area_ratio: float
    cid_suspect_span_ratio: float
    line_rect_count: int

    is_blank: bool
    is_scanned_candidate: bool
    has_broken_font_suspect: bool
    is_multi_column_candidate: bool
    column_count_estimate: int
    has_table_candidate: bool

    notes: list[str] = field(default_factory=list)

    @property
    def requires_ocr_hint(self) -> bool:
        """Convenience flag: does this page likely need OCR at all, for
        at least some region? True if scanned OR native text is untrustworthy."""
        return self.is_scanned_candidate or self.has_broken_font_suspect


# --------------------------------------------------------------------------
# Analysis logic
# --------------------------------------------------------------------------

def _compute_image_area_ratio(page: RawPage) -> float:
    page_area = page.width * page.height
    if page_area <= 0:
        return 0.0
    # Images may overlap (e.g. a background image plus a logo) - summing
    # naively can exceed 1.0. We clip rather than compute true covered
    # area (which would need polygon union) because this is a cheap
    # pre-filter signal, not a precise measurement; over-counting overlap
    # only makes the scan-candidate check slightly more conservative,
    # which is the intended bias.
    total_area = sum(
        max(0.0, img.bbox[2] - img.bbox[0]) * max(0.0, img.bbox[3] - img.bbox[1])
        for img in page.images
    )
    return min(1.0, total_area / page_area)


def _compute_cid_suspect_ratio(font_spans: list[FontSpan], config: PreAnalyzerConfig) -> float:
    if len(font_spans) < config.cid_min_spans_for_judgment:
        return 0.0
    suspect_count = sum(1 for s in font_spans if s.is_cid_suspect)
    return suspect_count / len(font_spans)


def _detect_multi_column(
    font_spans: list[FontSpan], page_width: float, config: PreAnalyzerConfig
) -> tuple[bool, int]:
    """
    Heuristic multi-column detection via largest-gap splitting on span
    left-edge (x0) coordinates.

    Returns (is_multi_column, estimated_column_count). Column count
    estimate is capped at 2 - distinguishing 3+ columns reliably from
    this cheap a signal isn't worth the added complexity; the Layout
    Segmenter (ML-based) resolves finer-grained column structure.
    """
    if len(font_spans) < config.column_min_spans_for_detection or page_width <= 0:
        return False, 1

    x0s = sorted(span.bbox[0] for span in font_spans)
    gaps = [(x0s[i + 1] - x0s[i], i) for i in range(len(x0s) - 1)]
    if not gaps:
        return False, 1

    max_gap, split_idx = max(gaps, key=lambda g: g[0])
    gap_ratio = max_gap / page_width

    left_count = split_idx + 1
    right_count = len(x0s) - left_count
    min_side = config.column_min_side_fraction * len(x0s)

    if gap_ratio >= config.column_gap_ratio_threshold and left_count >= min_side and right_count >= min_side:
        return True, 2
    return False, 1


def analyze_page(page: RawPage, config: PreAnalyzerConfig | None = None) -> PageAnalysis:
    """
    Compute pre-analysis signals for a single page.

    This function is pure (no I/O, no side effects) precisely so it can
    be unit tested against hand-constructed RawPage fixtures and, later,
    re-run in bulk during the calibration phase without re-parsing PDFs.
    """
    config = config or PreAnalyzerConfig.default()
    notes: list[str] = []

    text_char_count = len(page.raw_text.strip())
    image_area_ratio = _compute_image_area_ratio(page)
    cid_ratio = _compute_cid_suspect_ratio(page.font_spans, config)
    is_multi_column, column_count = _detect_multi_column(page.font_spans, page.width, config)
    has_table_candidate = page.line_rect_count >= config.table_min_line_rect_count

    is_blank = text_char_count <= config.blank_page_max_chars and not page.images
    if is_blank:
        notes.append(
            f"Blank page: {text_char_count} chars, no images "
            f"(threshold <= {config.blank_page_max_chars} chars)"
        )

    is_scanned_candidate = (
        not is_blank
        and text_char_count <= config.scanned_max_text_chars
        and image_area_ratio >= config.scanned_min_image_area_ratio
    )
    if is_scanned_candidate:
        notes.append(
            f"Scan candidate: {text_char_count} text chars (<= {config.scanned_max_text_chars}), "
            f"image area ratio {image_area_ratio:.2f} (>= {config.scanned_min_image_area_ratio})"
        )

    has_broken_font_suspect = cid_ratio >= config.cid_suspect_span_ratio_threshold
    if has_broken_font_suspect:
        notes.append(
            f"Broken font mapping suspected: {cid_ratio:.1%} of font spans flagged "
            f"cid-suspect (threshold {config.cid_suspect_span_ratio_threshold:.1%})"
        )

    if is_multi_column:
        notes.append(f"Multi-column layout candidate: estimated {column_count} columns")

    if has_table_candidate:
        notes.append(
            f"Table candidate: {page.line_rect_count} vector line/rect objects "
            f"(threshold >= {config.table_min_line_rect_count})"
        )

    if page.extraction_error:
        notes.append(f"NOTE: page-level extraction error from Module 1: {page.extraction_error}")

    return PageAnalysis(
        page_number=page.page_number,
        text_char_count=text_char_count,
        image_area_ratio=image_area_ratio,
        cid_suspect_span_ratio=cid_ratio,
        line_rect_count=page.line_rect_count,
        is_blank=is_blank,
        is_scanned_candidate=is_scanned_candidate,
        has_broken_font_suspect=has_broken_font_suspect,
        is_multi_column_candidate=is_multi_column,
        column_count_estimate=column_count,
        has_table_candidate=has_table_candidate,
        notes=notes,
    )


def analyze_document(
    pages: list[RawPage], config: PreAnalyzerConfig | None = None
) -> list[PageAnalysis]:
    """Analyze every page of a document. Thin wrapper for convenience -
    kept separate from analyze_page so callers needing single-page
    analysis (e.g. calibration tooling) don't need a whole RawDocument."""
    config = config or PreAnalyzerConfig.default()
    return [analyze_page(p, config) for p in pages]


# --------------------------------------------------------------------------
# Future calibration workflow - extension point, not implemented yet
# --------------------------------------------------------------------------

def calibrate_from_labeled_samples(*args, **kwargs):
    """
    Placeholder for the future calibration workflow.

    Intended shape once real documents are available: accept a set of
    (RawPage, ground_truth_label) pairs - e.g. "this page is truly
    scanned", "this page truly has a broken font mapping" - sweep
    candidate threshold values from PreAnalyzerConfig against them, and
    return a PreAnalyzerConfig that maximizes classification accuracy
    (or minimizes false negatives specifically, since under-flagging a
    scanned/broken-font page is more costly downstream than over-flagging
    one).

    Deliberately NOT implemented now: designing a threshold-search
    procedure without real labeled data to validate it against would be
    guessing at a second layer of guesses. This function exists so the
    calibration phase has an agreed home in the codebase rather than
    being bolted on ad hoc later.
    """
    raise NotImplementedError(
        "Calibration workflow will be implemented once representative "
        "labeled documents are available, per the agreed plan."
    )
