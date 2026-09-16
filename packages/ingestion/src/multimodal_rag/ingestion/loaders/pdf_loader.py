"""
PDF Loader Module
==================

Stage 1 of the ingestion pipeline: loads a PDF file, validates it, and
extracts raw per-page data needed by downstream stages (Page Pre-Analyzer,
Layout Segmenter).

Design contract:
- This module NEVER interprets content (no layout decisions, no cleaning,
  no chunking). It only opens the file safely and exposes raw PyMuPDF
  data in a structured, typed form.
- Every failure mode (corrupt file, encrypted file, empty file, oversized
  file, absurd page count) is raised as an explicit, typed exception -
  never silently swallowed - so the orchestrator can fail fast with a
  clear message before any downstream (expensive) stage runs.
- A failure on a SINGLE page does not abort the whole document: it is
  logged and the page is returned empty, so downstream validation can
  flag just that page's chunks as failed instead of losing an entire
  30-100 page upload over one bad page.
"""

from __future__ import annotations

import logging
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Loader-specific policy constants. These are sanity ceilings, not tuned
# thresholds (unlike the Pre-Analyzer's classification thresholds, which
# per the frozen design require calibration against real documents before
# being trusted). Override via load_pdf() args if a caller needs different
# limits.
# --------------------------------------------------------------------------

# Hard product limit: the ingestion pipeline accepts PDFs up to and
# including 50 pages. Larger files are rejected before layout analysis,
# chunking, or embedding begins.
MAX_PAGE_COUNT = 50
MIN_PAGE_COUNT = 1
MAX_FILE_SIZE_MB = 200


class PDFLoadError(Exception):
    """Base exception for all PDF loading failures."""


class PDFEncryptedError(PDFLoadError):
    """Raised when the PDF is password-protected and cannot be opened."""


class PDFCorruptError(PDFLoadError):
    """Raised when the PDF cannot be parsed at all (corrupt / not a PDF)."""


class PDFTooLargeError(PDFLoadError):
    """Raised when the file exceeds the configured size ceiling."""


class PDFPageCountError(PDFLoadError):
    """Raised when page count is outside the sane range (0 pages, or an
    absurd count suggesting a corrupt or non-document file)."""


@dataclass
class ImageObject:
    """A single embedded image on a page, in raw (uninterpreted) form."""
    xref: int
    bbox: tuple[float, float, float, float]
    width: int
    height: int


@dataclass
class FontSpan:
    """A run of text sharing one font, used by the Pre-Analyzer for
    cid/broken-font-mapping detection. Not used for content extraction -
    Docling owns final text extraction per the locked design."""
    text: str
    font_name: str
    bbox: tuple[float, float, float, float]
    is_cid_suspect: bool


@dataclass
class RawPage:
    """Raw, uninterpreted data extracted from a single PDF page."""
    page_number: int  # 1-indexed, matches human page numbering
    width: float
    height: float
    raw_text: str  # fitz's default get_text(), unprocessed
    font_spans: list[FontSpan] = field(default_factory=list)
    images: list[ImageObject] = field(default_factory=list)
    line_rect_count: int = 0  # vector line/rect drawing count (table hint)
    extraction_error: str | None = None  # set if this page failed to extract


@dataclass
class RawDocument:
    """Raw, uninterpreted data extracted from an entire PDF document."""
    source_path: Path
    page_count: int
    pages: list[RawPage] = field(default_factory=list)


def normalized_text_hash(texts: list[str]) -> str | None:
    """Hash meaningful PDF text after removing layout-only whitespace."""
    normalized = " ".join(" ".join((text or "").split()) for text in texts).strip()
    if len(normalized) < 32:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def normalized_pdf_text_hash(path: str | Path) -> str | None:
    """Return a lightweight content fingerprint without layout/chunk analysis.

    The fingerprint intentionally ignores PDF metadata and whitespace. It is
    used only for duplicate preflight; image-only PDFs return ``None`` and
    continue through the normal loader/validation path.
    """
    try:
        with fitz.open(str(path)) as document:
            texts = [page.get_text("text") for page in document]
    except Exception:
        return None
    return normalized_text_hash(texts)


_GLYPH_ID_TOKEN_RE = re.compile(r"/(?:gid|glyph|g)\d{2,}", re.IGNORECASE)
# Some subset/obfuscated fonts expose raw PDF glyph names (e.g.
# "/gid00037") instead of "(cid:NNN)" when their ToUnicode CMap is
# missing or broken - a DIFFERENT broken-font representation than the
# "(cid:NNN)" pattern below, confirmed missing from this function
# entirely until a real document surfaced it. A single match is treated
# as sufficient (unlike the character-ratio check below) because normal
# English text never legitimately contains a literal "/gidNNNNN"-style
# substring - this has effectively zero false-positive risk.


def _detect_cid_suspect(text: str) -> bool:
    """
    Heuristic cid / broken-font-mapping detection.

    PyMuPDF renders genuinely unmapped glyphs either as the literal
    string "(cid:NNN)", as raw glyph-name tokens like "/gid00037" (a
    different representation used by some subset fonts), or - more
    commonly with certain embedded subset fonts that have a garbled
    ToUnicode CMap - as the Unicode replacement character or private-use-
    area codepoints. All three patterns are checked; relying on only one
    misses real documents (confirmed: a document surfaced the "/gid"
    pattern, which this function did not check for until now).
    """
    if "(cid:" in text:
        return True
    if _GLYPH_ID_TOKEN_RE.search(text):
        return True
    if not text:
        return False
    suspect_chars = sum(
        1 for ch in text
        if ch == "\ufffd" or (0xE000 <= ord(ch) <= 0xF8FF)
    )
    return (suspect_chars / len(text)) > 0.05


def _extract_page(doc: fitz.Document, page_index: int) -> RawPage:
    page = doc[page_index]
    raw_text = page.get_text("text")

    font_spans: list[FontSpan] = []
    text_dict = page.get_text("dict")
    for block in text_dict.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                span_text = span.get("text", "")
                if not span_text.strip():
                    continue
                font_spans.append(
                    FontSpan(
                        text=span_text,
                        font_name=span.get("font", "unknown"),
                        bbox=tuple(span.get("bbox", (0, 0, 0, 0))),
                        is_cid_suspect=_detect_cid_suspect(span_text),
                    )
                )

    images: list[ImageObject] = []
    for img in page.get_images(full=True):
        xref = img[0]
        try:
            rects = page.get_image_rects(xref)
        except Exception:
            rects = []
        bbox = tuple(rects[0]) if rects else (0.0, 0.0, 0.0, 0.0)
        try:
            base_image = doc.extract_image(xref)
            img_w, img_h = base_image.get("width", 0), base_image.get("height", 0)
        except Exception as e:
            logger.warning(
                "Could not extract image data for xref %d on page %d: %s",
                xref, page_index + 1, e,
            )
            img_w, img_h = 0, 0
        images.append(ImageObject(xref=xref, bbox=bbox, width=img_w, height=img_h))

    # Vector line/rect count is a cheap table-candidate signal consumed by
    # the Pre-Analyzer stage; this module only counts, it never interprets.
    try:
        line_rect_count = len(page.get_drawings())
    except Exception:
        line_rect_count = 0

    return RawPage(
        page_number=page_index + 1,
        width=page.rect.width,
        height=page.rect.height,
        raw_text=raw_text,
        font_spans=font_spans,
        images=images,
        line_rect_count=line_rect_count,
    )


def load_pdf(path: str | Path, max_file_size_mb: int = MAX_FILE_SIZE_MB) -> RawDocument:
    """
    Load and validate a PDF file, returning raw per-page data.

    Raises:
        FileNotFoundError: path does not exist.
        PDFTooLargeError: file exceeds max_file_size_mb.
        PDFEncryptedError: file is password-protected and cannot be unlocked.
        PDFCorruptError: file cannot be parsed as a PDF at all.
        PDFPageCountError: page count is 0, or exceeds MAX_PAGE_COUNT.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")

    size_mb = path.stat().st_size / (1024 * 1024)
    if size_mb > max_file_size_mb:
        raise PDFTooLargeError(
            f"'{path.name}' is {size_mb:.1f}MB, exceeds limit of {max_file_size_mb}MB"
        )
    if size_mb == 0:
        raise PDFCorruptError(f"'{path.name}' is an empty file (0 bytes)")

    try:
        doc = fitz.open(str(path))
    except fitz.FileDataError as e:
        raise PDFCorruptError(f"'{path.name}' could not be parsed as a PDF: {e}") from e
    except Exception as e:
        # PyMuPDF can raise several underlying exception types for bad
        # input (RuntimeError, ValueError, etc.) depending on how the
        # file is malformed - all are treated as corruption, not
        # allowed to propagate as unhandled errors.
        raise PDFCorruptError(f"'{path.name}' failed to open: {e}") from e

    try:
        if doc.is_encrypted:
            # Some PDFs are "restricted" (permissions-locked) rather than
            # truly password-protected and open with an empty password;
            # only treat it as a hard failure if that also fails.
            if not doc.authenticate(""):
                raise PDFEncryptedError(
                    f"'{path.name}' is password-protected and cannot be opened"
                )

        try:
            page_count = doc.page_count
        except Exception as e:
            raise PDFCorruptError(
                f"'{path.name}' opened but page table could not be read: {e}"
            ) from e

        if page_count < MIN_PAGE_COUNT:
            raise PDFPageCountError(f"'{path.name}' has no pages")
        if page_count > MAX_PAGE_COUNT:
            raise PDFPageCountError(
                f"'{path.name}' has {page_count} pages, exceeds sanity ceiling of "
                f"{MAX_PAGE_COUNT} pages"
            )

        logger.info("Loading '%s': %d pages, %.1fMB", path.name, page_count, size_mb)

        pages: list[RawPage] = []
        for i in range(page_count):
            try:
                pages.append(_extract_page(doc, i))
            except Exception as e:
                # A single bad page must never abort the whole document.
                logger.error("Page %d of '%s' failed to extract: %s", i + 1, path.name, e)
                pages.append(
                    RawPage(
                        page_number=i + 1,
                        width=0.0,
                        height=0.0,
                        raw_text="",
                        extraction_error=str(e),
                    )
                )

        return RawDocument(source_path=path, page_count=page_count, pages=pages)
    finally:
        doc.close()
