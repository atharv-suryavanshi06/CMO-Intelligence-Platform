#!/usr/bin/env python3
"""
marker_extract.py — Standalone benchmark script for Marker
(https://github.com/datalab-to/marker), for comparison against this
project's own Docling-based ingestion pipeline.

IMPORTANT: This script is 100% independent. It does NOT import, call, or
modify anything in the `ingestion/` package. It is safe to run without
touching the main project's outputs.

Usage:
    pip install marker-pdf
    python -m multimodal_rag.tools.comparison.marker_extract path/to/document.pdf
    python -m multimodal_rag.tools.comparison.marker_extract path/to/document.pdf --output-dir runtime-data/comparisons/marker

Output (under runtime-data/comparisons/marker/<pdf_stem>/):
    - <pdf_stem>.md          Full Markdown output (Marker's native format;
                              tables are rendered inline as Markdown tables,
                              Marker does not produce a separate tables file)
    - metadata.json          Marker's own per-document metadata
    - images/                Every image Marker extracted, saved to disk

NETWORK NOTE (confirmed, not assumed): Marker downloads its layout/OCR/
table model weights from `models.datalab.to` on first use. In network-
restricted environments (this script was developed in a sandbox where
that host is not reachable - confirmed via a real 403 from
`create_model_dict()`, not guessed), this script will fail at the model-
loading step with a clear error. It will work normally in any environment
with regular internet access; models are cached locally after first run.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from multimodal_rag.paths import LEGACY_MARKER_COMPARISON_DIR, MARKER_COMPARISON_DIR, prefer_new_path


def run(pdf_path: Path, output_dir: Path) -> None:
    try:
        from marker.config.parser import ConfigParser
        from marker.converters.pdf import PdfConverter
        from marker.models import create_model_dict
        from marker.output import text_from_rendered
    except ImportError as e:
        print(f"ERROR: marker-pdf is not installed. Run: pip install marker-pdf\n  ({e})")
        sys.exit(1)

    doc_output_dir = output_dir / pdf_path.stem
    images_dir = doc_output_dir / "images"
    doc_output_dir.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(parents=True, exist_ok=True)

    print(f"[marker] Loading models (downloads on first run from models.datalab.to)...")
    t0 = time.perf_counter()
    try:
        config_parser = ConfigParser({"output_format": "markdown"})
        artifact_dict = create_model_dict()
    except Exception as e:
        print(f"\n[marker] FAILED to load models: {e}")
        print(
            "[marker] This is expected in network-restricted environments - Marker "
            "requires reaching models.datalab.to on first run to download its layout/"
            "OCR/table model weights. Run this script in an environment with normal "
            "internet access; models are cached locally afterward."
        )
        sys.exit(1)
    load_time = time.perf_counter() - t0
    print(f"[marker] Models loaded in {load_time:.1f}s")

    print(f"[marker] Converting '{pdf_path.name}'...")
    t0 = time.perf_counter()
    converter = PdfConverter(
        artifact_dict=artifact_dict,
        config=config_parser.generate_config_dict(),
    )
    rendered = converter(str(pdf_path))
    convert_time = time.perf_counter() - t0

    markdown_text, extracted_metadata, images = text_from_rendered(rendered)

    md_path = doc_output_dir / f"{pdf_path.stem}.md"
    md_path.write_text(markdown_text, encoding="utf-8")

    for image_name, image_obj in (images or {}).items():
        try:
            image_obj.save(images_dir / image_name)
        except Exception as e:
            print(f"[marker] WARNING: could not save image '{image_name}': {e}")

    metadata = {
        "source_file": pdf_path.name,
        "tool": "marker-pdf",
        "model_load_time_seconds": round(load_time, 2),
        "conversion_time_seconds": round(convert_time, 2),
        "markdown_char_count": len(markdown_text),
        "image_count": len(images or {}),
        "marker_metadata": extracted_metadata,
    }
    (doc_output_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, default=str))

    print(f"[marker] Done in {convert_time:.1f}s. Output written to: {doc_output_dir}")
    print(f"  - {md_path}")
    print(f"  - {doc_output_dir / 'metadata.json'}")
    print(f"  - {images_dir} ({len(images or {})} image(s))")


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone Marker extraction benchmark.")
    parser.add_argument("pdf_path", help="Path to the PDF to process.")
    parser.add_argument(
        "--output-dir", default=str(prefer_new_path(MARKER_COMPARISON_DIR, LEGACY_MARKER_COMPARISON_DIR)),
        help=f"Base output directory (default: {prefer_new_path(MARKER_COMPARISON_DIR, LEGACY_MARKER_COMPARISON_DIR)})",
    )
    args = parser.parse_args()

    pdf_path = Path(args.pdf_path)
    if not pdf_path.exists():
        print(f"ERROR: '{pdf_path}' does not exist.")
        sys.exit(1)

    run(pdf_path, Path(args.output_dir))


if __name__ == "__main__":
    main()
