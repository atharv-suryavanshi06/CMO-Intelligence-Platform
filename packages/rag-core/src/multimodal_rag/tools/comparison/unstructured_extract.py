#!/usr/bin/env python3
"""
unstructured_extract.py — Standalone benchmark script for Unstructured
(https://github.com/Unstructured-IO/unstructured), for comparison against
this project's own Docling-based ingestion pipeline.

IMPORTANT: This script is 100% independent. It does NOT import, call, or
modify anything in the `ingestion/` package. It is safe to run without
touching the main project's outputs.

Usage:
    pip install "unstructured[pdf]"
    python -m multimodal_rag.tools.comparison.unstructured_extract path/to/document.pdf
    python -m multimodal_rag.tools.comparison.unstructured_extract path/to/document.pdf --strategy hi_res
    python -m multimodal_rag.tools.comparison.unstructured_extract path/to/document.pdf --output-dir runtime-data/comparisons/unstructured

Output (under runtime-data/comparisons/unstructured/<pdf_stem>/):
    - extracted_text.md      All elements concatenated in document order,
                              with tables rendered as HTML inline (Unstructured's
                              own table representation) and element category
                              labels shown for easy comparison
    - elements.json           Full structured element list (Unstructured's
                              native JSON via elements_to_json) - includes
                              per-element type, text, and metadata
    - tables/                 Each detected table's HTML, saved separately
    - images/                 Extracted images (only populated with
                              --strategy hi_res, which is the strategy that
                              supports image block extraction)

NOTE ON TABLES (confirmed via a real test run, not assumed): with
strategy="fast", `infer_table_structure=True` does NOT actually produce
table structure - "fast" only runs pdfminer's text-position extraction,
with no layout/table detection model in the loop, so no element is ever
categorized as "Table" in fast mode. Real table detection requires
strategy="hi_res" (which runs an actual layout/table detection model).
This script still passes infer_table_structure=True unconditionally
since it's harmless with "fast" and required for "hi_res" - but if you
run with the default "fast" strategy, expect `tables_detected: 0` in
metadata.json even on a PDF that clearly has tables. Use --strategy
hi_res for real table extraction.

NETWORK NOTE (confirmed, not assumed): the default/"fast" strategy's
paragraph and title detection uses spaCy's en_core_web_sm model, which
Unstructured downloads from a GitHub release asset on first use. In
network-restricted environments (this script was developed in a sandbox
where that specific signed download URL returned 403 - confirmed via a
real failed run, not guessed), text extraction is skipped entirely rather
than degraded. The "hi_res" strategy additionally requires a layout
detection model, typically also fetched from the network on first use.
Both will work normally in any environment with regular internet access.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from multimodal_rag.paths import LEGACY_UNSTRUCTURED_COMPARISON_DIR, UNSTRUCTURED_COMPARISON_DIR, prefer_new_path


def run(pdf_path: Path, output_dir: Path, strategy: str) -> None:
    try:
        from unstructured.partition.pdf import partition_pdf
        from unstructured.staging.base import elements_to_json
    except ImportError as e:
        print(f"ERROR: unstructured is not installed. Run: pip install \"unstructured[pdf]\"\n  ({e})")
        sys.exit(1)

    doc_output_dir = output_dir / pdf_path.stem
    images_dir = doc_output_dir / "images"
    tables_dir = doc_output_dir / "tables"
    doc_output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[unstructured] Partitioning '{pdf_path.name}' with strategy='{strategy}'...")
    t0 = time.perf_counter()
    try:
        elements = partition_pdf(
            filename=str(pdf_path),
            strategy=strategy,
            infer_table_structure=True,
            extract_images_in_pdf=(strategy == "hi_res"),
            extract_image_block_output_dir=str(images_dir) if strategy == "hi_res" else None,
        )
    except Exception as e:
        print(f"\n[unstructured] FAILED: {e}")
        print(
            "[unstructured] This is expected in network-restricted environments - the "
            "'fast' strategy needs to download a spaCy model (en_core_web_sm) from a "
            "GitHub release asset on first use, and 'hi_res' additionally needs a layout "
            "detection model. Run this script in an environment with normal internet "
            "access; models are cached locally afterward."
        )
        sys.exit(1)
    elapsed = time.perf_counter() - t0

    if not elements:
        print(
            "[unstructured] Partitioning returned zero elements. This usually means a "
            "required model failed to download (see NETWORK NOTE in this script's "
            "docstring) - check the log output above for a download/network error."
        )
        sys.exit(1)

    # --- extracted_text.md: reading-order reconstruction, tables as HTML ---
    lines = [f"# Unstructured extraction: {pdf_path.name}", f"*(strategy={strategy})*", ""]
    table_count = 0
    for el in elements:
        category = getattr(el, "category", type(el).__name__)
        page = el.metadata.page_number if el.metadata else None
        lines.append(f"\n<!-- [{category}] page={page} -->")
        if category == "Table" and el.metadata and el.metadata.text_as_html:
            table_count += 1
            lines.append(el.metadata.text_as_html)
        else:
            lines.append(str(el))

    (doc_output_dir / "extracted_text.md").write_text("\n".join(lines))

    # --- elements.json: full native structured output ---
    (doc_output_dir / "elements.json").write_text(elements_to_json(elements))

    # --- tables/: each table's HTML saved separately ---
    if table_count:
        tables_dir.mkdir(parents=True, exist_ok=True)
        idx = 0
        for el in elements:
            if getattr(el, "category", None) == "Table" and el.metadata and el.metadata.text_as_html:
                idx += 1
                page = el.metadata.page_number or "unknown"
                (tables_dir / f"table_{idx}_page{page}.html").write_text(el.metadata.text_as_html)

    # --- metadata.json: run-level summary ---
    category_counts: dict[str, int] = {}
    for el in elements:
        cat = getattr(el, "category", type(el).__name__)
        category_counts[cat] = category_counts.get(cat, 0) + 1

    metadata = {
        "source_file": pdf_path.name,
        "tool": "unstructured",
        "strategy": strategy,
        "processing_time_seconds": round(elapsed, 2),
        "total_elements": len(elements),
        "element_category_counts": category_counts,
        "tables_detected": table_count,
    }
    (doc_output_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, default=str))

    print(f"[unstructured] Done in {elapsed:.1f}s. Output written to: {doc_output_dir}")
    print(f"  - {doc_output_dir / 'extracted_text.md'}")
    print(f"  - {doc_output_dir / 'elements.json'}")
    print(f"  - {doc_output_dir / 'metadata.json'}")
    if table_count:
        print(f"  - {tables_dir} ({table_count} table(s))")
    if strategy == "hi_res":
        print(f"  - {images_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone Unstructured extraction benchmark.")
    parser.add_argument("pdf_path", help="Path to the PDF to process.")
    parser.add_argument(
        "--strategy", default="fast", choices=["fast", "hi_res", "ocr_only", "auto"],
        help="Unstructured partitioning strategy (default: fast). 'hi_res' gives better "
             "layout/table fidelity but is slower and needs a layout detection model.",
    )
    parser.add_argument(
        "--output-dir", default=str(prefer_new_path(UNSTRUCTURED_COMPARISON_DIR, LEGACY_UNSTRUCTURED_COMPARISON_DIR)),
        help=f"Base output directory (default: {prefer_new_path(UNSTRUCTURED_COMPARISON_DIR, LEGACY_UNSTRUCTURED_COMPARISON_DIR)})",
    )
    args = parser.parse_args()

    pdf_path = Path(args.pdf_path)
    if not pdf_path.exists():
        print(f"ERROR: '{pdf_path}' does not exist.")
        sys.exit(1)

    run(pdf_path, Path(args.output_dir), args.strategy)


if __name__ == "__main__":
    main()
