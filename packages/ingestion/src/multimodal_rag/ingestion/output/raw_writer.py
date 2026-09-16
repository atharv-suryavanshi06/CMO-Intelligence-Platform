"""
Raw Extraction Writer
========================

Runs immediately after Module 3 (Layout Segmentation) and BEFORE any
cleaning, OCR fallback, vision description, validation, or chunking.
Saves the direct, unmodified output of the extraction libraries (Docling)
so the raw extraction can be compared against the source PDF and
debugged independently of everything the pipeline does to it afterward.

Per the explicit requirement: this captures exactly what Docling
produced - native text_content as extracted, table grids/markdown as
structured, region geometry - with zero cleaning, zero OCR correction,
zero fallback logic applied. The cleaned pipeline (Modules 4-9) continues
to run on the SAME region objects afterward; this stage only writes a
snapshot, it does not fork or alter the data flowing through the rest of
the pipeline.

Output (written to <output_dir>/<document_id>/raw/, kept separate from
Module 9's final output files to avoid any filename collision):
- raw_text.txt: all regions' raw text, in reading order, grouped by page,
  exactly as Docling produced it (no cleaning applied).
- pages.json: every region grouped by page, with raw text/bbox/label.
- tables.json: every table region's raw structured grid + raw markdown,
  exactly as Docling's TableFormer produced it (pre-fallback, pre-cleaning).
- metadata.json: extraction-pass metadata (document id, source file, page
  count, region-type counts, timestamp) - distinct from Module 9's
  metadata.json, which describes the final chunked output.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from multimodal_rag.ingestion.analysis.layout_segmenter import Region

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_raw_text(regions: list[Region], path: Path) -> None:
    lines: list[str] = []
    current_page: int | None = None
    for region in sorted(regions, key=lambda r: (r.page_number, r.reading_order)):
        if region.page_number != current_page:
            current_page = region.page_number
            lines.append(f"\n{'=' * 20} PAGE {current_page} {'=' * 20}\n")
        lines.append(f"[{region.region_type}] ({region.raw_label})")
        if region.text_content:
            lines.append(region.text_content)
        elif region.table_data:
            lines.append(region.table_data.markdown)
        elif region.region_type == "figure":
            lines.append(f"[FIGURE - image saved at: {region.image_path or 'NOT SAVED'}]")
        else:
            lines.append("[NO TEXT CONTENT]")
        lines.append("")
    path.write_text("\n".join(lines),
    encoding="utf-8")


def _write_pages_json(regions: list[Region], path: Path) -> None:
    pages: dict[int, list[dict]] = {}
    for region in sorted(regions, key=lambda r: (r.page_number, r.reading_order)):
        pages.setdefault(region.page_number, []).append({
            "region_id": region.region_id,
            "region_type": region.region_type,
            "raw_label": region.raw_label,
            "reading_order": region.reading_order,
            "bbox": region.bbox,
            "coord_origin": region.coord_origin,
            "is_handwritten": region.is_handwritten,
            "raw_text_content": region.text_content,
            "has_table_data": region.table_data is not None,
            "image_path": region.image_path,
        })
    ordered = {str(k): pages[k] for k in sorted(pages)}
    path.write_text(json.dumps(ordered, indent=2, default=str))


def _write_tables_json(regions: list[Region], path: Path) -> None:
    tables = []
    for region in regions:
        if region.region_type != "table" or region.table_data is None:
            continue
        tables.append({
            "region_id": region.region_id,
            "page_number": region.page_number,
            "num_rows": region.table_data.num_rows,
            "num_cols": region.table_data.num_cols,
            "raw_rows": region.table_data.rows,
            "raw_markdown": region.table_data.markdown,
        })
    path.write_text(json.dumps(tables, indent=2, default=str))


def _write_raw_metadata_json(
    document_id: str, source_file: str, page_count: int, regions: list[Region], path: Path
) -> None:
    type_counts: dict[str, int] = {}
    for r in regions:
        type_counts[r.region_type] = type_counts.get(r.region_type, 0) + 1

    metadata = {
        "document_id": document_id,
        "source_file": source_file,
        "page_count": page_count,
        "extraction_timestamp": _now_iso(),
        "total_regions_extracted": len(regions),
        "region_type_counts": type_counts,
        "note": "This is the RAW extraction snapshot (Docling output only), "
                "captured before any cleaning, OCR fallback, vision description, "
                "or validation. See ../metadata.json for the final chunked output's metadata.",
    }
    path.write_text(json.dumps(metadata, indent=2, default=str))


def write_raw_extraction(
    document_id: str, source_file: str, output_dir: str | Path,
    regions: list[Region], page_count: int,
) -> Path:
    """
    Write the raw extraction snapshot. Returns the raw/ directory path.
    Called once, right after Module 3 produces `regions` and before any
    of those regions are cleaned, OCR-fallback-processed, or validated.
    """
    raw_dir = Path(output_dir) / document_id / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    _write_raw_text(regions, raw_dir / "raw_text.txt")
    _write_pages_json(regions, raw_dir / "pages.json")
    _write_tables_json(regions, raw_dir / "tables.json")
    _write_raw_metadata_json(document_id, source_file, page_count, regions, raw_dir / "metadata.json")

    logger.info("Wrote raw extraction snapshot for '%s' to %s (%d regions)",
                source_file, raw_dir, len(regions))
    return raw_dir
