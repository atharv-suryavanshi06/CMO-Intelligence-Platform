#!/usr/bin/env python3
"""
Package entry point for the enterprise document ingestion pipeline.

Usage:
    python -m multimodal_rag.cli.ingest                           # process every PDF
    python -m multimodal_rag.cli.ingest runtime-data/input/my_report.pdf  # process one PDF
    python -m multimodal_rag.cli.ingest --output-dir custom_out   # write elsewhere
    python -m multimodal_rag.cli.ingest --config-dir custom_cfg   # use other config

See README.md for full setup instructions.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from multimodal_rag.ingestion.analysis.page_preanalyzer import PreAnalyzerConfig
from multimodal_rag.ingestion.formats import (
    SUPPORTED_INGESTION_EXTENSIONS,
    SUPPORTED_INGESTION_FORMAT_LABEL,
    file_extension,
)

from multimodal_rag.ingestion.pipeline.orchestrator import IngestionError, OrchestratorConfig, ingest_document

_CLI_ACTIVE_INGESTION_EXTENSIONS = frozenset({".pdf"})
from multimodal_rag.paths import (
    CONFIG_DIR,
    INGESTION_ARTIFACTS_DIR,
    INPUT_DIR,
    LEGACY_INPUT_DIR,
    LEGACY_LOGS_DIR,
    LEGACY_OUTPUT_DIR,
    LOGS_DIR,
    prefer_new_path,
)

DEFAULT_INPUT_DIR = prefer_new_path(INPUT_DIR, LEGACY_INPUT_DIR)
DEFAULT_OUTPUT_DIR = prefer_new_path(INGESTION_ARTIFACTS_DIR, LEGACY_OUTPUT_DIR)
DEFAULT_LOGS_DIR = prefer_new_path(LOGS_DIR, LEGACY_LOGS_DIR)
DEFAULT_CONFIG_DIR = CONFIG_DIR


def setup_logging(logs_dir: Path) -> Path:
    """Configure logging to both console and a timestamped file under
    runtime-data/logs/. Every module in the pipeline already uses `logging.getLogger(
    __name__)`, so this single basicConfig call is all that's needed to
    capture output from every stage."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / f"ingestion_{datetime.now():%Y%m%d_%H%M%S}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return log_file


def load_config(config_dir: Path) -> OrchestratorConfig:
    """
    Build the pipeline config, loading calibrated values where available.

    Currently only PreAnalyzerConfig supports load/save (see
    page_preanalyzer.py) - it's the one config with thresholds meant to
    be tuned against real documents during the calibration phase agreed
    on earlier. If config/preanalyzer_config.json doesn't exist yet,
    PreAnalyzerConfig.load() already degrades gracefully to defaults
    (logs a warning, doesn't fail) - this is intentional, not a gap:
    the project is fully runnable before calibration ever happens.
    Every other sub-config uses its built-in defaults, which were chosen
    to be reasonable out-of-the-box rather than requiring tuning first.
    """
    preanalyzer_config_path = config_dir / "preanalyzer_config.json"
    preanalyzer = PreAnalyzerConfig.load(preanalyzer_config_path)
    return OrchestratorConfig(preanalyzer=preanalyzer)


def process_one(pdf_path: Path, output_dir: Path, config: OrchestratorConfig, logger: logging.Logger) -> bool:
    logger.info("Processing: %s", pdf_path)
    try:
        paths = ingest_document(pdf_path, output_dir, config)
    except IngestionError as e:
        logger.error("FAILED to ingest '%s': %s", pdf_path.name, e)
        print(f"\n[FAILED] {pdf_path.name}: {e}\n")
        return False

    print(f"\n[OK] {pdf_path.name}")
    print(f"     Output directory : {paths.document_dir}")
    print(f"     Raw extraction   : {paths.raw_dir}")
    print(f"     Chunks           : {paths.chunks_json}")
    print(f"     Metadata         : {paths.metadata_json}")
    print(f"     Validation report: {paths.validation_report_json}")
    print(f"     Audit (markdown) : {paths.audit_markdown}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the document ingestion pipeline.")
    parser.add_argument(
        "pdf_path", nargs="?", default=None,
        help=f"Path to a single supported file ({SUPPORTED_INGESTION_FORMAT_LABEL}). If omitted, active PDFs in the runtime-data/input/ directory are processed.",
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help=f"Where output folders are written (default: {DEFAULT_OUTPUT_DIR})")
    parser.add_argument("--input-dir", default=str(DEFAULT_INPUT_DIR), help=f"Where to look for PDFs when no path is given (default: {DEFAULT_INPUT_DIR})")
    parser.add_argument("--config-dir", default=str(DEFAULT_CONFIG_DIR), help="Where calibrated config JSON files live (default: config/)")
    parser.add_argument("--logs-dir", default=str(DEFAULT_LOGS_DIR), help=f"Where log files are written (default: {DEFAULT_LOGS_DIR})")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    input_dir = Path(args.input_dir)
    config_dir = Path(args.config_dir)
    logs_dir = Path(args.logs_dir)

    log_file = setup_logging(logs_dir)
    logger = logging.getLogger("main")
    logger.info("Logging to %s", log_file)

    config = load_config(config_dir)

    if args.pdf_path:
        targets = [Path(args.pdf_path)]
        if not targets[0].exists():
            print(f"Error: '{args.pdf_path}' does not exist.")
            return 1
        extension = file_extension(targets[0])
        if extension not in SUPPORTED_INGESTION_EXTENSIONS:
            print(f"Error: unsupported file format '{extension or '<none>'}'. Allowed formats: {SUPPORTED_INGESTION_FORMAT_LABEL}.")
            return 1
        if extension not in _CLI_ACTIVE_INGESTION_EXTENSIONS:
            print(f"Error: '{targets[0].name}' is an allowed format, but its ingestion pipeline is not available yet. PDF is currently supported.")
            return 1
    else:
        input_dir.mkdir(parents=True, exist_ok=True)
        targets = sorted(
            path for path in input_dir.iterdir()
            if path.is_file() and file_extension(path) in _CLI_ACTIVE_INGESTION_EXTENSIONS
        )
        if not targets:
            print(f"No active PDFs found in '{input_dir}'. Place a PDF there, or pass a supported path directly:")
            print("    python -m multimodal_rag.cli.ingest path/to/your_file.pdf")
            return 0

    logger.info("Found %d PDF(s) to process", len(targets))
    output_dir.mkdir(parents=True, exist_ok=True)

    results = [process_one(pdf, output_dir, config, logger) for pdf in targets]

    succeeded = sum(results)
    failed = len(results) - succeeded
    print(f"\n{'=' * 50}")
    print(f"Done: {succeeded} succeeded, {failed} failed, out of {len(results)} total.")
    print(f"Full log: {log_file}")
    print(f"{'=' * 50}")

    return 1 if failed and not succeeded else 0


if __name__ == "__main__":
    sys.exit(main())
