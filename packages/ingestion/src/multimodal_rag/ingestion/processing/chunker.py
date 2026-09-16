"""
Chunker + Metadata Enricher Module
=====================================

Consumes validated regions (Module 7's ValidatedRegionResult, paired with
their originating Region for ordering/type/bbox context) and produces
final Chunk objects ready for embedding, each carrying the full metadata
schema from the locked architecture.

Structure-aware design (per locked requirements, "layout preservation is
my highest priority" and S1/S2):
- Heading regions start a new section. Heading hierarchy is inferred
  from READING ORDER and PAGE BOUNDARIES, not from Docling's raw_label -
  an earlier version tried raw_label ("title" vs "section_header"), but
  real tracing showed Docling can label every heading "section_header"
  with no distinction at all. A later version used a single heading
  stack that PERSISTED ACROSS THE ENTIRE DOCUMENT (grouping consecutive
  heading regions into "runs" and aligning each run against the stack) -
  this correctly reproduced nested comparison sections, but tracing
  against the real document surfaced a real bug: a lone new top-level
  heading on a page with no sibling heading to disambiguate against
  (e.g. a page titled "Enterprise AI: What CMOs need to know" following
  a 10-page run of paired "Short-term/Long-term implications" comparison
  pages) could not be told apart from "one level deeper" by run-length
  alone, so a STALE heading from many pages earlier silently leaked into
  an unrelated section's metadata and embedded text.
- CURRENT approach: heading composition is scoped to PAGE BOUNDARIES.
  Within a page, consecutive heading regions still compose via the same
  run-length "align this run against the stack" logic as before (this is
  what correctly keeps two side-by-side comparison headings like
  "Short-term implications" / "Long-term implications" - both on the
  same page - distinct from each other). But crossing a page boundary
  now behaves as follows:
    - If the NEW page's first region is a heading, the heading stack is
      discarded entirely and rebuilt from ONLY that page's own headings
      - this is what fixes the leakage bug, since a stale multi-page-old
        heading can never survive past the first heading of a new page.
    - If the NEW page's first region is body content (no heading yet),
      the heading stack is INHERITED UNCHANGED from wherever the
      previous page left it, and buffered content continues to compose
      under that inherited label - this is what correctly threads a
      heading across a genuinely continuous multi-page section (e.g. a
      subsection whose body text flows from the bottom of one page onto
      the top of the next with no heading restating it).
  A heading appearing mid-page (not the page's first region) is
  unaffected by page scoping - it still nests via the normal run-length
  logic, which is how a sub-heading correctly nests under a heading
  inherited from the previous page. See chunk_document's page_changed
  handling for the exact implementation.
  DISCLOSED TRADE-OFF: because rule 1 above is unconditional, a
  continuation page whose own top-level heading immediately follows a
  page boundary will NOT retain an even-higher-level ancestor heading
  from a prior page (e.g. a subsection heading that opens a new page
  will not be prefixed with a top-level title established several pages
  earlier) - this was a deliberate, agreed simplification favoring
  reliability over maximal hierarchy depth, not an oversight.
- The composed heading stack is PREPENDED INTO chunk_text itself (not
  just stored as metadata) - see _compose_heading_prefix - so the
  heading words are actually part of what gets embedded, not just a
  field alongside it. If a section's body text is long enough to be
  split into multiple chunks, the SAME full composed heading stack is
  prepended to EVERY resulting chunk (splitting happens on the body text
  first, the heading prefix is added after), so no split chunk ever
  loses its ancestor context. Chunks are also NEVER merged across a page
  boundary - the buffer is always flushed before any new page's first
  region is processed.
- Table and figure regions are NEVER merged with surrounding paragraph
  text and never split mid-content (barring the oversized-table case
  below) - each becomes its own chunk, preserving their structural
  identity rather than flattening them into the surrounding narrative.
  Table chunking logic itself is unchanged by the heading-hierarchy fix.
- Regular paragraph/list/caption text within a section is concatenated
  into a parent section and emitted as sentence-safe semantic child chunks.
  The child metadata records the parent-section ID and child position so
  downstream tools can retain both precise retrieval units and their
  structural relationship. A sentence longer than the target is retained
  whole rather than being cut at an arbitrary character boundary.
- Regions with validation_status="failed" (no recoverable text) are
  excluded from chunks entirely but are returned separately as
  `unrecoverable`, not silently discarded - Module 9 (Output Writer)
  surfaces these in the audit report.

Documented simplification: after buffered section text is split, this
module does not attempt to map each resulting sub-chunk back to the
exact originating region/page - it attributes the FULL set of pages the
section's source regions came from to every chunk in that section. For
the vast majority of enterprise documents a section rarely spans more
than 1-2 pages, so this is a deliberate, disclosed simplification rather
than an attempt at more precision than the use case needs.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from multimodal_rag.ingestion.analysis.layout_segmenter import Region, TableData
from multimodal_rag.ingestion.processing.validator import ValidatedRegionResult

logger = logging.getLogger(__name__)

PIPELINE_VERSION = "1.2.0"

# Table/figure content is never merged with paragraph text (see module
# docstring); these region types get their own dedicated chunk(s).
_STRUCTURAL_REGION_TYPES = frozenset({"table", "figure"})
_HEADING_REGION_TYPES = frozenset({"heading"})
# Everything else (text, list_item, caption, footnote, formula, code,
# header, footer, unknown) is treated as mergeable paragraph-like content.


@dataclass
class ChunkerConfig:
    # Retrieval children stay small enough for precise matching. This is a
    # soft limit: an individual oversized sentence remains intact.
    chunk_size: int = 450
    chunk_overlap: int = 75
    # Oversized-table handling: if a table's markdown exceeds this many
    # characters, split it by row-groups rather than emitting one giant
    # chunk - keeps embeddings meaningful without ever splitting a table
    # in the middle of a row.
    max_table_chunk_chars: int = 2000
    table_rows_per_split: int = 15


@dataclass
class ChunkMetadata:
    """Full metadata schema per the locked architecture (S9/S12 of the
    original requirements, extended with pipeline_version for debugging
    future regressions)."""
    chunk_id: str
    document_id: str
    source_file: str
    page_numbers: list[int]
    section_title: str | None
    layout_type: str  # "paragraph" | "table" | "figure" | "list_item" | "caption" | etc.
    extraction_method: str
    ocr_confidence: float | None
    validation_status: str  # "ok" | "low_confidence" | "failed" (failed chunks aren't emitted, but the value is retained on any low_confidence chunk for audit)
    ingestion_timestamp: str
    pipeline_version: str
    table_reference: dict | None = None
    image_reference: dict | None = None
    source_region_ids: list[str] = field(default_factory=list)
    parent_section_id: str | None = None
    parent_chunk_id: str | None = None
    chunk_level: str = "child"  # "parent" is stored for context, not embedded.
    content_hash: str | None = None
    child_index: int | None = None
    child_count: int | None = None


@dataclass
class Chunk:
    chunk_text: str
    metadata: ChunkMetadata


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _aggregate_validation_status(statuses: list[str]) -> str:
    """Worst-case aggregation: a section chunk built from multiple source
    regions is only as trustworthy as its least-trustworthy contributor.
    ('failed' regions are filtered out before this is ever called, so in
    practice this only ever needs to choose between 'ok' and 'low_confidence'.)"""
    if "failed" in statuses:
        return "failed"
    if "low_confidence" in statuses:
        return "low_confidence"
    return "ok"


def _aggregate_confidence(confidences: list[float | None]) -> float | None:
    present = [c for c in confidences if c is not None]
    if not present:
        return None
    return sum(present) / len(present)


def _compose_section_title(heading_stack: list[str]) -> str | None:
    """Compact form for ChunkMetadata.section_title, e.g. 'Short- and
    long-term implications of AI in marketing > Short-term implications
    > Data'. Returns None if no heading is active yet."""
    return " > ".join(heading_stack) if heading_stack else None


def _compose_heading_prefix(heading_stack: list[str]) -> str:
    """Multi-line form prepended into chunk_text itself so heading words
    are actually embedded, not just present in metadata. Empty string if
    no heading is active yet (nothing to prepend)."""
    return ("\n".join(heading_stack) + "\n\n") if heading_stack else ""


# --------------------------------------------------------------------------
# Table chunking (split-by-row-group for oversized tables)
# --------------------------------------------------------------------------

def _chunk_table(
    region: Region, validated: ValidatedRegionResult, document_id: str,
    source_file: str, config: ChunkerConfig,
) -> list[Chunk]:
    table: TableData | None = validated.table_data
    text = validated.final_text or ""

    if table is None or len(text) <= config.max_table_chunk_chars:
        # Small enough (or structure already lost via OCR fallback) - one chunk.
        return [_build_chunk(
            text, document_id, source_file, [validated.page_number],
            section_title=None, layout_type="table",
            extraction_method=validated.extraction_method_used,
            confidence=validated.confidence, validation_status=validated.validation_status,
            source_region_ids=[region.region_id],
            table_reference={"num_rows": table.num_rows, "num_cols": table.num_cols} if table else None,
        )]

    # Oversized structured table: split by row-groups, never mid-row.
    chunks: list[Chunk] = []
    rows = table.rows
    header = rows[0] if rows else []
    for start in range(1, len(rows), config.table_rows_per_split):
        group = rows[start:start + config.table_rows_per_split]
        sub_rows = [header] + group if header else group
        sub_markdown = _rows_to_markdown(sub_rows)
        chunks.append(_build_chunk(
            sub_markdown, document_id, source_file, [validated.page_number],
            section_title=None, layout_type="table",
            extraction_method=validated.extraction_method_used,
            confidence=validated.confidence, validation_status=validated.validation_status,
            source_region_ids=[region.region_id],
            table_reference={
                "num_rows": len(group), "num_cols": table.num_cols,
                "row_group_start": start, "of_total_rows": table.num_rows,
            },
        ))
    return chunks


def _rows_to_markdown(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    header = "| " + " | ".join(rows[0]) + " |"
    sep = "| " + " | ".join(["---"] * len(rows[0])) + " |"
    body = "\n".join("| " + " | ".join(row) + " |" for row in rows[1:])
    return "\n".join(x for x in (header, sep, body) if x)


def _build_chunk(
    text: str, document_id: str, source_file: str, page_numbers: list[int],
    section_title: str | None, layout_type: str, extraction_method: str,
    confidence: float | None, validation_status: str, source_region_ids: list[str],
    table_reference: dict | None = None, image_reference: dict | None = None,
    parent_section_id: str | None = None, child_index: int | None = None,
    child_count: int | None = None, parent_chunk_id: str | None = None,
    chunk_level: str = "child",
) -> Chunk:
    return Chunk(
        chunk_text=text,
        metadata=ChunkMetadata(
            chunk_id=f"c_{uuid.uuid4().hex[:12]}",
            document_id=document_id,
            source_file=source_file,
            page_numbers=sorted(set(page_numbers)),
            section_title=section_title,
            layout_type=layout_type,
            extraction_method=extraction_method,
            ocr_confidence=confidence,
            validation_status=validation_status,
            ingestion_timestamp=_now_iso(),
            pipeline_version=PIPELINE_VERSION,
            table_reference=table_reference,
            image_reference=image_reference,
            source_region_ids=source_region_ids,
            parent_section_id=parent_section_id,
            parent_chunk_id=parent_chunk_id,
            chunk_level=chunk_level,
            child_index=child_index,
            child_count=child_count,
        ),
    )


# --------------------------------------------------------------------------
# Paragraph-buffer flushing (section text -> semantic children -> chunks)
# --------------------------------------------------------------------------

@dataclass
class _BufferedRegion:
    text: str
    page_number: int
    region_id: str
    extraction_method: str
    confidence: float | None
    validation_status: str
    layout_type: str


_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?…])(?:[\"')\]]+)?\s+")


def _semantic_units(text: str, max_chars: int) -> list[str]:
    """Return paragraph or sentence units without cutting a sentence in half.

    Short paragraphs remain intact. Oversized paragraphs are split into full
    sentences; a single sentence larger than the target is deliberately kept
    whole so the child remains semantically complete.
    """
    units: list[str] = []
    for paragraph in re.split(r"\n\s*\n+", text.strip()):
        paragraph = re.sub(r"\s+", " ", paragraph).strip()
        if not paragraph:
            continue
        if len(paragraph) <= max_chars:
            units.append(paragraph)
            continue
        sentences = [sentence.strip() for sentence in _SENTENCE_BOUNDARY.split(paragraph) if sentence.strip()]
        units.extend(sentences or [paragraph])
    return units


def _join_units(units: list[str]) -> str:
    return "\n\n".join(units)


def _overlap_suffix(units: list[str], overlap_chars: int) -> list[str]:
    if overlap_chars <= 0:
        return []
    suffix: list[str] = []
    for unit in reversed(units):
        candidate = [unit, *suffix]
        if suffix and len(_join_units(candidate)) > overlap_chars:
            break
        suffix = candidate
        if len(_join_units(suffix)) >= overlap_chars:
            break
    return suffix


def _semantic_children(text: str, config: ChunkerConfig) -> list[str]:
    """Pack semantic units into child chunks with sentence-safe overlap."""
    units = _semantic_units(text, config.chunk_size)
    if not units:
        return []

    children: list[str] = []
    current: list[str] = []
    for unit in units:
        if not current:
            current = [unit]
            continue
        if len(_join_units([*current, unit])) <= config.chunk_size:
            current.append(unit)
            continue

        children.append(_join_units(current))
        current = _overlap_suffix(current, config.chunk_overlap)
        while current and len(_join_units([*current, unit])) > config.chunk_size:
            current.pop(0)
        current.append(unit)

    if current:
        children.append(_join_units(current))
    return children


def _flush_paragraph_buffer(
    buffer: list[_BufferedRegion], document_id: str, source_file: str,
    section_title: str | None, heading_prefix: str, config: ChunkerConfig,
) -> list[Chunk]:
    if not buffer:
        return []

    combined_text = "\n\n".join(b.text for b in buffer)
    splits = _semantic_children(combined_text, config)
    # Heading prefix is applied AFTER splitting the body text, to every
    # resulting piece - this is what guarantees a section split into
    # multiple chunks never loses its parent heading on chunks after the
    # first (see module docstring). Splitting on the combined text
    # (heading + body) instead would only place the heading in the FIRST
    # chunk, which is exactly the behavior this fix is required to avoid.
    final_texts = [f"{heading_prefix}{s}" for s in splits] if heading_prefix else splits

    all_pages = [b.page_number for b in buffer]
    all_region_ids = [b.region_id for b in buffer]
    agg_status = _aggregate_validation_status([b.validation_status for b in buffer])
    agg_confidence = _aggregate_confidence([b.confidence for b in buffer])
    # If the section mixes region types (e.g. a caption plus body text),
    # layout_type reflects that rather than overclaiming a single type.
    distinct_layout_types = {b.layout_type for b in buffer}
    layout_type = distinct_layout_types.pop() if len(distinct_layout_types) == 1 else "mixed"

    parent_section_id = f"p_{uuid.uuid4().hex[:12]}"
    parent_text = f"{heading_prefix}{combined_text}" if heading_prefix else combined_text
    parent = _build_chunk(
        parent_text, document_id, source_file, all_pages, section_title,
        layout_type, "|".join(sorted({b.extraction_method for b in buffer})),
        agg_confidence, agg_status, all_region_ids,
        parent_section_id=parent_section_id,
        chunk_level="parent",
    )
    children = [
        _build_chunk(
            final_text, document_id, source_file, all_pages, section_title,
            layout_type, "|".join(sorted({b.extraction_method for b in buffer})),
            agg_confidence, agg_status, all_region_ids,
            parent_section_id=parent_section_id,
            parent_chunk_id=parent.metadata.chunk_id,
            child_index=child_index,
            child_count=len(final_texts),
        )
        for child_index, final_text in enumerate(final_texts)
    ]
    return [parent, *children]


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------

def chunk_document(
    ordered_pairs: list[tuple[Region, ValidatedRegionResult]],
    document_id: str,
    source_file: str,
    config: ChunkerConfig | None = None,
) -> tuple[list[Chunk], list[ValidatedRegionResult]]:
    """
    Build final chunks from an ordered (by reading_order) list of
    (Region, ValidatedRegionResult) pairs for one document.

    Returns (chunks, unrecoverable) - `unrecoverable` holds every
    validated result with validation_status == "failed" or no usable
    text, for Module 9's audit report. Nothing is silently dropped: it
    either becomes a chunk or is returned here.
    """
    config = config or ChunkerConfig()
    chunks: list[Chunk] = []
    unrecoverable: list[ValidatedRegionResult] = []
    buffer: list[_BufferedRegion] = []
    heading_stack: list[str] = []
    current_page: int | None = None
    # `pending_run` accumulates heading texts for the CURRENT run (an
    # unbroken sequence of consecutive heading regions ON THE SAME PAGE).
    # It is only applied to `heading_stack` once the run ends - i.e. the
    # moment a non-heading region is encountered, or at the end of the
    # document. This deferred application implements "align this run
    # against the existing stack, replacing only the bottom len(run)
    # levels" from a single forward pass with no lookahead. See module
    # docstring for the full algorithm.
    #
    # PAGE SCOPING (replaces the old cross-document-persistent stack):
    # `heading_stack` is no longer allowed to carry stale context across
    # an arbitrary number of pages. Real-document tracing showed the
    # cross-page stack fix was itself fragile: a single new top-level
    # heading on a page with no other heading to disambiguate against
    # (e.g. page 18's "Enterprise AI: What CMOs need to know" following
    # the 10-page "Short-term/Long-term implications" section) could not
    # be told apart from "one level deeper" by run-length alone, so a
    # stale parent heading from many pages earlier silently leaked in.
    # The fix scopes heading composition to the page boundary instead:
    # - A new page whose FIRST region is a heading discards
    #   `heading_stack` entirely and rebuilds it from only that page's
    #   own headings (see the page-boundary block below).
    # - A new page whose FIRST region is body content (no heading yet)
    #   inherits `heading_stack` completely unchanged from wherever the
    #   previous page left it - this is what correctly threads a
    #   heading label across a genuinely continuous multi-page section
    #   like "The data challenges" (pages 21-22) without needing a
    #   document-wide persistent stack.
    # A heading appearing MID-page (not the page's first region) is
    # unaffected by page scoping - it still composes via the normal
    # run-length logic below, which is how a sub-heading correctly
    # nests under a heading inherited from the previous page.
    pending_run: list[str] = []

    def _apply_pending_run() -> None:
        nonlocal heading_stack, pending_run
        if pending_run:
            run_length = len(pending_run)
            heading_stack = heading_stack[: max(0, len(heading_stack) - run_length)] + pending_run
            pending_run = []

    for region, validated in ordered_pairs:
        if validated.validation_status == "failed" or not (validated.final_text or "").strip():
            if validated.region_type not in ("figure",) or validated.validation_status == "failed":
                unrecoverable.append(validated)
            continue

        page_changed = current_page is not None and validated.page_number != current_page
        if page_changed:
            # Crossing a page boundary. Content must never merge across
            # pages, so finalize and flush everything belonging to the
            # OLD page first, using the OLD (pre-reset) heading_stack -
            # a trailing heading run right at the end of a page (rare,
            # but possible) still belongs to that page, so it's applied
            # before anything is reset.
            _apply_pending_run()
            section_title = _compose_section_title(heading_stack)
            heading_prefix = _compose_heading_prefix(heading_stack)
            chunks.extend(_flush_paragraph_buffer(buffer, document_id, source_file, section_title, heading_prefix, config))
            buffer = []
            if region.region_type in _HEADING_REGION_TYPES:
                # New page opens with a heading - discard inherited
                # context entirely and rebuild only from this page's
                # own headings (requirement 4).
                heading_stack = []
            # else: new page opens with body content - heading_stack is
            # left exactly as the previous page ended it (requirement 5:
            # inherit the complete label and continue under it).
        current_page = validated.page_number

        if region.region_type in _HEADING_REGION_TYPES:
            # A heading always closes out whatever body content was
            # buffered under the CURRENT (not-yet-updated) stack - the
            # stack itself only changes once this run completes.
            section_title = _compose_section_title(heading_stack)
            heading_prefix = _compose_heading_prefix(heading_stack)
            chunks.extend(_flush_paragraph_buffer(buffer, document_id, source_file, section_title, heading_prefix, config))
            buffer = []
            pending_run.append(validated.final_text.strip())
            continue

        # Reaching any non-heading region means the current run (if any)
        # is complete - apply it to the stack BEFORE this region's
        # content gets attributed to it.
        _apply_pending_run()

        if region.region_type in _STRUCTURAL_REGION_TYPES:
            section_title = _compose_section_title(heading_stack)
            heading_prefix = _compose_heading_prefix(heading_stack)
            chunks.extend(_flush_paragraph_buffer(buffer, document_id, source_file, section_title, heading_prefix, config))
            buffer = []
            if region.region_type == "table":
                chunks.extend(_chunk_table(region, validated, document_id, source_file, config))
            else:  # figure
                chunks.append(_build_chunk(
                    validated.final_text, document_id, source_file, [validated.page_number],
                    section_title, "figure", validated.extraction_method_used,
                    validated.confidence, validated.validation_status, [region.region_id],
                    image_reference={"region_id": region.region_id},
                ))
            continue
        

        # Mergeable paragraph-like content (text, list_item, caption,
        # footnote, formula, code, header, footer, unknown).
        if validated.page_number in [21, 28, 32, 34]:
            print("=" * 80)
            print(f"Chunker Page: {validated.page_number}")
            print(f"Region: {region.region_id}")
            print(f"Type: {region.region_type}")
            print(f"Method: {validated.extraction_method_used}")
            print("Text entering chunker:")
            print(validated.final_text)
            print("=" * 80)


        buffer.append(_BufferedRegion(
            text=validated.final_text, page_number=validated.page_number,
            region_id=region.region_id, extraction_method=validated.extraction_method_used,
            confidence=validated.confidence, validation_status=validated.validation_status,
            layout_type=region.region_type,
        ))

    _apply_pending_run()  # flush any trailing run (document ended on headings)
    section_title = _compose_section_title(heading_stack)
    heading_prefix = _compose_heading_prefix(heading_stack)
    chunks.extend(_flush_paragraph_buffer(buffer, document_id, source_file, section_title, heading_prefix, config))
    return chunks, unrecoverable
