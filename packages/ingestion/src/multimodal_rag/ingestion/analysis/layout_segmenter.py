"""
Layout Segmenter Module
========================

Stage 3 of the ingestion pipeline. Wraps Docling's DocumentConverter -
the locked primary layout/table engine (see final_engineering_decisions.md
S4) - and maps its output into this pipeline's internal `Region` objects:
one per detected layout element (paragraph, heading, table, figure,
header, footer, caption, etc.), each carrying its page number, bounding
box, reading-order position, and extracted content.

Design contract (per locked architecture, S5 - region-level routing):
- The unit of downstream routing is the Region, never the Page. A single
  page's paragraph, table, and figure regions are extracted independently
  via the field of each Region, exactly as specified.
- Docling performs layout detection, native text extraction, AND table
  structure recognition in a single conversion pass (this is *why*
  Docling was chosen over gluing 3 separate libraries together - see
  final_engineering_decisions.md S4). This module's job is to map that
  single unified output into our internal schema, not to re-derive it.
- Region/table mapping logic is factored into pure functions
  (`_map_docling_document`, `_map_table_item`, ...) that operate on an
  already-built DoclingDocument object. This is deliberate: it lets the
  mapping logic be unit-tested against hand-constructed DoclingDocument
  fixtures, independent of whether the actual PDF->DoclingDocument model
  inference can run in a given environment.

KNOWN OPERATIONAL LIMITATION (disclosed, not hidden):
Docling's layout/table models are downloaded from Hugging Face on first
use. In network environments that don't allow huggingface.co egress
(e.g. this sandbox), `segment_document()` will raise `LayoutModelUnavailableError`
rather than a raw traceback. This was discovered during development, not
assumed away - the actual model-based conversion path could not be
end-to-end tested in this sandbox for that reason, and should be verified
in an environment with normal internet access before being trusted in
production. The pure mapping logic (DoclingDocument -> Region) IS fully
unit-tested here, independent of that limitation.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from docling_core.types.doc import DocItemLabel
from docling_core.types.doc.document import DoclingDocument, PictureItem, TableItem, TextItem
from multimodal_rag.paths import FIGURES_DIR, LEGACY_INGESTION_IMAGES_DIR, prefer_new_path

logger = logging.getLogger(__name__)


class LayoutSegmentationError(Exception):
    """Base exception for layout segmentation failures."""


class LayoutModelUnavailableError(LayoutSegmentationError):
    """
    Raised when Docling's underlying models cannot be loaded - most
    commonly because model weights need to be downloaded from Hugging
    Face and the environment's network policy blocks that host. This is
    an operational/environment problem, not a document problem, so it is
    surfaced distinctly from a genuinely corrupt/unsupported document.
    """


class DocumentConversionError(LayoutSegmentationError):
    """Raised when Docling fails to convert a specific document (as
    opposed to a model-loading problem affecting all documents)."""


# --------------------------------------------------------------------------
# Internal schema
# --------------------------------------------------------------------------

# Maps Docling's label taxonomy onto this pipeline's own region types.
# Kept as an explicit dict (not a 1:1 passthrough of Docling's enum) so the
# rest of the pipeline depends on OUR vocabulary, not Docling's internal
# one - if we ever swap the layout engine (per the documented future
# upgrade path to Azure/Google), only this mapping needs to change.
_LABEL_TO_REGION_TYPE: dict[DocItemLabel, str] = {
    DocItemLabel.TITLE: "heading",
    DocItemLabel.SECTION_HEADER: "heading",
    DocItemLabel.TEXT: "text",
    DocItemLabel.PARAGRAPH: "text",
    DocItemLabel.LIST_ITEM: "list_item",
    DocItemLabel.CAPTION: "caption",
    DocItemLabel.FOOTNOTE: "footnote",
    DocItemLabel.PAGE_HEADER: "header",
    DocItemLabel.PAGE_FOOTER: "footer",
    DocItemLabel.TABLE: "table",
    DocItemLabel.PICTURE: "figure",
    DocItemLabel.CHART: "figure",
    DocItemLabel.FORMULA: "formula",
    DocItemLabel.CODE: "code",
    DocItemLabel.HANDWRITTEN_TEXT: "text",  # flagged via is_handwritten, not a separate type
}

# Labels we deliberately treat as structural noise for RAG purposes (not
# emitted as regions at all) rather than unmapped/unknown.
_IGNORED_LABELS: set[DocItemLabel] = {
    DocItemLabel.MARKER,
    DocItemLabel.DOCUMENT_INDEX,
}


@dataclass
class TableData:
    """Internal structured-table representation - deliberately our own
    type, not Docling's, so downstream stages (cleaning, chunking,
    metadata) never need to know Docling exists."""
    rows: list[list[str]]
    num_rows: int
    num_cols: int
    markdown: str  # human-readable rendering, for embedding (S2 of requirements)


@dataclass
class Region:
    """
    One segmented layout element. This is the unit of downstream routing
    - see module docstring.
    """
    region_id: str
    page_number: int
    region_type: str  # see _LABEL_TO_REGION_TYPE values, plus "unknown"
    reading_order: int
    bbox: tuple[float, float, float, float]  # (l, t, r, b) in Docling's coord space
    coord_origin: str = "TOPLEFT"  # Docling's BoundingBox.coord_origin, needed by
    # downstream modules (e.g. the Validator's OCR-fallback region cropping) to
    # correctly map this bbox onto a rendered page image. Stored as the enum's
    # string value, not the enum itself, so this dataclass stays dependency-free.
    text_content: str | None = None
    table_data: TableData | None = None
    image_path: str | None = None
    is_handwritten: bool = False
    raw_label: str = ""  # original Docling label, kept for debugging/audit
    notes: list[str] = field(default_factory=list)


@dataclass
class LayoutSegmenterConfig:
    image_output_dir: Path = prefer_new_path(FIGURES_DIR, LEGACY_INGESTION_IMAGES_DIR)
    save_figure_images: bool = True

    # --- Added while investigating the std::bad_alloc / partial-output
    # issue on large (38-page) documents. See segment_document()'s
    # docstring for the full investigation writeup. ---

    generate_picture_images: bool = True
    # Docling's OWN default for this is False. That default was never
    # overridden anywhere in this module, which is the CONFIRMED root
    # cause of every figure region having image_path=None: without this
    # flag, Docling never renders picture crops at all, so
    # PictureItem.get_image() has nothing to return regardless of what
    # _save_figure_image() does. Verified via introspection of the
    # installed PdfPipelineOptions class, not assumed.

    images_scale: float = 1.0
    # Passed straight through to PdfPipelineOptions.images_scale. Higher
    # values increase per-page rendered-image memory linearly - if
    # memory pressure persists even with batching (see batch_size_pages
    # below), lowering this is the next lever to pull.

    table_structure_mode: str = "accurate"
    # "accurate" | "fast" - maps to Docling's TableFormerMode. "accurate"
    # is Docling's own default and matches this project's locked
    # "accuracy over speed" requirement, but it is the more memory/
    # compute-heavy of the two modes. Exposed here, config-driven, so it
    # can be switched to "fast" as a memory-pressure release valve
    # without touching code, consistent with this project's calibrate-
    # later philosophy.

    accelerator_device: str = "auto"
    # "auto" | "cpu" | "cuda" | "mps" | "xpu". "auto" is Docling's own
    # default. On a memory-constrained machine, forcing "cpu" explicitly
    # can avoid GPU/CPU fallback thrashing that "auto" occasionally
    # causes - worth trying if batching alone doesn't resolve memory
    # issues.

    accelerator_num_threads: int = 4
    # Docling's own default. More threads = more peak memory for
    # marginal speed gain on a memory-constrained machine; lower this
    # (e.g. to 2) as another release valve if needed.

    batch_size_pages: int = 10
    # THE key fix for the observed std::bad_alloc-after-page-7 failure.
    # Rather than one converter.convert() call across the whole document
    # (which keeps the C++-backed PDF backend and every page's model-
    # inference buffers alive simultaneously for the entire document),
    # the document is converted in page_range batches of this size, with
    # an explicit gc.collect() between batches to encourage prompt
    # release of native-backed memory. See segment_document()'s
    # docstring for the full reasoning and the honest caveat about what
    # could not be verified in this sandbox.

    # --- Added while investigating "~15-20% of content still missing"
    # after batching resolved the crash. Two independent, page-scoped
    # fallbacks - neither changes how a page that extracts cleanly is
    # handled at all. ---

    enable_native_text_fallback: bool = True
    native_fallback_min_char_ratio: float = 0.3
    # If Docling's non-table text for a page has fewer characters than
    # this fraction of PyMuPDF's raw text for the SAME page, Docling is
    # judged to have under-extracted that page. Below this ratio, ONE of
    # two recovery modes applies - see native_fallback_full_replace_below_ratio.
    # This is distinct from the Validator's (Module 7) OCR fallback: that
    # one runs later, per-region, and handles broken-font/scanned pages
    # via image-based OCR. This one runs here, per-page, right after
    # extraction, and handles Docling simply extracting less than what's
    # actually in the native text layer - a different failure mode with a
    # different, cheaper fix (PyMuPDF already has the real text, no OCR
    # needed).

    native_fallback_full_replace_below_ratio: float = 0.05
    # Two-tier recovery, added after real-world testing showed full-page
    # replacement was discarding good Docling regions (headings, lists)
    # on pages where Docling got SOME structure right but not everything:
    #   - ratio < this value (e.g. Docling found almost nothing, < 5%):
    #     FULL REPLACE (original behavior) - there's negligible good
    #     content to preserve, so replacing the page's non-table regions
    #     with one recovered native-text block remains the simplest,
    #     most reliable choice.
    #   - this value <= ratio < native_fallback_min_char_ratio (Docling
    #     found a meaningful but incomplete amount): SUPPLEMENT mode -
    #     every existing Docling region for the page is KEPT untouched,
    #     and one recovered native-text block is APPENDED after them
    #     (reading_order = after all existing regions on that page).
    # Deliberately NOT attempted: precise bbox-level reconciliation
    # (matching individual PyMuPDF text blocks against Docling regions'
    # bboxes and interpolating exact reading-order positions for
    # whatever's missing). That would require real geometric matching -
    # coordinate reconciliation, overlap-threshold tuning, multi-column
    # interpolation - genuine new complexity and new failure modes for a
    # benefit (perfect reading order for a recovered block on an already-
    # degraded page) judged not to be worth the added fragility. The
    # honest tradeoff of SUPPLEMENT mode: the appended block may
    # duplicate some content Docling already got, and its position is
    # "end of page," not precisely interleaved - accepted deliberately,
    # since duplicate text is a much smaller downstream harm for a RAG
    # pipeline than either lost content or a fragile reconciliation bug.

    enable_table_fallback: bool = True
    table_fallback_min_rows: int = 2
    table_fallback_min_cols: int = 2
    # If pdfplumber finds a table with at least this many rows/cols on a
    # page where Docling detected NO table region at all, that table is
    # added (converted to Markdown) as a recovered region. Deliberately
    # conservative - a 1-row or 1-column "table" is usually a false
    # positive from geometric table-detection heuristics, not real
    # tabular content.


# --------------------------------------------------------------------------
# Table mapping
# --------------------------------------------------------------------------

def _map_table_item(item: TableItem, doc: DoclingDocument) -> TableData:
    """
    Build our internal grid representation from Docling's TableData.

    Docling represents cells with row/col spans rather than a flat grid,
    so a merged cell (e.g. a header spanning 3 columns) appears ONCE in
    `table_cells` with col_span=3, not three times. We expand spans into
    a full num_rows x num_cols grid here, repeating the cell's text into
    every grid position it covers, because the downstream markdown/text
    linearization (S2 of requirements) needs a simple, complete grid, not
    a sparse span representation. This is a deliberate, documented
    simplification: a spanning header will read as repeated text across
    the columns it covers, which is the correct human-readable behavior
    for embedding purposes even though it's redundant for programmatic use.
    """
    data = item.data
    num_rows, num_cols = data.num_rows, data.num_cols
    grid: list[list[str]] = [["" for _ in range(num_cols)] for _ in range(num_rows)]

    for cell in data.table_cells:
        for r in range(cell.start_row_offset_idx, min(cell.end_row_offset_idx, num_rows)):
            for c in range(cell.start_col_offset_idx, min(cell.end_col_offset_idx, num_cols)):
                grid[r][c] = cell.text

    try:
        markdown = item.export_to_markdown(doc=doc)
    except Exception as e:
        # Fallback: build a minimal markdown table from the grid we just
        # computed, rather than losing the table's text entirely just
        # because Docling's own renderer hit an edge case.
        logger.warning("TableItem.export_to_markdown failed, using grid fallback: %s", e)
        header = "| " + " | ".join(grid[0]) + " |" if grid else ""
        sep = "| " + " | ".join(["---"] * num_cols) + " |" if num_cols else ""
        body = "\n".join("| " + " | ".join(row) + " |" for row in grid[1:])
        markdown = "\n".join(x for x in (header, sep, body) if x)

    return TableData(rows=grid, num_rows=num_rows, num_cols=num_cols, markdown=markdown)


# --------------------------------------------------------------------------
# Figure mapping
# --------------------------------------------------------------------------

def _save_figure_image(
    item: PictureItem, doc: DoclingDocument, region_id: str, config: LayoutSegmenterConfig
) -> str | None:
    if not config.save_figure_images:
        return None
    try:
        image = item.get_image(doc)
    except Exception as e:
        logger.warning("Could not retrieve image for figure region %s: %s", region_id, e)
        return None
    if image is None:
        logger.info(
            "Figure region %s has no renderable image (Docling returned None) - "
            "likely because page images were not generated during conversion.",
            region_id,
        )
        return None

    config.image_output_dir.mkdir(parents=True, exist_ok=True)
    out_path = config.image_output_dir / f"{region_id}.png"
    try:
        image.save(out_path)
    except Exception as e:
        logger.warning("Failed to save figure image for region %s: %s", region_id, e)
        return None
    return str(out_path)


# --------------------------------------------------------------------------
# Core mapping (pure - unit-testable without running model inference)
# --------------------------------------------------------------------------

def _map_docling_document(
    doc: DoclingDocument, config: LayoutSegmenterConfig | None = None
) -> list[Region]:
    """
    Convert an already-built DoclingDocument into our internal Region
    list. Pure function w.r.t. its inputs (aside from optionally writing
    figure image files to disk) - deliberately separated from
    segment_document() so it can be tested against hand-constructed
    DoclingDocument fixtures without requiring model inference.
    """
    config = config or LayoutSegmenterConfig()
    regions: list[Region] = []

    for order_idx, (item, _level) in enumerate(doc.iterate_items(traverse_pictures=True)):
        label = getattr(item, "label", None)
        if label is None or label in _IGNORED_LABELS:
            continue

        region_type = _LABEL_TO_REGION_TYPE.get(label)
        if region_type is None:
            # Unmapped label: don't silently drop the content. Emit it as
            # "unknown" and log loudly so new Docling label types get
            # noticed and classified deliberately, not by accident.
            logger.warning(
                "Unmapped Docling label '%s' encountered - emitting as region_type='unknown'. "
                "Add this label to _LABEL_TO_REGION_TYPE once its correct handling is decided.",
                label.value if hasattr(label, "value") else label,
            )
            region_type = "unknown"

        if not item.prov:
            logger.warning(
                "Item with label '%s' has no provenance (page/bbox) info - skipping.",
                label,
            )
            continue

        prov = item.prov[0]
        bbox = (prov.bbox.l, prov.bbox.t, prov.bbox.r, prov.bbox.b)
        coord_origin = prov.bbox.coord_origin.value if hasattr(prov.bbox.coord_origin, "value") else str(prov.bbox.coord_origin)
        region_id = f"r_{uuid.uuid4().hex[:12]}"
        notes: list[str] = []
        is_handwritten = label == DocItemLabel.HANDWRITTEN_TEXT
        if is_handwritten:
            notes.append(
                "Handwritten text detected - OCR/text-layer confidence for this region "
                "should be treated as unreliable (per adversarial-case requirement)."
            )

        region = Region(
            region_id=region_id,
            page_number=prov.page_no,
            region_type=region_type,
            reading_order=order_idx,
            bbox=bbox,
            coord_origin=coord_origin,
            raw_label=label.value if hasattr(label, "value") else str(label),
            is_handwritten=is_handwritten,
            notes=notes,
        )

        if isinstance(item, TableItem):
            try:
                region.table_data = _map_table_item(item, doc)
            except Exception as e:
                logger.error("Failed to map table region %s: %s", region_id, e)
                region.notes.append(f"Table mapping failed: {e}")
        elif isinstance(item, PictureItem):
            region.image_path = _save_figure_image(item, doc, region_id, config)
        elif isinstance(item, TextItem):
            region.text_content = item.text
        else:
            # Any other DocItem subtype we haven't explicitly handled -
            # try to read .text if present, otherwise leave content empty
            # rather than raising, so one odd item type doesn't kill the
            # whole document.
            region.text_content = getattr(item, "text", None)

        regions.append(region)

    return regions


# --------------------------------------------------------------------------
# Page-scoped fallbacks (investigation: "~15-20% of content still missing"
# after batching fixed the crash). See LayoutSegmenterConfig for the
# reasoning behind each threshold.
# --------------------------------------------------------------------------

def _rows_to_markdown(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    header = "| " + " | ".join(rows[0]) + " |"
    sep = "| " + " | ".join(["---"] * len(rows[0])) + " |"
    body = "\n".join("| " + " | ".join(row) + " |" for row in rows[1:])
    return "\n".join(x for x in (header, sep, body) if x)


def _native_text_fallback_region(
    page_number: int, raw_text: str, reading_order: int, raw_label: str, note: str,
) -> Region:
    return Region(
        region_id=f"r_{uuid.uuid4().hex[:12]}",
        page_number=page_number,
        region_type="text",
        reading_order=reading_order,
        bbox=(0.0, 0.0, 0.0, 0.0),  # no single reliable bbox for a whole-page recovered block
        text_content=raw_text,
        raw_label=raw_label,
        notes=[note],
    )


def _apply_native_text_fallback(
    regions: list[Region], page_raw_texts: dict[int, str], config: LayoutSegmenterConfig,
) -> list[Region]:
    """
    Per-page quality check: compare Docling's extracted text volume
    against PyMuPDF's independent raw text extraction for the same page.

    Two-tier response (see LayoutSegmenterConfig.native_fallback_full_replace_below_ratio
    for the full reasoning):
    - Far below baseline (ratio < native_fallback_full_replace_below_ratio):
      REPLACE the page's non-table regions with one recovered block, as
      before - negligible good content exists to preserve.
    - Below threshold but not negligible: SUPPLEMENT - keep every
      existing Docling region for the page untouched, append one
      recovered block after them.
    Table regions are never touched by this function either way - they
    have their own fallback (`_apply_table_fallback`).
    """
    if not config.enable_native_text_fallback:
        return regions

    regions_by_page: dict[int, list[Region]] = {}
    for r in regions:
        regions_by_page.setdefault(r.page_number, []).append(r)

    result: list[Region] = []
    for page_number, raw_text in page_raw_texts.items():
        page_regions = regions_by_page.pop(page_number, [])
        raw_char_count = len(raw_text.strip())
        docling_char_count = sum(
            len(r.text_content or "") for r in page_regions if r.region_type != "table"
        )
        ratio = (docling_char_count / raw_char_count) if raw_char_count > 0 else 1.0

        if raw_char_count > 0 and ratio < config.native_fallback_full_replace_below_ratio:
            logger.warning(
                "Page %d: Docling extracted %d/%d chars (ratio %.2f < full-replace threshold "
                "%.2f) - REPLACING this page's non-table regions with one recovered block",
                page_number, docling_char_count, raw_char_count, ratio,
                config.native_fallback_full_replace_below_ratio,
            )
            table_regions = [r for r in page_regions if r.region_type == "table"]
            min_order = min((r.reading_order for r in page_regions), default=0)
            result.append(_native_text_fallback_region(
                page_number, raw_text, min_order, "native_text_fallback",
                "Recovered via PyMuPDF native-text fallback (full replace): Docling extracted "
                "almost nothing for this page - see LayoutSegmenterConfig.native_fallback_full_replace_below_ratio.",
            ))
            result.extend(table_regions)

        elif raw_char_count > 0 and ratio < config.native_fallback_min_char_ratio:
            logger.warning(
                "Page %d: Docling extracted %d/%d chars (ratio %.2f < threshold %.2f, but "
                ">= full-replace threshold) - SUPPLEMENTING: keeping Docling's %d existing "
                "regions and appending one recovered block",
                page_number, docling_char_count, raw_char_count, ratio,
                config.native_fallback_min_char_ratio, len(page_regions),
            )
            result.extend(page_regions)  # keep everything Docling found, untouched
            max_order = max((r.reading_order for r in page_regions), default=-1)
            result.append(_native_text_fallback_region(
                page_number, raw_text, max_order + 1, "native_text_supplement",
                "Recovered via PyMuPDF native-text fallback (supplement): Docling's extraction "
                "for this page was below the quality threshold, but existing Docling regions "
                "were kept rather than replaced. This block may duplicate some content Docling "
                "already extracted correctly - see LayoutSegmenterConfig.native_fallback_full_replace_below_ratio.",
            ))
        else:
            result.extend(page_regions)

    for leftover in regions_by_page.values():
        result.extend(leftover)  # pages with regions but no raw_text entry (shouldn't normally happen)

    return result


def _apply_table_fallback(
    regions: list[Region], pdf_path: Path, total_pages: int, config: LayoutSegmenterConfig,
) -> list[Region]:
    """
    For every page where Docling detected NO table region at all, run
    pdfplumber's independent (non-ML, geometry-based) table detector as a
    second opinion. A table pdfplumber finds with reasonable dimensions
    is added as a recovered table region, converted to Markdown via the
    same rows-to-markdown logic Module 3 already uses for Docling tables.
    """
    if not config.enable_table_fallback:
        return regions

    pages_with_docling_table = {r.page_number for r in regions if r.region_type == "table"}
    pages_to_check = sorted(set(range(1, total_pages + 1)) - pages_with_docling_table)
    if not pages_to_check:
        return regions

    try:
        import pdfplumber
    except ImportError:
        logger.warning("pdfplumber is not installed - skipping table fallback entirely")
        return regions

    added: list[Region] = []
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page_number in pages_to_check:
                if page_number > len(pdf.pages):
                    continue
                try:
                    raw_tables = pdf.pages[page_number - 1].extract_tables()
                except Exception as e:
                    logger.warning("pdfplumber table extraction failed on page %d: %s", page_number, e)
                    continue

                for raw_table in raw_tables:
                    rows = [[cell or "" for cell in row] for row in raw_table]
                    if len(rows) < config.table_fallback_min_rows:
                        continue
                    if not rows or len(rows[0]) < config.table_fallback_min_cols:
                        continue
                    num_cols = max(len(r) for r in rows)
                    normalized_rows = [r + [""] * (num_cols - len(r)) for r in rows]
                    markdown = _rows_to_markdown(normalized_rows)
                    table_data = TableData(
                        rows=normalized_rows, num_rows=len(normalized_rows),
                        num_cols=num_cols, markdown=markdown,
                    )
                    existing_orders = [r.reading_order for r in regions if r.page_number == page_number]
                    reading_order = (max(existing_orders) + 1) if existing_orders else 0
                    added.append(Region(
                        region_id=f"r_{uuid.uuid4().hex[:12]}",
                        page_number=page_number, region_type="table",
                        reading_order=reading_order, bbox=(0.0, 0.0, 0.0, 0.0),
                        table_data=table_data, raw_label="pdfplumber_table_fallback",
                        notes=["Recovered via pdfplumber: Docling did not detect a table on this page."],
                    ))
                    logger.info(
                        "Page %d: recovered a %dx%d table via pdfplumber fallback",
                        page_number, len(normalized_rows), num_cols,
                    )
    except Exception as e:
        logger.warning("pdfplumber fallback pass failed entirely, proceeding without it: %s", e)
        return regions

    return regions + added


# --------------------------------------------------------------------------
# Public entry point (impure - does actual Docling model inference)
# --------------------------------------------------------------------------

def _build_converter(config: LayoutSegmenterConfig):
    """
    Build a configured DocumentConverter, instead of the bare
    `DocumentConverter()` this module used previously.

    INVESTIGATION FINDINGS (std::bad_alloc after page 7 on a 38-page
    document; figure regions all missing images):

    1. Why figure regions had no renderable image (question #4):
       CONFIRMED, not speculated. Introspecting the installed
       PdfPipelineOptions class (docling==2.110.0) shows
       `generate_picture_images` defaults to False. This module
       previously called bare `DocumentConverter()`, which uses that
       default - so Docling never rendered picture crops at all, and
       every downstream `PictureItem.get_image()` call in
       `_save_figure_image()` had nothing to return, regardless of that
       function's own logic. Fixed below by explicitly setting
       `generate_picture_images=True` via PdfPipelineOptions.

    2. Are the default DocumentConverter/PdfPipelineOptions causing
       excessive memory usage (question #2)? Partially, and confirmed
       ONLY for what introspection can show: the expensive optional
       features (picture classification, picture description, chart
       extraction, code/formula enrichment) are all already False by
       default, so they were not silently running. What IS on by
       default and IS expensive: `do_table_structure=True` running in
       `TableFormerMode.ACCURATE` (the heavier of Docling's two table
       modes) on every page, plus layout detection and OCR running on
       every page, all within a single `converter.convert()` call that
       keeps the whole document's backend and per-page inference buffers
       alive until the ENTIRE document finishes. That accumulation
       pattern - fine for 7 pages, exhausted by somewhere past that - is
       the textbook signature of a memory ceiling being hit by
       accumulation, not a single unusually-complex page. This module
       now exposes `table_structure_mode`, `accelerator_device`, and
       `accelerator_num_threads` as config-driven release valves.

    3. Should pages be processed in batches (question #3)? Yes -
       implemented below via Docling's own `page_range` parameter on
       `convert()` (confirmed to exist via signature introspection:
       `page_range: Tuple[int, int] = (1, sys.maxsize)`), called
       repeatedly across page-range batches with an explicit
       `gc.collect()` between them, rather than one whole-document call.

    HONEST LIMITATION: this sandbox cannot execute Docling's actual
    model-based conversion at all (Hugging Face is not in the network
    allowlist - see this module's top docstring), so the batching fix
    could not be empirically confirmed to eliminate the std::bad_alloc
    here. What IS verified: `generate_picture_images` defaulting to
    False (confirmed root cause of #4), `page_range` existing as a real,
    documented parameter on `convert()` (confirmed via introspection, not
    memory), and `TableFormerMode.ACCURATE` being the confirmed default
    mode. The batching strategy itself is the standard mitigation for
    this class of problem (bound peak memory by bounding work-per-call),
    but you should watch the logs on your next 38-page run to confirm it
    resolves it - and if std::bad_alloc still occurs, the next levers to
    pull are, in order: lower `batch_size_pages` further (e.g. to 5),
    set `table_structure_mode="fast"`, set `accelerator_device="cpu"`
    explicitly, then `accelerator_num_threads=2`.
    """
    from docling.datamodel.pipeline_options import (
        AcceleratorOptions,
        PdfPipelineOptions,
        TableFormerMode,
        TableStructureOptions,
    )
    from docling.datamodel.base_models import InputFormat
    from docling.document_converter import DocumentConverter, PdfFormatOption

    mode = TableFormerMode.ACCURATE if config.table_structure_mode == "accurate" else TableFormerMode.FAST

    pipeline_options = PdfPipelineOptions(
        generate_picture_images=config.generate_picture_images,
        images_scale=config.images_scale,
        table_structure_options=TableStructureOptions(mode=mode),
        accelerator_options=AcceleratorOptions(
            device=config.accelerator_device,
            num_threads=config.accelerator_num_threads,
        ),
    )
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
    )


def segment_document(
    pdf_path: str | Path, config: LayoutSegmenterConfig | None = None
) -> list[Region]:
    """
    Run Docling's conversion pipeline on a PDF and return internal Region
    objects, processing the document in page-range batches (per
    `config.batch_size_pages`) rather than one whole-document call - see
    `_build_converter`'s docstring for the full investigation behind
    this. The converter itself is built once (models load once) and
    reused across batches; only the per-batch native/inference buffers
    are released between batches via an explicit `gc.collect()`.

    Raises:
        LayoutModelUnavailableError: Docling's models could not be loaded
            (e.g. blocked Hugging Face access, no cached weights).
        DocumentConversionError: conversion failed for this specific
            document, or its true page count could not be determined.
    """
    import fitz  # PyMuPDF - already a project dependency (Module 1)
    import gc

    config = config or LayoutSegmenterConfig()
    path = Path(pdf_path)

    # Ground-truth page count AND raw per-page text, determined
    # independently of Docling via PyMuPDF. The page count drives
    # batching and the orchestrator's page-coverage check; the raw text
    # drives the native-text fallback below. Gathered in one pass while
    # the file is already open, rather than reopening it later.
    try:
        with fitz.open(str(path)) as doc:
            total_pages = doc.page_count
            page_raw_texts = {i + 1: doc[i].get_text("text") for i in range(total_pages)}
    except Exception as e:
        raise DocumentConversionError(f"Could not determine page count for '{path.name}': {e}") from e

    try:
        converter = _build_converter(config)
    except Exception as e:
        raise DocumentConversionError(f"Could not construct Docling converter: {e}") from e

    batch_size = max(1, config.batch_size_pages)
    all_regions: list[Region] = []

    for batch_start in range(1, total_pages + 1, batch_size):
        batch_end = min(batch_start + batch_size - 1, total_pages)
        try:
            result = converter.convert(str(path), page_range=(batch_start, batch_end))
        except Exception as e:
            message = str(e)
            if "huggingface" in message.lower() or "not in allowlist" in message.lower() or \
               "HfHub" in type(e).__name__:
                raise LayoutModelUnavailableError(
                    f"Docling's models could not be loaded (network/model-cache issue): {e}"
                ) from e
            raise DocumentConversionError(
                f"Docling failed converting pages {batch_start}-{batch_end} of '{path.name}': {e}"
            ) from e

        try:
            all_regions.extend(_map_docling_document(result.document, config))
        except Exception as e:
            raise DocumentConversionError(
                f"Failed to map Docling output for pages {batch_start}-{batch_end} of "
                f"'{path.name}' into internal regions: {e}"
            ) from e

        logger.info("Converted pages %d-%d of %d for '%s'", batch_start, batch_end, total_pages, path.name)

        # Explicitly drop the batch's result and force garbage collection
        # before starting the next batch. Docling's PDF backend and model
        # inference buffers are C++/native-backed (docling-parse,
        # pypdfium2, ONNX/torch tensors) - Python's reference counting
        # alone does not reliably reclaim that memory promptly within a
        # single long-lived process, which is the most plausible
        # explanation for memory accumulating across pages until
        # std::bad_alloc. This is the standard mitigation for that
        # pattern without changing the pipeline's architecture.
        del result
        gc.collect()

    # Page-scoped fallbacks, applied once across the full document after
    # all batches complete - see LayoutSegmenterConfig and the two
    # _apply_*_fallback functions above for the full reasoning.
    all_regions = _apply_native_text_fallback(all_regions, page_raw_texts, config)
    all_regions = _apply_table_fallback(all_regions, path, total_pages, config)

    return all_regions
