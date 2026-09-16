"""Content-addressed deduplication for active ingestion artifacts."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from multimodal_rag.ingestion.processing.chunker import Chunk
from multimodal_rag.ingestion.loaders.pdf_loader import normalized_pdf_text_hash, normalized_text_hash
from multimodal_rag.paths import INPUT_DIR, LEGACY_INPUT_DIR

REGISTRY_FILE = "chunk_registry.json"


def sha256_file(path: str | Path) -> str:
    """Return the SHA-256 digest of a file's exact bytes."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalized_hash(text: str) -> str:
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _load_registry(output_dir: str | Path) -> dict:
    root = Path(output_dir)
    path = root / REGISTRY_FILE
    if path.exists():
        registry = json.loads(path.read_text(encoding="utf-8"))
        registry.setdefault("chunks", {})
        registry.setdefault("documents", {})
        registry.setdefault("files", {})
        registry.setdefault("content", {})
        return registry
    registry = {"chunks": {}, "documents": {}, "files": {}, "content": {}}
    for document_dir in root.iterdir() if root.exists() else []:
        if not document_dir.is_dir() or document_dir.name.startswith("_"):
            continue
        chunks_path = document_dir / "chunks.json"
        if not chunks_path.exists():
            continue
        for record in json.loads(chunks_path.read_text(encoding="utf-8")):
            metadata = record.get("metadata", {})
            if metadata.get("chunk_level") == "parent":
                continue
            chunk_hash = metadata.get("content_hash") or normalized_hash(record.get("chunk_text", ""))
            registry["chunks"].setdefault(chunk_hash, {
                "chunk_id": metadata.get("chunk_id"),
                "document_id": metadata.get("document_id"),
                "source_file": metadata.get("source_file"),
            })
    return registry


def _backfill_legacy_fingerprints(root: Path, registry: dict) -> bool:
    """Backfill file/content fingerprints when legacy source PDFs remain."""
    files = registry.setdefault("files", {})
    content = registry.setdefault("content", {})
    changed = False
    for document in registry.get("documents", {}).values():
        source_file = document.get("source_file")
        document_id = document.get("document_id")
        record = {"document_id": document_id, "source_file": source_file}
        if source_file:
            for input_root in (INPUT_DIR, LEGACY_INPUT_DIR):
                source_path = input_root / source_file
                if not source_path.is_file():
                    continue
                source_hash = sha256_file(source_path)
                if source_hash not in files:
                    files[source_hash] = record
                    changed = True
                content_hash = normalized_pdf_text_hash(source_path)
                if content_hash and content_hash not in content:
                    content[content_hash] = record
                    changed = True
                break

        # Uploaded PDFs are removed after completion, but their raw snapshot
        # remains under the artifact directory. Use it to backfill the
        # normalized-content fingerprint when the original source is gone.
        pages_path = root / str(document_id) / "raw" / "pages.json"
        if document_id and pages_path.is_file():
            try:
                pages = json.loads(pages_path.read_text(encoding="utf-8"))
                texts = [
                    region.get("raw_text_content", "")
                    for page in pages.values()
                    for region in page
                    if isinstance(region, dict)
                ] if isinstance(pages, dict) else []
                content_hash = normalized_text_hash(texts)
            except (OSError, ValueError, json.JSONDecodeError):
                content_hash = None
            if content_hash and content_hash not in content:
                content[content_hash] = record
                changed = True
    return changed


def _persist_registry_if_changed(root: Path, registry: dict, changed: bool) -> None:
    if not changed:
        return
    registry_path = root / REGISTRY_FILE
    try:
        root.mkdir(parents=True, exist_ok=True)
        registry_path.write_text(json.dumps(registry, indent=2), encoding="utf-8")
    except OSError:
        # Detection still works for this request even if persistence is
        # temporarily unavailable.
        pass


def find_file_duplicate(output_dir: str | Path, file_hash: str) -> dict | None:
    """Find an already registered document with the same exact file hash."""
    root = Path(output_dir)
    registry = _load_registry(root)
    files = registry.setdefault("files", {})
    _persist_registry_if_changed(root, registry, _backfill_legacy_fingerprints(root, registry))
    return files.get(file_hash)


def find_content_duplicate(output_dir: str | Path, content_hash: str) -> dict | None:
    """Find a document with the same normalized extracted-text fingerprint."""
    root = Path(output_dir)
    registry = _load_registry(root)
    content = registry.setdefault("content", {})
    _persist_registry_if_changed(root, registry, _backfill_legacy_fingerprints(root, registry))
    return content.get(content_hash)


def deduplicate_chunks(
    output_dir: str | Path,
    document_id: str,
    source_file: str,
    chunks: list[Chunk],
    source_sha256: str | None = None,
    content_sha256: str | None = None,
) -> tuple[list[Chunk], dict, dict]:
    """Keep one canonical retrieval child; parents remain document context."""
    registry = _load_registry(output_dir)
    unique, reused = [], []
    document_hash = normalized_hash("\n".join(chunk.chunk_text for chunk in chunks))
    for chunk in chunks:
        if chunk.metadata.chunk_level == "parent":
            unique.append(chunk)
            continue
        chunk_hash = normalized_hash(chunk.chunk_text)
        chunk.metadata.content_hash = chunk_hash
        canonical = registry["chunks"].get(chunk_hash)
        if canonical:
            reused.append({"chunk_hash": chunk_hash, "canonical": canonical})
            continue
        registry["chunks"][chunk_hash] = {"chunk_id": chunk.metadata.chunk_id, "document_id": document_id, "source_file": source_file}
        unique.append(chunk)
    registry["documents"].setdefault(document_hash, {"document_id": document_id, "source_file": source_file})
    if source_sha256:
        registry["files"].setdefault(
            source_sha256,
            {"document_id": document_id, "source_file": source_file},
        )
    if content_sha256:
        registry["content"].setdefault(
            content_sha256,
            {"document_id": document_id, "source_file": source_file},
        )
    exact_duplicate = bool(chunks) and not any(c.metadata.chunk_level != "parent" for c in unique)
    if exact_duplicate:
        unique = []
    report = {
        "document_hash": document_hash,
        "content_sha256": content_sha256,
        "total_retrieval_chunks": sum(c.metadata.chunk_level != "parent" for c in chunks),
        "stored_retrieval_chunks": sum(c.metadata.chunk_level != "parent" for c in unique),
        "reused_retrieval_chunks": reused,
        "exact_document_duplicate": exact_duplicate,
    }
    return unique, registry, report


def persist_deduplication(output_dir: str | Path, document_dir: str | Path, registry: dict, report: dict) -> None:
    Path(output_dir, REGISTRY_FILE).write_text(json.dumps(registry, indent=2), encoding="utf-8")
    Path(document_dir, "deduplication.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
