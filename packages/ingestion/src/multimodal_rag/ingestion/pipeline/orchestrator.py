"""
Orchestrator Module
======================

Wires Modules 1-10 into a single callable pipeline:

    load_pdf (1) -> analyze_document (2) -> segment_document (3)
        -> write_raw_extraction (RAW SNAPSHOT - direct Docling output,
            zero cleaning/OCR/validation applied yet)
        -> per-region: validate_region / process_figure_region+validate_figure_region (4,5,6,7)
        -> chunk_document (8) -> write_document_output (9)

Design notes:

- `segment_fn` is an injectable dependency, defaulting to Module 3's real
  `segment_document`. This is NOT speculative over-engineering: Module
  3's actual Docling model inference cannot run in this sandbox
  (Hugging Face is not in the network allowlist - see
  layout_segmenter.py's module docstring), so this is the seam that lets
  every OTHER step of the orchestration (region routing, the fallback
  chain, figure two-pass handling, chunking, output writing) be
  genuinely tested end-to-end here using injected Region fixtures, while
  still defaulting to the real Docling pipeline in any environment where
  it can run. This is the same pattern already used for testability
  throughout the codebase (pure mapping functions, dependency-injected
  configs), applied at the top level.
- Figure regions require a two-pass approach: EVERY figure's image hash
  must be registered in a DecorativeImageRegistry before ANY figure is
  classified as decorative-or-not, because that's a whole-document
  question, not a per-region one (see vision_describer.py).
- A document-level failure (can't load the PDF at all, layout
  segmentation fails entirely) raises `IngestionError` - a clear, loud
  failure for the whole document. A REGION-level failure never raises;
  it's captured as `validation_status="failed"` and surfaces in the
  audit/validation report (Module 9) instead, per the "never silently
  drop content" principle carried through every module so far.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import fitz  # PyMuPDF - already a project dependency (see layout_segmenter.py,
# validator.py); needed here for the POC's whole-page render helper below,
# since render_region_image() only renders a bbox crop, not a full page.
from PIL import Image

from multimodal_rag.ingestion.analysis.layout_segmenter import (
    LayoutSegmentationError,
    LayoutSegmenterConfig,
    Region,
    segment_document,
)
from multimodal_rag.ingestion.analysis.page_preanalyzer import (
    PageAnalysis,
    PreAnalyzerConfig,
    analyze_document,
)

from multimodal_rag.ingestion.analysis.layout_analysis import (
    LayoutAnalysisConfig,
    build_layout_analysis,
)

from multimodal_rag.ingestion.routing.routing_policy import (
    RoutingPolicyConfig,
    decide_page_routing,
)

from multimodal_rag.ingestion.extractors.ocr_extractor import OCRConfig
from multimodal_rag.ingestion.extractors.vision_describer import (
    DecorativeImageRegistry,
    VisionAPIUnavailableError,
    VisionDescriberConfig,
    VisionDescriptionError,
    compute_average_hash,
    describe_diagram_image,
    process_figure_region,
)
from multimodal_rag.ingestion.loaders.pdf_loader import PDFLoadError, load_pdf, normalized_pdf_text_hash
from multimodal_rag.ingestion.output.human_readable_writer import write_human_readable_extraction
from multimodal_rag.ingestion.output.raw_writer import write_raw_extraction
from multimodal_rag.ingestion.output.deduplicator import (
    deduplicate_chunks,
    find_content_duplicate,
    find_file_duplicate,
    persist_deduplication,
    sha256_file,
)
from multimodal_rag.ingestion.output.writer import (
    DocumentOutputPaths,
    OutputWriterConfig,
    write_document_output,
)
from multimodal_rag.ingestion.processing.chunker import ChunkerConfig, chunk_document
from multimodal_rag.ingestion.processing.cleaner import CleaningConfig
from multimodal_rag.ingestion.processing.validator import (
    RegionRenderError,
    ValidatedRegionResult,
    ValidationConfig,
    render_region_image,
    validate_figure_region,
    validate_region,
)

logger = logging.getLogger(__name__)


class IngestionError(Exception):
    """Raised only for whole-document failures (can't load the PDF at
    all, layout segmentation fails entirely) - NOT for individual region
    failures, which are handled per-region via validation_status."""


class DuplicateDocumentError(IngestionError):
    """Raised before expensive extraction when a duplicate was already ingested."""


class IncompletePageCoverageError(IngestionError):
    """
    Raised when the layout segmenter's output does not cover every page
    of the source PDF. This is a real, observed failure mode: Docling's
    StandardPdfPipeline can hit a per-page preprocessing error (e.g.
    std::bad_alloc on a later page of a large document) internally,
    swallow it, and still report "Finished converting document" with a
    partial result - i.e. it does not always raise when it should.

    This check is deliberately independent of whatever segment_document()
    itself does or doesn't catch: it compares the COMPLETE set of pages
    Module 1 (PyMuPDF) says the PDF has against the set of pages actually
    represented in the regions Module 3 returned, using Module 2's
    already-computed `is_blank` signal to correctly exempt genuinely
    blank pages (which legitimately produce zero regions) from being
    flagged as missing. See `_validate_page_coverage` below.
    """


@dataclass
class OrchestratorConfig:
    preanalyzer: PreAnalyzerConfig = field(default_factory=PreAnalyzerConfig.default)
    layout: LayoutSegmenterConfig = field(default_factory=LayoutSegmenterConfig)
    ocr: OCRConfig = field(default_factory=OCRConfig)
    vision: VisionDescriberConfig = field(default_factory=VisionDescriberConfig)
    validation: ValidationConfig = field(default_factory=ValidationConfig)
    cleaning: CleaningConfig = field(default_factory=CleaningConfig)
    chunker: ChunkerConfig = field(default_factory=ChunkerConfig)
    output: OutputWriterConfig = field(default_factory=OutputWriterConfig)
    layout_analysis: LayoutAnalysisConfig = field(default_factory=LayoutAnalysisConfig)
    routing: RoutingPolicyConfig = field(default_factory=RoutingPolicyConfig)


def _validate_page_coverage(
    regions: list[Region],
    total_pages: int,
    page_analysis_by_number: dict[int, PageAnalysis],
    source_file: str,
) -> None:
    """
    Fail loudly if any non-blank page has zero regions - see
    IncompletePageCoverageError's docstring for why this check exists
    independent of segment_document()'s own error handling.

    A page is only accepted as legitimately having zero regions if
    Module 2's Pre-Analyzer independently flagged it `is_blank` (using
    PyMuPDF's raw text/image signals - a source of truth that doesn't
    depend on Docling having worked correctly at all). Any other page
    missing from `regions` is treated as a real extraction failure.
    """
    pages_represented = {r.page_number for r in regions}
    missing_pages = []
    for page_num in range(1, total_pages + 1):
        if page_num in pages_represented:
            continue
        page_analysis = page_analysis_by_number.get(page_num)
        if page_analysis is not None and page_analysis.is_blank:
            continue  # legitimately blank - zero regions is correct, not a failure
        missing_pages.append(page_num)

    if missing_pages:
        raise IncompletePageCoverageError(
            f"Layout segmentation for '{source_file}' only covered "
            f"{len(pages_represented)} of {total_pages} pages. Missing (non-blank) "
            f"pages: {missing_pages}. This usually means Docling's conversion hit an "
            f"internal error partway through (e.g. std::bad_alloc on a large document) "
            f"but still returned a partial result instead of raising - refusing to "
            f"produce final output from incomplete data. The raw extraction snapshot "
            f"for whatever WAS extracted has already been written to the 'raw/' "
            f"subfolder for debugging."
        )


def _is_native_text_trusted(page_analysis: PageAnalysis | None) -> bool:
    """
    Pure, separately-testable decision: is this page's native text layer
    trustworthy, per the Page Pre-Analyzer's signals? Broken-font pages
    and scan candidates both mean "don't trust the text layer even
    though Docling produced something" - handled identically here
    regardless of which signal fired.
    """
    if page_analysis is None:
        return True  # no signal available - default to trusting native text
    return not (page_analysis.has_broken_font_suspect or page_analysis.is_scanned_candidate)


def _build_figure_images(
    regions: list[Region], pdf_path: str | Path, config: OrchestratorConfig,
) -> dict[str, Image.Image]:
    """
    Build the image used for figure OCR/Vision analysis via a FRESH,
    high-DPI bbox crop of the source PDF (reusing Module 7's already-
    tested `render_region_image`), NOT Docling's own saved picture crop
    (`region.image_path`).

    ROOT CAUSE this fixes: Docling's saved crop is rendered at
    `images_scale` (default 1.0 - roughly native PDF-point resolution),
    which is too low-resolution for OCR to reliably read small
    diagram/infographic/flowchart text. `render_region_image` renders at
    `config.validation.render_dpi` (default 200), matching what's already
    used for text/table OCR fallback elsewhere in the pipeline.

    `region.image_path` (Docling's crop) is NOT touched by this change -
    it's still saved by Module 3 and still used separately for markdown
    embedding in the audit/human-readable outputs. This function only
    changes what image is fed INTO OCR/Vision analysis, not what's shown
    to a human reading the output.
    """
    images: dict[str, Image.Image] = {}
    for r in regions:
        if r.region_type != "figure":
            continue
        try:
            images[r.region_id] = render_region_image(
                pdf_path, r.page_number, r.bbox, r.coord_origin, config.validation.render_dpi,
            )
        except RegionRenderError as e:
            logger.warning(
                "Could not render high-DPI crop for figure region %s (falling back to "
                "Docling's saved crop if available): %s", r.region_id, e,
            )
            if r.image_path:
                try:
                    images[r.region_id] = Image.open(r.image_path)
                except Exception as e2:
                    logger.warning("Docling's saved crop for region %s also unusable: %s", r.region_id, e2)
    return images


def _validate_figure(
    region: Region,
    figure_images: dict[str, Image.Image],
    registry: DecorativeImageRegistry,
    config: OrchestratorConfig,
) -> ValidatedRegionResult:
    if region.region_id not in figure_images:
        return ValidatedRegionResult(
            region_id=region.region_id, page_number=region.page_number,
            region_type="figure", final_text=None, table_data=None,
            validation_status="failed", failure_reason="figure_image_unavailable",
            extraction_method_used="none", attempted_methods=[],
            confidence=None, notes=["Figure region had no usable saved image to process"],
        )
    figure_result = process_figure_region(
        region.region_id, figure_images[region.region_id], registry, config.vision, config.ocr,
    )
    validated = validate_figure_region(figure_result)
    validated.page_number = region.page_number  # validate_figure_region doesn't know the real page number
    return validated


# --------------------------------------------------------------------------
# POC: whole-page Gemini summary for hardcoded infographic-like pages
# --------------------------------------------------------------------------
#
# PROBLEM: Gemini is normally only ever invoked on individual REGION crops
# (via process_figure_region / validate_table_region's vision escalation).
# For infographic-style pages (comparison matrices, SmartArt, process
# diagrams) this means Gemini never sees the whole page at once, so it
# can't describe relationships between boxes, arrows, titles, legends,
# and tables that are spread across several regions.
#
# TEMPORARY POC FIX: for a hardcoded set of pages, render the ENTIRE page
# and describe it with ONE additional Gemini call, stored as its own
# chunk alongside (not instead of) the normal per-region OCR/validation
# output. To be replaced with routing-policy-driven logic later - see
# TODO at _PAGE_LEVEL_GEMINI_PAGES below.

_PAGE_LEVEL_GEMINI_PAGES: frozenset[int] = frozenset({21, 28, 32, 34})
# TODO(POC): hand-picked page numbers. Replace with a routing_decisions-
# driven signal (e.g. an explicit decision.use_page_level_gemini flag)
# once the routing policy exposes one - see module docstring / PR notes.


def _render_full_page_image(
    pdf_path: str | Path, page_number: int, dpi: int,
) -> Image.Image:
    """
    Render an ENTIRE PDF page (not a bbox crop) to an image, for the
    page-level Gemini call on `_PAGE_LEVEL_GEMINI_PAGES`.

    Mirrors validator.py's `render_region_image` zoom/matrix approach
    (same fitz technique, same DPI convention) but with no `clip` rect,
    since there is no existing whole-page render helper to reuse -
    `render_region_image` requires a region bbox, and adding a whole-
    page variant to validator.py was out of scope for this POC (that
    file is not to be modified).
    """
    try:
        doc = fitz.open(str(pdf_path))
    except Exception as e:
        raise RegionRenderError(
            f"Could not open '{pdf_path}' to render page {page_number}: {e}"
        ) from e
    try:
        if page_number < 1 or page_number > doc.page_count:
            raise RegionRenderError(
                f"Page {page_number} out of range for '{pdf_path}' ({doc.page_count} pages)"
            )
        page = doc[page_number - 1]
        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        pixmap = page.get_pixmap(matrix=matrix)
        return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    finally:
        doc.close()


def _build_page_level_gemini_pair(
    pdf_path: str | Path,
    page_number: int,
    document_id: str,
    config: OrchestratorConfig,
) -> tuple[Region, ValidatedRegionResult]:
    """
    POC HARDCODE (see `_PAGE_LEVEL_GEMINI_PAGES`): render the whole page
    and describe it via `describe_diagram_image` - the SAME raw Gemini
    call Module 5 already uses, reused as-is. Deliberately NOT
    `process_figure_region`: that function's decorative-image/OCR-vs-
    diagram classification tree is specific to individual figure
    regions and doesn't apply to a whole page.

    Returns a synthetic (Region, ValidatedRegionResult) pair with
    region_type="figure" so it flows through the EXISTING, UNMODIFIED
    chunker as its own dedicated chunk (see chunker.py's
    `_STRUCTURAL_REGION_TYPES` handling) rather than needing any new
    chunking logic. `reading_order=-1` so that once this pair is merged
    back into `ordered_pairs` and re-sorted, it lands before this page's
    real regions rather than disrupting their order.

    Never raises: a render or Gemini failure here becomes
    validation_status="failed" (picked up by chunker.py as
    `unrecoverable` and surfaced in the audit report), per this
    codebase's "region-level failures never raise" principle - a
    problem with this one POC addition must not abort ingestion of the
    rest of the document.
    """
    region_id = f"page_summary_{document_id}_p{page_number}"
    common_kwargs = dict(
        region_id=region_id,
        page_number=page_number,
        region_type="figure",
        extraction_method_used="page_level_vision_description",
        attempted_methods=["page_level_vision_description"],
        confidence=None,
    )

    try:
        page_image = _render_full_page_image(pdf_path, page_number, config.validation.render_dpi)
        print(f"[PAGE SUMMARY] Rendered page {page_number}")
    except RegionRenderError as e:
        print(f"[PAGE SUMMARY] FAILED: {e}")
        logger.warning("Could not render full page %d for page-level Gemini summary: %s", page_number, e)
        region = Region(
            region_id=region_id, page_number=page_number, region_type="figure",
            reading_order=-1, bbox=(0.0, 0.0, 0.0, 0.0), coord_origin="TOPLEFT",
            raw_label="page_level_gemini_summary_poc",
            notes=["POC: hardcoded page-level Gemini summary (see orchestrator._PAGE_LEVEL_GEMINI_PAGES)"],
        )
        validated = ValidatedRegionResult(
            final_text=None, table_data=None, validation_status="failed",
            failure_reason="page_render_failed",
            notes=[f"Could not render full page image: {e}"],
            **common_kwargs,
        )
        return region, validated

    region = Region(
        region_id=region_id, page_number=page_number, region_type="figure",
        reading_order=-1, bbox=(0.0, 0.0, float(page_image.width), float(page_image.height)),
        coord_origin="TOPLEFT", raw_label="page_level_gemini_summary_poc",
        notes=["POC: hardcoded page-level Gemini summary (see orchestrator._PAGE_LEVEL_GEMINI_PAGES)"],
    )

    try:
        description = describe_diagram_image(page_image, config.vision)
        print(f"[PAGE SUMMARY] Gemini success page {page_number}")
        print(description[:500])
        validated = ValidatedRegionResult(
            final_text=description, table_data=None, validation_status="ok",
            failure_reason=None,
            notes=["Whole-page Gemini Vision description generated for infographic-like page (POC hardcode)"],
            **common_kwargs,
        )
    except VisionAPIUnavailableError as e:
        print(f"[PAGE SUMMARY] FAILED: {e}")
        validated = ValidatedRegionResult(
            final_text=None, table_data=None, validation_status="failed",
            failure_reason="vision_unavailable",
            notes=[f"Vision LLM unavailable for page-level summary: {e}"],
            **common_kwargs,
        )
    except VisionDescriptionError as e:
        print(f"[PAGE SUMMARY] FAILED: {e}")
        logger.error("Page-level Gemini description failed for page %d: %s", page_number, e)
        validated = ValidatedRegionResult(
            final_text=None, table_data=None, validation_status="failed",
            failure_reason="vision_call_failed",
            notes=[f"Vision LLM call failed for page-level summary: {e}"],
            **common_kwargs,
        )
    return region, validated


def ingest_document(
    pdf_path: str | Path,
    output_dir: str | Path,
    config: OrchestratorConfig | None = None,
    segment_fn: Callable[[str, LayoutSegmenterConfig], list[Region]] | None = None,
    source_sha256: str | None = None,
    content_sha256: str | None = None,
    progress_callback: Callable[[int, int, int | None], None] | None = None,
) -> DocumentOutputPaths:
    """
    Run the complete ingestion pipeline on one PDF and write its output
    files. Raises IngestionError only for whole-document failures; every
    region-level problem is captured in the output rather than raised.
    """
    config = config or OrchestratorConfig()
    segment_fn = segment_fn or segment_document
    pdf_path = Path(pdf_path)
    source_sha256 = source_sha256 or sha256_file(pdf_path)
    existing_file = find_file_duplicate(output_dir, source_sha256)
    if existing_file:
        raise DuplicateDocumentError(
            f"Duplicate document already ingested as {existing_file.get('document_id', 'an existing document')}; upload skipped."
        )
    content_sha256 = content_sha256 or normalized_pdf_text_hash(pdf_path)
    if content_sha256:
        existing_content = find_content_duplicate(output_dir, content_sha256)
        if existing_content:
            raise DuplicateDocumentError(
                f"Duplicate document content already ingested as {existing_content.get('document_id', 'an existing document')}; upload skipped."
            )
    document_id = f"doc_{uuid.uuid4().hex[:12]}"
    source_file = pdf_path.name
    start_time = time.perf_counter()

    # --- Module 1: load ---
    try:
        raw_doc = load_pdf(pdf_path)
    except (PDFLoadError, FileNotFoundError) as e:
        raise IngestionError(f"Failed to load '{source_file}': {e}") from e

    # --- Module 2: pre-analyze ---
    page_analyses = analyze_document(raw_doc.pages, config.preanalyzer)
    page_analysis_by_number = {pa.page_number: pa for pa in page_analyses}

    # --- Module 3: layout segmentation ---
    try:
        regions = segment_fn(str(pdf_path), config.layout)
    except LayoutSegmentationError as e:
        raise IngestionError(f"Layout segmentation failed for '{source_file}': {e}") from e

    regions = sorted(regions, key=lambda r: (r.page_number, r.reading_order))
    progress_total = len(regions) + sum(
        1 for page_number in _PAGE_LEVEL_GEMINI_PAGES if page_number <= raw_doc.page_count
    )
    if progress_callback:
        progress_callback(0, progress_total, None)

    # --- Raw Extraction snapshot: written HERE, immediately after Module 3,
    # BEFORE any cleaning, OCR fallback, vision description, or validation
    # touches these regions. Captures Docling's direct output for debugging
    # extraction quality independent of everything downstream. The SAME
    # `regions` objects continue into the pipeline below unchanged - this
    # is a snapshot write, not a fork. ---
    raw_dir = write_raw_extraction(document_id, source_file, output_dir, regions, raw_doc.page_count)
    layout_analyses = build_layout_analysis(regions, config.layout_analysis)
    routing_decisions = decide_page_routing(page_analysis_by_number, layout_analyses, config.routing)

    # --- Page coverage validation (requirement: fail loudly instead of
    # silently producing incomplete output). Runs AFTER the raw snapshot
    # is written on purpose - if this raises, you still have raw/ showing
    # exactly what WAS extracted before the failure, for debugging. Runs
    # BEFORE any further processing - an incomplete extraction must never
    # reach chunking/final output. ---
    _validate_page_coverage(regions, raw_doc.page_count, page_analysis_by_number, source_file)

    # --- Two-pass figure handling: register every figure's hash BEFORE
    # classifying any of them as decorative (whole-document question) ---
    figure_images = _build_figure_images(regions, pdf_path, config)
    registry = DecorativeImageRegistry(config.vision)
    for region in regions:
        if region.region_type == "figure" and region.region_id in figure_images:
            registry.add(
                region.region_id, region.page_number,
                compute_average_hash(figure_images[region.region_id]),
            )

    # --- Modules 4/5/6/7: per-region extraction + validation ---
    ordered_pairs: list[tuple[Region, ValidatedRegionResult]] = []
    for completed, region in enumerate(regions, start=1):
        if region.region_type == "figure":
            validated = _validate_figure(region, figure_images, registry, config)
        else:
            page_analysis = page_analysis_by_number.get(region.page_number)
            native_trusted = _is_native_text_trusted(page_analysis)
            # `registry` is the SAME DecorativeImageRegistry built above
            # for real figures - shared here so a table that ends up
            # escalating to Vision (see validate_table_region) uses one
            # consistent decorative-image universe for the whole
            # document rather than a fresh one per table.
            decision = routing_decisions[region.page_number]
            validated = validate_region(
                region,
                pdf_path,
                native_text_is_trusted=decision.use_native,
                config=config.validation,
                ocr_config=config.ocr,
                cleaning_config=config.cleaning,
                vision_config=config.vision,
                decorative_registry=registry,
                use_gemini=decision.use_gemini,
            )
        ordered_pairs.append((region, validated))
        if progress_callback:
            progress_callback(completed, progress_total, region.page_number)

    # ------------------------------------------------------------------
    # POC: Add one page-level Gemini summary for hardcoded infographic pages
    # ------------------------------------------------------------------
    for page_number in sorted(_PAGE_LEVEL_GEMINI_PAGES):
        if page_number > raw_doc.page_count:
            continue

        page_region, page_validated = _build_page_level_gemini_pair(
            pdf_path=pdf_path,
            page_number=page_number,
            document_id=document_id,
            config=config,
        )

        ordered_pairs.append((page_region, page_validated))
        if progress_callback:
            progress_callback(len(ordered_pairs), progress_total, page_number)

    # Keep reading order correct
    ordered_pairs.sort(
        key=lambda pair: (pair[0].page_number, pair[0].reading_order)
    )

    # --- Module 8: chunking ---
    chunks, unrecoverable = chunk_document(ordered_pairs, document_id, source_file, config.chunker)
    chunks, deduplication_registry, deduplication_report = deduplicate_chunks(
        output_dir,
        document_id,
        source_file,
        chunks,
        source_sha256=source_sha256,
        content_sha256=content_sha256,
    )

    # --- Module 9: output writing ---
    processing_time = time.perf_counter() - start_time
    paths = write_document_output(
        document_id, source_file, output_dir, chunks, unrecoverable, ordered_pairs,
        raw_doc.page_count, processing_time, config.output,
    )
    paths.raw_dir = raw_dir
    persist_deduplication(output_dir, paths.document_dir, deduplication_registry, deduplication_report)

    # --- Additive: human-readable extraction renderer. Does NOT modify
    # or read any of the files write_document_output() just produced -
    # it's a separate, read-only consumer of the same ordered_pairs. ---
    write_human_readable_extraction(document_id, source_file, output_dir, ordered_pairs, raw_doc.page_count)

    logger.info(
        "Ingested '%s' as %s in %.2fs: %d chunks, %d unrecoverable regions",
        source_file, document_id, processing_time, len(chunks), len(unrecoverable),
    )
    return paths
