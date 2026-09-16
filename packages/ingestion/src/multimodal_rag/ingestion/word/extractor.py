"""Extract DOCX text and convert legacy DOC files before extraction."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


class WordExtractionError(RuntimeError):
    """Raised when a Word document cannot be converted or read."""


@dataclass
class WordOutputPaths:
    document_dir: Path
    chunks_json: Path
    metadata_json: Path
    validation_report_json: Path
    audit_markdown: Path
    tables_dir: Path | None = None


def ingest_word(word_path: str | Path, output_dir: str | Path, source_sha256: str | None = None) -> WordOutputPaths:
    """Extract a scanned-safe DOC/DOCX upload into standard text chunks."""
    word_path = Path(word_path)
    started = time.perf_counter()
    if word_path.suffix.lower() == ".doc":
        with tempfile.TemporaryDirectory(prefix="rag-word-") as conversion_dir:
            converted_path = _convert_doc_to_docx(word_path, Path(conversion_dir))
            blocks = _read_docx_blocks(converted_path)
    else:
        blocks = _read_docx_blocks(word_path)
    if not blocks:
        raise WordExtractionError("The Word document contains no extractable text.")

    document_id = f"word_{uuid.uuid4().hex[:12]}"
    chunks = _build_chunks(document_id, word_path.name, blocks)
    document_dir = Path(output_dir) / document_id
    document_dir.mkdir(parents=True, exist_ok=True)
    chunks_path = document_dir / "chunks.json"
    metadata_path = document_dir / "metadata.json"
    validation_path = document_dir / "validation_report.json"
    audit_path = document_dir / "extracted_text_audit.md"
    chunks_path.write_text(json.dumps(chunks, indent=2), encoding="utf-8")
    metadata_path.write_text(json.dumps({
        "document_id": document_id, "source_file": word_path.name,
        "word_format": word_path.suffix.lower().lstrip("."), "source_sha256": source_sha256,
        "total_chunks": len(chunks), "processing_time_seconds": round(time.perf_counter() - started, 3),
    }, indent=2), encoding="utf-8")
    validation_path.write_text(json.dumps({"status": "ok", "extractor": "python-docx", "blocks": len(blocks)}, indent=2), encoding="utf-8")
    audit_path.write_text("# Word Extraction: " + word_path.name + "\n\n" + "\n\n".join(blocks), encoding="utf-8")
    return WordOutputPaths(document_dir, chunks_path, metadata_path, validation_path, audit_path)


def _convert_doc_to_docx(doc_path: Path, conversion_dir: Path) -> Path:
    soffice = _find_soffice()
    profile_dir = conversion_dir / "libreoffice-profile"
    profile_uri = profile_dir.resolve().as_uri()
    try:
        completed = subprocess.run(
            [
                str(soffice), f"-env:UserInstallation={profile_uri}", "--headless",
                "--convert-to", "docx", "--outdir", str(conversion_dir), str(doc_path),
            ],
            capture_output=True, text=True, timeout=120, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WordExtractionError(f"LibreOffice could not convert the DOC file: {exc}") from exc
    converted_path = conversion_dir / f"{doc_path.stem}.docx"
    if completed.returncode != 0 or not converted_path.exists():
        detail = (completed.stderr or completed.stdout).strip()[:500]
        raise WordExtractionError(f"LibreOffice could not convert the DOC file. {detail}")
    return converted_path


def _find_soffice() -> Path:
    configured_path = os.getenv("RAG_LIBREOFFICE_PATH")
    candidates = [configured_path, shutil.which("soffice"), r"C:\Program Files\LibreOffice\program\soffice.exe"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise WordExtractionError("LibreOffice is required for legacy .doc uploads. Set RAG_LIBREOFFICE_PATH to soffice.exe.")


def _read_docx_blocks(docx_path: Path) -> list[str]:
    try:
        from docx import Document
        document = Document(str(docx_path))
    except Exception as exc:
        raise WordExtractionError(f"Could not read the DOCX file: {exc}") from exc
    blocks = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                blocks.append(" | ".join(cells))
    return blocks


def _build_chunks(document_id: str, source_file: str, blocks: list[str], target_chars: int = 1_000) -> list[dict]:
    groups: list[str] = []
    buffer: list[str] = []
    length = 0
    for block in blocks:
        if buffer and length + len(block) + 2 > target_chars:
            groups.append("\n\n".join(buffer))
            buffer, length = [], 0
        buffer.append(block)
        length += len(block) + 2
    if buffer:
        groups.append("\n\n".join(buffer))
    timestamp = datetime.now(timezone.utc).isoformat()
    return [{"chunk_text": text, "metadata": {
        "chunk_id": f"{document_id}_text_{index:04d}", "document_id": document_id,
        "source_file": source_file, "page_numbers": [], "section_title": None,
        "layout_type": "word_text", "extraction_method": "python-docx",
        "ocr_confidence": None, "validation_status": "ok", "ingestion_timestamp": timestamp,
        "pipeline_version": "word-v1", "source_region_ids": [],
    }} for index, text in enumerate(groups)]
