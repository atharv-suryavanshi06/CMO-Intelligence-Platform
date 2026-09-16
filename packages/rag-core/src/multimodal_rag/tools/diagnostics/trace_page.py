#!/usr/bin/env python3
"""
trace_page.py - DIAGNOSTIC ONLY. Does not modify any pipeline module.

Replicates orchestrator.ingest_document()'s exact stage sequence, up
through (but not including) chunk_document(), and prints every
heading-related object for ONE page - so we can see precisely which
stage first loses a given heading string, instead of guessing.

Usage:
    python -m multimodal_rag.tools.diagnostics.trace_page path/to/document.pdf --page 6 --search "Short-term implications"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from multimodal_rag.ingestion.analysis.layout_segmenter import segment_document
from multimodal_rag.ingestion.analysis.page_preanalyzer import analyze_document
from multimodal_rag.ingestion.extractors.vision_describer import (
    DecorativeImageRegistry,
    compute_average_hash,
    process_figure_region,
)
from multimodal_rag.ingestion.loaders.pdf_loader import load_pdf
from multimodal_rag.ingestion.pipeline.orchestrator import OrchestratorConfig, _build_figure_images, _is_native_text_trusted
from multimodal_rag.ingestion.processing.validator import validate_figure_region, validate_region


def _hit(label: str, text: str | None, search: str | None) -> None:
    if text is None:
        print(f"  {label}: None")
        return
    marker = ""
    if search:
        marker = " <-- FOUND" if search in text else " <-- NOT FOUND"
    print(f"  {label} ({len(text)} chars){marker}:\n    {text!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Trace one page through the pipeline (diagnostic only, no code changes).")
    parser.add_argument("pdf_path")
    parser.add_argument("--page", type=int, required=True)
    parser.add_argument("--search", default=None, help="Substring to check for at each stage, e.g. 'Short-term implications'")
    args = parser.parse_args()

    pdf_path = Path(args.pdf_path)
    search = args.search

    # --- STAGE 1: raw_text (Module 1, PyMuPDF) ---
    print(f"=== STAGE 1: raw_text (Module 1 / PyMuPDF) for page {args.page} ===")
    raw_doc = load_pdf(pdf_path)
    raw_page = next((p for p in raw_doc.pages if p.page_number == args.page), None)
    if raw_page is None:
        print(f"ERROR: page {args.page} not found (document has {raw_doc.page_count} pages)")
        return
    _hit("raw_page.raw_text", raw_page.raw_text, search)

    # --- STAGE 2: Page Pre-Analyzer (Module 2) ---
    print(f"\n=== STAGE 2: Page Pre-Analyzer (Module 2) for page {args.page} ===")
    page_analyses = analyze_document(raw_doc.pages)
    pa = next(p for p in page_analyses if p.page_number == args.page)
    print(f"  is_blank={pa.is_blank}  is_scanned_candidate={pa.is_scanned_candidate}  "
          f"has_broken_font_suspect={pa.has_broken_font_suspect}  "
          f"is_multi_column_candidate={pa.is_multi_column_candidate}")
    native_trusted = _is_native_text_trusted(pa)
    print(f"  -> native_text_is_trusted for this page: {native_trusted}")

    # --- STAGE 3: Layout Segmentation (Module 3, REAL Docling) ---
    print(f"\n=== STAGE 3: Layout Segmentation (Module 3 / Docling) - all regions on page {args.page} ===")
    config = OrchestratorConfig()
    all_regions = segment_document(str(pdf_path), config.layout)
    page_regions = [r for r in all_regions if r.page_number == args.page]
    print(f"  Total regions on page {args.page}: {len(page_regions)}\n")
    for r in page_regions:
        print(f"  region_id={r.region_id}")
        print(f"    region_type={r.region_type!r}  raw_label={r.raw_label!r}  reading_order={r.reading_order}")
        print(f"    bbox={r.bbox}  is_handwritten={r.is_handwritten}  image_path={r.image_path}")
        _hit("    text_content (raw, pre-validation)", r.text_content, search)
        if r.table_data:
            print(f"    table_data: {r.table_data.num_rows}x{r.table_data.num_cols}")
        print()

    # --- STAGE 4-7: Validation (per region, same call sequence as orchestrator) ---
    print(f"=== STAGE 4-7: Validation - per region, page {args.page} ===")
    figure_images = _build_figure_images(page_regions, str(pdf_path), config)
    registry = DecorativeImageRegistry(config.vision)
    for r in page_regions:
        if r.region_type == "figure" and r.region_id in figure_images:
            registry.add(r.region_id, r.page_number, compute_average_hash(figure_images[r.region_id]))

    ordered_pairs = []
    for r in page_regions:
        if r.region_type == "figure":
            if r.region_id not in figure_images:
                print(f"  [{r.region_id}] figure: NO IMAGE AVAILABLE -> will be marked failed, skipping")
                continue
            figure_result = process_figure_region(r.region_id, figure_images[r.region_id], registry, config.vision, config.ocr)
            validated = validate_figure_region(figure_result)
            validated.page_number = r.page_number
        else:
            validated = validate_region(r, str(pdf_path), native_trusted, config.validation, config.ocr, config.cleaning)
        ordered_pairs.append((r, validated))

        print(f"  [{r.region_id}] region_type={r.region_type!r}  raw_label={r.raw_label!r}")
        print(f"    validation_status={validated.validation_status}  extraction_method_used={validated.extraction_method_used}")
        _hit("    validated.final_text", validated.final_text, search)
        print()

    # --- STAGE 8 INPUT: exact list passed into chunk_document() ---
    print(f"=== Exact ordered_pairs that would be passed into chunk_document() for page {args.page} ===")
    for r, v in ordered_pairs:
        preview = (v.final_text or "")[:60]
        print(f"  region_type={r.region_type!r:12} raw_label={r.raw_label!r:20} "
              f"validation_status={v.validation_status!r:16} final_text_preview={preview!r}")


if __name__ == "__main__":
    main()
