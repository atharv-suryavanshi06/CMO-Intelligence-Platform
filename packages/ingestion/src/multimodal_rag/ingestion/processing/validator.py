"""
Validator Module
===================

Ties together every extraction path built so far (Docling native text/
tables from Module 3, OCR from Module 4, Vision description from Module
5, cleaning from Module 6) into the fallback chain the requirements
mandate: never trust a single extractor, always validate, always retry
via a fallback before giving up, and never silently drop content.

Per the locked requirements (S5): the intended shape is

    Native Extraction -> Validate -> pass? -> yes: done
                                            -> no: OCR -> Validate -> pass?
                                                                    -> yes: done
                                                                    -> no: flag failed / vision

This module implements exactly that, per region_type:

- text-like regions (text, heading, list_item, caption, header, footer,
  footnote, formula, code): Docling's native text -> validate -> if it
  fails, crop the region from the source page and OCR it -> validate
  again -> if that also fails, emit a `failed` result rather than
  either silently dropping the content or pretending garbage text is fine.
- table regions: Docling's structured table extraction -> validate
  (non-empty grid AND readable cell text, not glyph garbage) -> if
  either check fails, fall back to OCR-ing the table's bounding box
  (recovers SOME text even if row/column structure is lost) -> if OCR
  is ALSO insufficient, escalate to Gemini Vision by reusing Module 5's
  existing process_figure_region()/describe_diagram_image() pipeline
  UNCHANGED, treating the table's rendered image the same way a figure's
  would be treated. This exists because some visually-rendered "tables"
  (e.g. an infographic-style page Docling classifies as a TABLE region
  despite it really being a designed graphic) have a broken font mapping
  that produces successfully-PARSED grid structure with unreadable
  garbage cell text like "/gid00037/gid00064" - structure succeeding is
  not the same as content being readable, and this case was previously
  invisible to validation entirely. Deliberately NOT a second layout/
  table library (Camelot etc. were rejected in the locked architecture)
  for the OCR tier, and deliberately NOT a reimplementation of vision
  logic for the Vision tier - both tiers reuse existing extractors as-is.
- figure regions: validated from the FigureDescriptionResult already
  produced by Module 5 (which has its own internal decorative/text-image/
  diagram/vision-unavailable decision tree) - this module maps that
  result onto a validation_status, it doesn't recompute it.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image

from multimodal_rag.ingestion.analysis.layout_segmenter import Region, TableData
from multimodal_rag.ingestion.extractors.ocr_extractor import OCRConfig, OCRExtractionError, run_ocr
from multimodal_rag.ingestion.extractors.vision_describer import (
    DecorativeImageRegistry,
    FigureDescriptionResult,
    VisionDescriberConfig,
    process_figure_region,
)
from multimodal_rag.ingestion.processing.cleaner import CleaningConfig, clean_text

logger = logging.getLogger(__name__)

TEXT_LIKE_REGION_TYPES = frozenset({
    "text", "heading", "list_item", "caption", "header", "footer",
    "footnote", "formula", "code",
})


class RegionRenderError(Exception):
    """Raised when a region's source page/bbox cannot be rendered to an
    image for OCR fallback (e.g. the source PDF is no longer available,
    or the bbox is degenerate)."""


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

@dataclass
class ValidationConfig:
    ocr_low_confidence_threshold: float = 0.6
    # OCR mean confidence at/above this -> "ok"; below it but at/above
    # the failed threshold -> "low_confidence".
    ocr_failed_confidence_threshold: float = 0.3
    # OCR mean confidence below this (or empty result) -> "failed".
    min_meaningful_char_ratio: float = 0.4
    # Fraction of a cleaned text's characters that must be alphanumeric,
    # whitespace, or common punctuation for it to be considered real
    # content rather than extraction garbage (mojibake remnants, stray
    # symbols from a broken font, etc).
    render_dpi: int = 200
    # Resolution used when rendering a page region to an image for OCR
    # fallback - high enough for reasonable OCR accuracy, low enough to
    # stay fast for an interactive single-document upload.


@dataclass
class ValidatedRegionResult:
    region_id: str
    page_number: int
    region_type: str
    final_text: str | None
    table_data: TableData | None
    validation_status: str  # "ok" | "low_confidence" | "failed"
    failure_reason: str | None
    extraction_method_used: str
    attempted_methods: list[str] = field(default_factory=list)
    confidence: float | None = None
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------

_MEANINGFUL_CHAR_RE = re.compile(r"[A-Za-z0-9\s.,;:!?'\"()\-]")

_BROKEN_GLYPH_TOKEN_RE = re.compile(r"\(cid:\d+\)|/(?:gid|glyph|g)\d{2,}", re.IGNORECASE)
# Kept in sync with pdf_loader.py's _GLYPH_ID_TOKEN_RE by intent, not by
# import: this check runs on DOCLING's actual extracted text (what
# becomes the chunk), while pdf_loader.py's version runs on PyMuPDF's
# independent font spans (a page-level pre-filter signal, computed by a
# DIFFERENT extraction backend). The two backends can disagree on the
# same broken font, so this check exists here too, on the real content,
# rather than trusting only the upstream PyMuPDF-based page flag. This
# is also why it's a separate check from _meaningful_char_ratio below:
# glyph-ID tokens like "/gid00037" are made of letters and digits, so
# they PASS the character-class ratio check while being complete
# garbage content-wise - confirmed by testing, not assumed.


def _meaningful_char_ratio(text: str) -> float:
    if not text:
        return 0.0
    meaningful = sum(1 for ch in text if _MEANINGFUL_CHAR_RE.match(ch))
    return meaningful / len(text)


def _is_text_acceptable(text: str | None, config: ValidationConfig) -> bool:
    if not text or not text.strip():
        return False
    if _BROKEN_GLYPH_TOKEN_RE.search(text):
        return False
    return _meaningful_char_ratio(text) >= config.min_meaningful_char_ratio


def render_region_image(
    pdf_path: str | Path, page_number: int, bbox: tuple[float, float, float, float],
    coord_origin: str = "TOPLEFT", dpi: int = 200,
) -> Image.Image:
    """
    Render a cropped image of a region's bounding box from the source
    PDF, for OCR fallback. Handles the TOPLEFT/BOTTOMLEFT coordinate
    origin distinction explicitly rather than assuming one - Docling
    defaults to TOPLEFT for its unified document model (verified via
    introspection during Module 3 development), but this is written to
    handle BOTTOMLEFT too since that's PDF's native convention and could
    appear depending on Docling configuration/version.
    """
    try:
        doc = fitz.open(str(pdf_path))
    except Exception as e:
        raise RegionRenderError(f"Could not open '{pdf_path}' to render region image: {e}") from e

    try:
        if page_number < 1 or page_number > doc.page_count:
            raise RegionRenderError(
                f"Page {page_number} out of range for '{pdf_path}' ({doc.page_count} pages)"
            )
        page = doc[page_number - 1]
        l, t, r, b = bbox

        if coord_origin == "BOTTOMLEFT":
            page_height = page.rect.height
            # BOTTOMLEFT: y=0 is the bottom of the page: convert to the
            # top-left, y-down convention PyMuPDF's Rect expects.
            top = page_height - max(t, b)
            bottom = page_height - min(t, b)
            l, t, r, b = l, top, r, bottom
        else:
            # TOPLEFT: still guard against t/b being given in swapped
            # order rather than assuming the caller got it right.
            t, b = min(t, b), max(t, b)

        rect = fitz.Rect(l, t, r, b)
        if rect.is_empty or rect.width <= 0 or rect.height <= 0:
            raise RegionRenderError(f"Degenerate bbox for region on page {page_number}: {bbox}")

        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        pixmap = page.get_pixmap(matrix=matrix, clip=rect)
        return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    finally:
        doc.close()



# --------------------------------------------------------------------------
# Text-like region validation
# --------------------------------------------------------------------------

def validate_text_like_region(
    region: Region,
    pdf_path: str | Path,
    native_text_is_trusted: bool = True,
    config: ValidationConfig | None = None,
    ocr_config: OCRConfig | None = None,
    cleaning_config: CleaningConfig | None = None,
    use_gemini: bool = False,
    vision_config: VisionDescriberConfig | None = None,
    decorative_registry: DecorativeImageRegistry | None = None,
) -> ValidatedRegionResult:
    """
    Validate a text/heading/list/caption/header/footer/footnote/formula/
    code region, falling back from Docling's native text to OCR when
    needed.

    `native_text_is_trusted` should be False when the Page Pre-Analyzer
    flagged this page as having a broken font mapping or being a scan
    candidate (Module 2's `has_broken_font_suspect` / `is_scanned_candidate`)
    - in that case native text is skipped entirely rather than validated
    and rejected, since we already have independent evidence it's untrustworthy.

    `use_gemini` is NOT decided here - it is RoutingPolicy's
    PageRoutingDecision.use_gemini, forwarded unchanged by the caller
    (the orchestrator). This function still owns every quality judgment
    (is native text acceptable, is OCR confident enough); `use_gemini`
    only controls whether Vision is additionally consulted - it never
    overrides those quality checks, and it never gets computed here.
    """
    config = config or ValidationConfig()
    attempted: list[str] = []
    notes: list[str] = []

    if region.is_handwritten:
        notes.append("Region flagged as handwritten text by the Layout Segmenter")

    if native_text_is_trusted and not region.is_handwritten:
        attempted.append("native_text")
        cleaned = clean_text(region.text_content or "", cleaning_config)
        if _is_text_acceptable(cleaned.text, config) and not use_gemini:
            return ValidatedRegionResult(
                region_id=region.region_id, page_number=region.page_number,
                region_type=region.region_type, final_text=cleaned.text, table_data=None,
                validation_status="ok", failure_reason=None,
                extraction_method_used="native_text", attempted_methods=attempted,
                confidence=None, notes=notes + cleaned.applied_fixes,
            )
        if _is_text_acceptable(cleaned.text, config) and use_gemini:
            notes.append(
                "Native text passed validation, but routing policy flagged this page as "
                "infographic-like - continuing to OCR + Gemini Vision for structure recovery"
            )
            notes.extend(cleaned.applied_fixes)
        elif not cleaned.text.strip():
            notes.append("Native text failed validation: empty after cleaning")
        elif _BROKEN_GLYPH_TOKEN_RE.search(cleaned.text):
            notes.append(
                "Native text failed validation: contains broken glyph-ID tokens "
                "(e.g. /gidNNNNN or (cid:N)) - Docling's font mapping failed for this "
                "region even though the page-level pre-analyzer signal did not flag it"
            )
        else:
            notes.append("Native text failed validation: below meaningful-character ratio")
    else:
        notes.append("Native text skipped (page flagged untrustworthy or region is handwritten)")

    # --- OCR fallback ---
    attempted.append("ocr_fallback")
    try:
        image = render_region_image(
            pdf_path, region.page_number, region.bbox, region.coord_origin, config.render_dpi
        )
    except RegionRenderError as e:
        logger.error("Could not render region %s for OCR fallback: %s", region.region_id, e)
        return ValidatedRegionResult(
            region_id=region.region_id, page_number=region.page_number,
            region_type=region.region_type, final_text=None, table_data=None,
            validation_status="failed", failure_reason=f"region_render_failed: {e}",
            extraction_method_used="none", attempted_methods=attempted,
            confidence=None, notes=notes,
        )

    try:
        ocr_result = run_ocr(image, ocr_config)
    except OCRExtractionError as e:
        return ValidatedRegionResult(
            region_id=region.region_id, page_number=region.page_number,
            region_type=region.region_type, final_text=None, table_data=None,
            validation_status="failed", failure_reason=f"ocr_engine_failed: {e}",
            extraction_method_used="none", attempted_methods=attempted,
            confidence=None, notes=notes,
        )

    cleaned_ocr = clean_text(ocr_result.text, cleaning_config)
    notes.extend(cleaned_ocr.applied_fixes)

    if region.page_number in [21, 28, 32, 34]:
        print("\n" + "=" * 80)
        print(f"PAGE: {region.page_number}")
        print(f"REGION: {region.region_id}")
        print(f"TYPE: {region.region_type}")

        print("\n----- Native Text -----")
        print(region.text_content)

        print("\n----- OCR Text -----")
        print(cleaned_ocr.text)

        print(f"\nOCR Confidence: {ocr_result.mean_confidence:.3f}")

        print("\nText Acceptable:", _is_text_acceptable(cleaned_ocr.text, config))
        print("=" * 80 + "\n")

    if ocr_result.mean_confidence >= config.ocr_low_confidence_threshold and _is_text_acceptable(cleaned_ocr.text, config):
        status = "ok"
        failure_reason = None
    elif ocr_result.mean_confidence >= config.ocr_failed_confidence_threshold and cleaned_ocr.text.strip():
        status = "low_confidence"
        failure_reason = None
    else:
        status = "failed"
        failure_reason = (
            "possible_handwriting_or_illegible_scan" if region.is_handwritten
            else "native_text_and_ocr_both_failed"
        )

    ocr_based_result = ValidatedRegionResult(
        region_id=region.region_id, page_number=region.page_number,
        region_type=region.region_type,
        final_text=cleaned_ocr.text if cleaned_ocr.text.strip() else None,
        table_data=None, validation_status=status, failure_reason=failure_reason,
        extraction_method_used="ocr_fallback", attempted_methods=attempted,
        confidence=ocr_result.mean_confidence, notes=notes,
    )

    # --- Vision escalation. Triggered by EITHER a genuine quality
    # failure (existing behavior, unchanged) OR use_gemini (an explicit,
    # externally-supplied routing decision this function does not
    # compute - see docstring). Reuses the SAME rendered image already
    # produced above for OCR - no second crop - same pattern
    # validate_table_region already uses for its own Vision escalation. ---
    should_escalate_to_vision = vision_config is not None and (use_gemini or status == "failed")
    if not should_escalate_to_vision:
        return ocr_based_result

    notes.append(
        "Escalating to Gemini Vision: " + (
            "routing policy flagged this page as infographic-like"
            if use_gemini else "native text and OCR both failed"
        )
    )
    attempted.append("vision_fallback")
    registry = decorative_registry if decorative_registry is not None else DecorativeImageRegistry(vision_config)
    figure_result = process_figure_region(region.region_id, image, registry, vision_config, ocr_config)
    vision_validated = validate_figure_region(figure_result)
    vision_validated.page_number = region.page_number
    vision_validated.region_type = region.region_type
    vision_validated.extraction_method_used = f"text_vision_escalation:{figure_result.extraction_method}"
    vision_validated.attempted_methods = attempted + [figure_result.extraction_method]
    # OCR text is never discarded, even when Vision's description becomes
    # final_text - kept as grounding context in notes, per this module's
    # "never silently drop content" principle (see module docstring).
    vision_validated.notes = notes + figure_result.notes + [f"OCR-recovered text (grounding): {cleaned_ocr.text}"]
    return vision_validated


# --------------------------------------------------------------------------
# Table region validation
# --------------------------------------------------------------------------

def _is_table_text_garbage(table: TableData, config: ValidationConfig) -> bool:
    """
    True if a table's cell text is unreadable garbage even though its
    GRID STRUCTURE parsed successfully (non-empty rows/cols). This is a
    genuinely different failure mode from an empty grid: Docling's
    table-structure model can succeed at finding rows and columns while
    the underlying font's ToUnicode mapping is broken, producing cells
    full of literal "/gid00037"-style tokens or near-unreadable
    character noise instead of real text - structural success does not
    imply content is readable. Reuses the SAME glyph-token pattern and
    meaningful-character-ratio check already used elsewhere in this
    module for text regions, applied here to a table's flattened cell
    text instead of a text region's string.
    """
    all_text = " ".join(cell for row in table.rows for cell in row)
    if not all_text.strip():
        return True  # non-empty grid shape, but every cell is blank - not usable content either
    if _BROKEN_GLYPH_TOKEN_RE.search(all_text):
        return True
    return _meaningful_char_ratio(all_text) < config.min_meaningful_char_ratio


def validate_table_region(
    region: Region,
    pdf_path: str | Path,
    config: ValidationConfig | None = None,
    ocr_config: OCRConfig | None = None,
    cleaning_config: CleaningConfig | None = None,
    vision_config: VisionDescriberConfig | None = None,
    decorative_registry: DecorativeImageRegistry | None = None,
    use_gemini: bool = False,
) -> ValidatedRegionResult:
    """
    Validate a table region with a three-tier fallback chain:

    1. Docling's structured table extraction, accepted only if the grid
       is non-empty AND its cell text is actually readable (see
       _is_table_text_garbage) - a table that parses structurally but
       contains glyph garbage is treated the same as an empty table.
    2. OCR on the table's rendered bbox (loses row/column structure,
       recovers plain text) - unchanged from before.
    3. NEW: if OCR is also insufficient (empty, low-confidence, or
       itself full of glyph garbage), escalate to Gemini Vision by
       reusing Module 5's process_figure_region()/validate_figure_region()
       UNCHANGED - the table's own rendered image is passed through
       exactly as a figure's image would be. Only reachable when the
       caller opts in by providing `vision_config`; omitting it preserves
       the exact prior two-tier behavior (backward compatible - existing
       callers that don't pass vision_config see no change at all).

    `decorative_registry`, if provided, lets the caller share ONE
    DecorativeImageRegistry across both real figures and vision-escalated
    tables (see orchestrator.py) rather than spinning up a fresh one per
    table - decorative-image detection logic itself is entirely Module
    5's unmodified code either way.

    `use_gemini` is NOT decided here - it is RoutingPolicy's
    PageRoutingDecision.use_gemini, forwarded unchanged by the caller.
    This function still owns every quality judgment (is the structured
    table readable, is OCR confident enough); `use_gemini` only adds an
    additional trigger for Vision escalation on top of those existing
    quality checks, it never overrides them.
    """
    config = config or ValidationConfig()
    attempted = ["docling_table_structured"]
    notes: list[str] = []

    table = region.table_data
    structurally_present = table is not None and table.num_rows > 0 and table.num_cols > 0 and any(
        cell.strip() for row in table.rows for cell in row
    )
    is_garbage = structurally_present and _is_table_text_garbage(table, config)
    if is_garbage:
        notes.append(
            "Structured table extraction produced a non-empty grid, but cell text is "
            "unreadable (broken glyph mapping / garbage characters) - treating as failed"
        )

    if structurally_present and not is_garbage and not use_gemini:
        return ValidatedRegionResult(
            region_id=region.region_id, page_number=region.page_number,
            region_type="table", final_text=table.markdown, table_data=table,
            validation_status="ok", failure_reason=None,
            extraction_method_used="docling_table_structured", attempted_methods=attempted,
            confidence=None, notes=notes,
        )

    if structurally_present and not is_garbage and use_gemini:
        notes.append(
            "Structured table extraction succeeded, but routing policy flagged this page "
            "as infographic-like - continuing to Gemini Vision for structural context"
        )

    if not (structurally_present and not is_garbage):
        notes.append("Structured table extraction was empty, failed, or unreadable - falling back to OCR on table bbox")
    attempted.append("ocr_fallback")
    try:
        image = render_region_image(
            pdf_path, region.page_number, region.bbox, region.coord_origin, config.render_dpi
        )
        ocr_result = run_ocr(image, ocr_config)
    except (RegionRenderError, OCRExtractionError) as e:
        return ValidatedRegionResult(
            region_id=region.region_id, page_number=region.page_number,
            region_type="table", final_text=None, table_data=None,
            validation_status="failed",
            failure_reason=f"table_structure_and_ocr_fallback_both_failed: {e}",
            extraction_method_used="none", attempted_methods=attempted,
            confidence=None, notes=notes,
        )

    cleaned = clean_text(ocr_result.text, cleaning_config)
    ocr_is_sufficient = (
        bool(cleaned.text.strip())
        and ocr_result.mean_confidence >= config.ocr_low_confidence_threshold
        and not _BROKEN_GLYPH_TOKEN_RE.search(cleaned.text)
    )

    if ocr_is_sufficient and not use_gemini:
        notes.append("Recovered table text via OCR, but row/column structure is lost - flagged low_confidence, not ok")
        return ValidatedRegionResult(
            region_id=region.region_id, page_number=region.page_number,
            region_type="table", final_text=cleaned.text, table_data=None,
            validation_status="low_confidence", failure_reason=None,
            extraction_method_used="ocr_fallback", attempted_methods=attempted,
            confidence=ocr_result.mean_confidence, notes=notes + cleaned.applied_fixes,
        )

    # --- OCR insufficient: escalate to Gemini Vision via the EXISTING,
    # UNMODIFIED figure pipeline, reusing the SAME rendered image (no
    # second render, no duplicate crop). ---
    if vision_config is None:
        # Caller didn't opt into vision escalation - preserve the exact
        # prior behavior (two-tier: structured -> OCR only).
        status = "low_confidence" if cleaned.text.strip() else "failed"
        reason = None if cleaned.text.strip() else "table_structure_failed_and_ocr_found_no_text"
        return ValidatedRegionResult(
            region_id=region.region_id, page_number=region.page_number,
            region_type="table", final_text=cleaned.text if cleaned.text.strip() else None,
            table_data=None, validation_status=status, failure_reason=reason,
            extraction_method_used="ocr_fallback", attempted_methods=attempted,
            confidence=ocr_result.mean_confidence, notes=notes + cleaned.applied_fixes,
        )

    if ocr_is_sufficient and use_gemini:
        notes.append(
            "OCR fallback recovered readable text, but routing policy flagged this page as "
            "infographic-like - escalating to Gemini Vision via the figure pipeline anyway"
        )
    else:
        notes.append("OCR fallback also insufficient - escalating to Gemini Vision via the figure pipeline")
    attempted.append("vision_fallback")
    registry = decorative_registry if decorative_registry is not None else DecorativeImageRegistry(vision_config)
    # NOTE: process_figure_region() internally runs its own OCR pass as
    # part of its text-vs-diagram classification - this means the OCR
    # above and the one inside process_figure_region() are genuinely
    # redundant runtime work for this one region. Accepted deliberately:
    # avoiding it would require passing a pre-computed OCR result into
    # process_figure_region(), which means changing its signature - and
    # vision_describer.py is explicitly not to be modified. One extra
    # OCR call on an already-failed table region is a small, bounded
    # cost for reusing Module 5's logic completely unchanged.
    figure_result = process_figure_region(region.region_id, image, registry, vision_config, ocr_config)
    vision_validated = validate_figure_region(figure_result)
    vision_validated.page_number = region.page_number
    vision_validated.region_type = "table"
    vision_validated.extraction_method_used = f"table_vision_fallback:{figure_result.extraction_method}"
    vision_validated.attempted_methods = attempted + [figure_result.extraction_method]
    # OCR/structured-table text is never discarded, even when Vision's
    # description becomes final_text - kept as grounding context in
    # notes, per this module's "never silently drop content" principle.
    grounding_text = table.markdown if (structurally_present and not is_garbage) else cleaned.text
    vision_validated.notes = notes + figure_result.notes + [f"OCR/table-recovered text (grounding): {grounding_text}"]
    return vision_validated


# --------------------------------------------------------------------------
# Figure region validation (consumes Module 5's result, doesn't recompute)
# --------------------------------------------------------------------------

def validate_figure_region(figure_result: FigureDescriptionResult) -> ValidatedRegionResult:
    notes = list(figure_result.notes)

    if figure_result.classification == "decorative":
        return ValidatedRegionResult(
            region_id=figure_result.region_id, page_number=-1, region_type="figure",
            final_text=None, table_data=None, validation_status="ok", failure_reason=None,
            extraction_method_used=figure_result.extraction_method,
            attempted_methods=[figure_result.extraction_method],
            confidence=None, notes=notes,
        )

    if figure_result.classification in ("text_image", "diagram"):
        return ValidatedRegionResult(
            region_id=figure_result.region_id, page_number=-1, region_type="figure",
            final_text=figure_result.description_text, table_data=None,
            validation_status="ok" if figure_result.description_text else "failed",
            failure_reason=None if figure_result.description_text else "empty_description",
            extraction_method_used=figure_result.extraction_method,
            attempted_methods=[figure_result.extraction_method],
            confidence=None, notes=notes,
        )

    # classification == "diagram_undescribed": vision LLM unavailable/failed
    if figure_result.description_text:
        return ValidatedRegionResult(
            region_id=figure_result.region_id, page_number=-1, region_type="figure",
            final_text=figure_result.description_text, table_data=None,
            validation_status="low_confidence",
            failure_reason="vision_unavailable_used_ocr_fallback",
            extraction_method_used=figure_result.extraction_method,
            attempted_methods=[figure_result.extraction_method],
            confidence=None, notes=notes,
        )
    return ValidatedRegionResult(
        region_id=figure_result.region_id, page_number=-1, region_type="figure",
        final_text=None, table_data=None, validation_status="failed",
        failure_reason="vision_unavailable_no_ocr_fallback_text",
        extraction_method_used=figure_result.extraction_method,
        attempted_methods=[figure_result.extraction_method],
        confidence=None, notes=notes,
    )


# --------------------------------------------------------------------------
# Dispatcher
# --------------------------------------------------------------------------

def validate_region(
    region: Region,
    pdf_path: str | Path,
    native_text_is_trusted: bool = True,
    config: ValidationConfig | None = None,
    ocr_config: OCRConfig | None = None,
    cleaning_config: CleaningConfig | None = None,
    vision_config: VisionDescriberConfig | None = None,
    decorative_registry: DecorativeImageRegistry | None = None,
    use_gemini: bool = False,
) -> ValidatedRegionResult:
    """
    Route a region to the correct validator by region_type. Figure
    regions are NOT handled here - they require Module 5's
    FigureDescriptionResult (which needs document-wide decorative-image
    context), so they go through `validate_figure_region` directly,
    called by the orchestrator after Module 5 has run.

    `vision_config`/`decorative_registry`/`use_gemini` are now forwarded
    to BOTH the text-like and table branches (previously `vision_config`/
    `decorative_registry` were table-only, since text-like regions had
    no Vision escalation path at all). `use_gemini` is RoutingPolicy's
    PageRoutingDecision.use_gemini, forwarded unchanged - this dispatcher
    does not compute it, same as the two functions it calls.
    """
    if region.region_type in TEXT_LIKE_REGION_TYPES:
        return validate_text_like_region(
            region, pdf_path, native_text_is_trusted, config, ocr_config, cleaning_config,
            use_gemini, vision_config, decorative_registry,
        )
    if region.region_type == "table":
        return validate_table_region(
            region, pdf_path, config, ocr_config, cleaning_config, vision_config,
            decorative_registry, use_gemini,
        )
    if region.region_type == "figure":
        raise ValueError(
            "Figure regions must be validated via validate_figure_region() using "
            "Module 5's FigureDescriptionResult, not validate_region()."
        )
    # "unknown" or any future unmapped label - attempt as text-like rather
    # than dropping it, since some content is better than none.
    logger.warning(
        "Region %s has unrecognized region_type '%s' - validating as text-like",
        region.region_id, region.region_type,
    )
    return validate_text_like_region(
        region, pdf_path, native_text_is_trusted, config, ocr_config, cleaning_config,
        use_gemini, vision_config, decorative_registry,
    )
