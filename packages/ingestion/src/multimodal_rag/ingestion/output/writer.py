"""
Output Writer Module
=======================

Final pipeline stage: writes all per-document outputs to disk. Per the
locked requirements (S4), every processed document must produce:

- chunks.json: the actual chunk text + metadata, ready for embedding/
  ChromaDB ingestion (this is what feeds the existing RAG stack).
- metadata.json: document-level summary + a lightweight per-chunk
  metadata index (no chunk text repeated here, to keep it small).
- validation_report.json: the FULL per-region validation record -
  every region that was processed, whether it became a chunk or not,
  with its status/confidence/failure_reason. This is deliberately built
  from the complete ordered region list, not from the chunks - a region
  that failed and produced no chunk must still be visible here.
- extracted_text_audit.md: a human-readable, reading-order
  reconstruction of the ENTIRE document (including failed/skipped
  regions, clearly marked, not hidden) so it can be visually compared
  against the source PDF to spot reading-order, layout, table, OCR,
  image, and header/footer problems - this is the direct answer to the
  locked requirement that the audit "allow me to compare the
  reconstructed document against the original PDF."

Also writes structured table JSON files to a `tables/` subfolder (one
per table region with successfully structured data), so `table_reference.
structured_path` in the metadata schema points at something real.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from multimodal_rag.ingestion.analysis.layout_segmenter import Region
from multimodal_rag.ingestion.processing.chunker import Chunk, PIPELINE_VERSION
from multimodal_rag.ingestion.processing.validator import ValidatedRegionResult

logger = logging.getLogger(__name__)


@dataclass
class OutputWriterConfig:
    write_structured_tables: bool = True


@dataclass
class DocumentOutputPaths:
    document_dir: Path
    chunks_json: Path
    metadata_json: Path
    validation_report_json: Path
    audit_markdown: Path
    tables_dir: Path | None
    raw_dir: Path | None = None
    # Populated by the orchestrator after write_raw_extraction() runs -
    # not set by write_document_output() itself, since raw extraction
    # happens earlier in the pipeline (see raw_writer.py) and this
    # dataclass is just the return-value container for all output paths.


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# chunks.json
# --------------------------------------------------------------------------

def _write_chunks_json(chunks: list[Chunk], path: Path) -> None:
    data = [
        {"chunk_text": c.chunk_text, "metadata": asdict(c.metadata)}
        for c in chunks
    ]
    path.write_text(json.dumps(data, indent=2, default=str))


# --------------------------------------------------------------------------
# metadata.json
# --------------------------------------------------------------------------

def _write_metadata_json(
    document_id: str, source_file: str, page_count: int,
    chunks: list[Chunk], unrecoverable: list[ValidatedRegionResult],
    processing_time_seconds: float, path: Path,
) -> None:
    status_counts: dict[str, int] = {}
    for c in chunks:
        status_counts[c.metadata.validation_status] = status_counts.get(c.metadata.validation_status, 0) + 1

    summary = {
        "document_id": document_id,
        "source_file": source_file,
        "page_count": page_count,
        "pipeline_version": PIPELINE_VERSION,
        "ingestion_timestamp": _now_iso(),
        "processing_time_seconds": round(processing_time_seconds, 3),
        "total_chunks": len(chunks),
        "chunks_by_validation_status": status_counts,
        "total_unrecoverable_regions": len(unrecoverable),
        "chunk_metadata_index": [asdict(c.metadata) for c in chunks],
    }
    path.write_text(json.dumps(summary, indent=2, default=str))


# --------------------------------------------------------------------------
# validation_report.json
# --------------------------------------------------------------------------

def _write_validation_report_json(
    ordered_pairs: list[tuple[Region, ValidatedRegionResult]], path: Path
) -> None:
    """
    Built from the COMPLETE ordered region list, not from chunks/
    unrecoverable alone - this is the single source of truth for "what
    happened to every region in this document", regardless of whether it
    ended up in a chunk.
    """
    records = []
    status_totals: dict[str, int] = {}
    for region, validated in ordered_pairs:
        status_totals[validated.validation_status] = status_totals.get(validated.validation_status, 0) + 1
        records.append({
            "region_id": region.region_id,
            "page_number": validated.page_number,
            "region_type": region.region_type,
            "raw_label": region.raw_label,
            "reading_order": region.reading_order,
            "validation_status": validated.validation_status,
            "failure_reason": validated.failure_reason,
            "extraction_method_used": validated.extraction_method_used,
            "attempted_methods": validated.attempted_methods,
            "confidence": validated.confidence,
            "is_handwritten": region.is_handwritten,
            "notes": validated.notes,
        })

    report = {
        "total_regions": len(records),
        "status_totals": status_totals,
        "regions": records,
    }
    path.write_text(json.dumps(report, indent=2, default=str))


# --------------------------------------------------------------------------
# extracted_text_audit.md
# --------------------------------------------------------------------------

_STATUS_MARKERS = {
    "ok": "✅",
    "low_confidence": "⚠️ LOW CONFIDENCE",
    "failed": "❌ FAILED",
}


def _format_region_for_audit(region: Region, validated: ValidatedRegionResult) -> str:
    marker = _STATUS_MARKERS.get(validated.validation_status, validated.validation_status)
    lines = [
        f"### Page {validated.page_number} — `{region.region_type}` ({region.raw_label}) — {marker}",
        f"*region_id: `{region.region_id}` | extraction_method: `{validated.extraction_method_used}`"
        + (f" | confidence: {validated.confidence:.2f}" if validated.confidence is not None else "")
        + "*",
    ]
    if validated.failure_reason:
        lines.append(f"> **Failure reason:** {validated.failure_reason}")
    if region.is_handwritten:
        lines.append("> ⚠️ Region flagged as handwritten text - treat with extra caution.")
    if validated.notes:
        lines.append(f"> Notes: {'; '.join(validated.notes)}")

    if region.region_type == "figure" and region.image_path:
        lines.append(f"\n![figure]({region.image_path})")

    content = validated.final_text
    if content and content.strip():
        lines.append("")
        lines.append(content if region.region_type != "table" else content)
    else:
        lines.append("\n*[No text recovered for this region]*")

    lines.append("\n---\n")
    return "\n".join(lines)


def _write_audit_markdown(
    ordered_pairs: list[tuple[Region, ValidatedRegionResult]],
    document_id: str, source_file: str, page_count: int, path: Path,
) -> None:
    status_counts: dict[str, int] = {}
    for _, v in ordered_pairs:
        status_counts[v.validation_status] = status_counts.get(v.validation_status, 0) + 1

    header = [
        f"# Extraction Audit: {source_file}",
        f"",
        f"- **Document ID:** {document_id}",
        f"- **Pages:** {page_count}",
        f"- **Total regions processed:** {len(ordered_pairs)}",
        f"- **Status breakdown:** " + ", ".join(f"{k}: {v}" for k, v in status_counts.items()),
        f"",
        "This document reconstructs the extraction pipeline's output in reading order, "
        "including regions that failed or were low-confidence (clearly marked), so it can "
        "be compared directly against the original PDF to spot reading-order, layout, table, "
        "OCR, image, and header/footer problems.",
        "",
        "---",
        "",
    ]

    body = [_format_region_for_audit(region, validated) for region, validated in ordered_pairs]
    path.write_text("\n".join(header) + "\n".join(body), encoding="utf-8")

# --------------------------------------------------------------------------
# Structured table JSON files
# --------------------------------------------------------------------------

def _write_structured_tables(
    ordered_pairs: list[tuple[Region, ValidatedRegionResult]], tables_dir: Path
) -> dict[str, str]:
    """Writes one JSON file per successfully-structured table region.
    Returns a mapping of region_id -> written file path, so callers can
    backfill `table_reference.structured_path` in chunk metadata if desired."""
    written: dict[str, str] = {}
    for region, validated in ordered_pairs:
        if region.region_type != "table" or validated.table_data is None:
            continue
        tables_dir.mkdir(parents=True, exist_ok=True)
        out_path = tables_dir / f"{region.region_id}.json"
        out_path.write_text(json.dumps({
            "region_id": region.region_id,
            "page_number": region.page_number,
            "num_rows": validated.table_data.num_rows,
            "num_cols": validated.table_data.num_cols,
            "rows": validated.table_data.rows,
        }, indent=2))
        written[region.region_id] = str(out_path)
    return written


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------

def write_document_output(
    document_id: str,
    source_file: str,
    output_dir: str | Path,
    chunks: list[Chunk],
    unrecoverable: list[ValidatedRegionResult],
    ordered_pairs: list[tuple[Region, ValidatedRegionResult]],
    page_count: int,
    processing_time_seconds: float,
    config: OutputWriterConfig | None = None,
) -> DocumentOutputPaths:
    """
    Write every required output file for one document. All four required
    files (chunks.json, metadata.json, validation_report.json,
    extracted_text_audit.md) are always written, even if the document had
    significant extraction problems - a document that failed badly should
    produce a very visible audit trail, not silently produce nothing.
    """
    config = config or OutputWriterConfig()
    document_dir = Path(output_dir) / document_id
    document_dir.mkdir(parents=True, exist_ok=True)

    chunks_path = document_dir / "chunks.json"
    metadata_path = document_dir / "metadata.json"
    validation_path = document_dir / "validation_report.json"
    audit_path = document_dir / "extracted_text_audit.md"
    tables_dir = document_dir / "tables"

    _write_chunks_json(chunks, chunks_path)
    _write_metadata_json(
        document_id, source_file, page_count, chunks, unrecoverable,
        processing_time_seconds, metadata_path,
    )
    _write_validation_report_json(ordered_pairs, validation_path)
    _write_audit_markdown(ordered_pairs, document_id, source_file, page_count, audit_path)

    tables_written = None
    if config.write_structured_tables:
        tables_written = _write_structured_tables(ordered_pairs, tables_dir)
        if not tables_written:
            tables_dir = None  # no tables directory created if nothing to write

    logger.info(
        "Wrote output for document '%s': %d chunks, %d unrecoverable regions, %s",
        document_id, len(chunks), len(unrecoverable),
        f"{len(tables_written)} structured tables" if tables_written else "no tables",
    )

    return DocumentOutputPaths(
        document_dir=document_dir,
        chunks_json=chunks_path,
        metadata_json=metadata_path,
        validation_report_json=validation_path,
        audit_markdown=audit_path,
        tables_dir=tables_dir if tables_written else None,
    )
