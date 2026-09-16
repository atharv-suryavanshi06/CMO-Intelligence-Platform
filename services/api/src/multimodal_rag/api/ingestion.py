"""Background document ingestion jobs used by the authenticated API."""

from __future__ import annotations

import json
import logging
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from multimodal_rag.api.config import CorpusScope
from multimodal_rag.api.service import clear_scoped_index_cache
from multimodal_rag.cli.ingest import load_config
from multimodal_rag.ingestion.output.deduplicator import find_content_duplicate, find_file_duplicate
from multimodal_rag.ingestion.formats import file_extension
from multimodal_rag.ingestion.media.deepgram import DeepgramTranscriber, ingest_media
from multimodal_rag.ingestion.word.extractor import ingest_word
from multimodal_rag.ingestion.presentation.extractor import ingest_presentation
from multimodal_rag.paths import CONFIG_DIR
from multimodal_rag.rag.embedding.embedder import EmbeddingConfig, embed_document, write_embeddings
from multimodal_rag.rag.indexing.chroma_index import EmptyIndexError, build_index_from_output_dir, save_index
from multimodal_rag.security.clamav import ClamAVScanError, ClamAVScanner, ClamAVThreatDetected
from multimodal_rag.ingestion.pipeline.orchestrator import IngestionError, ingest_document

logger = logging.getLogger(__name__)


@dataclass
class IngestionJob:
    job_id: str
    filename: str
    user_id: str
    project_id: str | None
    status: str = "queued"
    stage: str = "upload"
    document_id: str | None = None
    chunk_count: int = 0
    embedded_count: int = 0
    extraction_completed: int = 0
    extraction_total: int = 0
    extraction_percent: int = 0
    error: str | None = None
    _extra: dict[str, Any] = field(default_factory=dict, repr=False)

    def snapshot(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "filename": self.filename,
            "user_id": self.user_id,
            "project_id": self.project_id,
            "status": self.status,
            "stage": self.stage,
            "document_id": self.document_id,
            "chunk_count": self.chunk_count,
            "embedded_count": self.embedded_count,
            "extraction_completed": self.extraction_completed,
            "extraction_total": self.extraction_total,
            "extraction_percent": self.extraction_percent,
            "error": self.error,
        }


class IngestionJobManager:
    """Serialize upload jobs and expose thread-safe status snapshots.

    A single worker prevents two requests in the same API process from
    rebuilding one scoped Chroma collection at the same time. Each user/project
    scope has its own artifact directory, so this does not touch the existing
    global CLI worker's output while it is running.
    """

    def __init__(self, max_workers: int = 1, scanner: ClamAVScanner | None = None, transcriber: DeepgramTranscriber | None = None) -> None:
        self._jobs: dict[str, IngestionJob] = {}
        self._lock = threading.Lock()
        self._inflight_file_hashes: dict[str, str] = {}
        self._inflight_content_hashes: dict[str, str] = {}
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="rag-ingest")
        self._scanner = scanner or ClamAVScanner()
        self._transcriber = transcriber or DeepgramTranscriber(api_key=None)

    def submit(
        self,
        *,
        pdf_path: Path,
        scope: CorpusScope,
        filename: str,
        job_id: str | None = None,
        source_sha256: str | None = None,
        content_sha256: str | None = None,
    ) -> dict[str, Any]:
        job = IngestionJob(
            job_id=job_id or uuid.uuid4().hex,
            filename=filename,
            user_id=scope.user_id,
            project_id=scope.project_id,
        )
        with self._lock:
            if source_sha256:
                existing_file = find_file_duplicate(scope.ingestion_artifacts_dir, source_sha256)
                if existing_file:
                    raise IngestionError(
                        f"Duplicate document already ingested as {existing_file.get('document_id', 'an existing document')}; upload skipped."
                    )
                if source_sha256 in self._inflight_file_hashes:
                    raise IngestionError("Duplicate document upload is already being processed; upload skipped.")
            if content_sha256:
                existing_content = find_content_duplicate(scope.ingestion_artifacts_dir, content_sha256)
                if existing_content:
                    raise IngestionError(
                        f"Duplicate document content already ingested as {existing_content.get('document_id', 'an existing document')}; upload skipped."
                    )
                if content_sha256 in self._inflight_content_hashes:
                    raise IngestionError("Duplicate document content is already being processed; upload skipped.")
            self._jobs[job.job_id] = job
            if source_sha256:
                self._inflight_file_hashes[source_sha256] = job.job_id
            if content_sha256:
                self._inflight_content_hashes[content_sha256] = job.job_id
        try:
            self._executor.submit(self._run, job.job_id, pdf_path, scope, source_sha256, content_sha256)
        except Exception:
            with self._lock:
                if source_sha256:
                    self._inflight_file_hashes.pop(source_sha256, None)
                if content_sha256:
                    self._inflight_content_hashes.pop(content_sha256, None)
                self._jobs.pop(job.job_id, None)
            raise
        return job.snapshot()

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.snapshot() if job else None

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _update(self, job_id: str, **values: Any) -> None:
        with self._lock:
            job = self._jobs[job_id]
            for key, value in values.items():
                setattr(job, key, value)

    @staticmethod
    def _has_usable_embeddings(document_dir: Path) -> bool:
        """Return True only for a complete, non-empty embedding artifact."""
        embeddings_path = document_dir / "embeddings.npy"
        metadata_path = document_dir / "embeddings_metadata.json"
        if not embeddings_path.exists() or not metadata_path.exists():
            return False
        try:
            vectors = np.load(embeddings_path, mmap_mode="r")
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            embedded_chunks = metadata.get("chunks", [])
            return (
                vectors.ndim == 2
                and vectors.shape[0] > 0
                and vectors.shape[1] > 0
                and vectors.shape[0] == len(embedded_chunks)
            )
        except (OSError, ValueError, json.JSONDecodeError):
            return False

    def _embed_pending_documents(
        self,
        job_id: str,
        output_dir: Path,
        preferred_document_dir: Path,
    ) -> int:
        """Embed every scoped document missing a usable vector artifact.

        The uploaded document is attempted first. Existing chunk-only
        documents are then backfilled so a newly created scoped index can
        answer questions about the whole scope, not just the latest upload.
        Exact duplicate uploads can legitimately produce zero new chunks; in
        that case their existing canonical chunks are embedded from the
        original document instead.
        """
        pending = [
            document_dir
            for document_dir in sorted(output_dir.iterdir())
            if document_dir.is_dir()
            and not document_dir.name.startswith("_")
            and (document_dir / "chunks.json").exists()
            and not self._has_usable_embeddings(document_dir)
        ]
        pending.sort(key=lambda document_dir: document_dir != preferred_document_dir)
        total_embedded = 0
        failures: list[str] = []
        for document_dir in pending:
            try:
                result = embed_document(
                    document_dir / "chunks.json",
                    EmbeddingConfig(),
                    checkpoint_dir=document_dir,
                )
                if result.embeddings.ndim != 2 or result.embeddings.shape[0] == 0:
                    logger.warning("No new embeddable chunks in '%s'; leaving it out of the index", document_dir.name)
                    continue
                write_embeddings(result, document_dir)
                total_embedded += len(result.chunks)
                self._update(job_id, embedded_count=total_embedded)
            except Exception as exc:
                logger.exception("Embedding failed for scoped document '%s'", document_dir.name)
                if document_dir == preferred_document_dir:
                    raise
                failures.append(f"{document_dir.name}: {exc}")

        if failures:
            logger.warning("Skipped %d pre-existing document embedding job(s): %s", len(failures), "; ".join(failures))
        return total_embedded

    def _run(
        self,
        job_id: str,
        pdf_path: Path,
        scope: CorpusScope,
        source_sha256: str | None = None,
        content_sha256: str | None = None,
    ) -> None:
        try:
            self._update(job_id, status="running", stage="security_scan")
            try:
                self._scanner.scan(pdf_path)
            except ClamAVThreatDetected as exc:
                quarantine_path = self._quarantine(job_id, pdf_path, scope)
                logger.warning("Quarantined upload for ingestion job %s at %s: %s", job_id, quarantine_path, exc)
                self._update(
                    job_id,
                    status="failed",
                    stage="security_scan",
                    error="Upload blocked by the malware scan.",
                )
                return
            except ClamAVScanError as exc:
                if self._scanner.fail_closed:
                    raise IngestionError(f"Security scan failed: {exc}") from exc
                logger.warning("Continuing ingestion job %s after ClamAV scan failure: %s", job_id, exc)

            self._update(job_id, status="running", stage="extract")
            config = load_config(CONFIG_DIR)
            if file_extension(pdf_path) in {".mp3", ".mp4"}:
                paths = ingest_media(pdf_path, scope.ingestion_artifacts_dir, self._transcriber, source_sha256)
            elif file_extension(pdf_path) in {".doc", ".docx"}:
                paths = ingest_word(pdf_path, scope.ingestion_artifacts_dir, source_sha256)
            elif file_extension(pdf_path) in {".ppt", ".pptx"}:
                paths = ingest_presentation(pdf_path, scope.ingestion_artifacts_dir, source_sha256)
            else:
                def on_extraction_progress(completed: int, total: int, page_number: int | None = None) -> None:
                    percent = round((completed / total) * 100) if total else 0
                    self._update(
                        job_id,
                        extraction_completed=completed,
                        extraction_total=total,
                        extraction_percent=percent,
                    )
                    logger.info(
                        "Extraction progress job %s: %d/%d (%d%%)%s",
                        job_id,
                        completed,
                        total,
                        percent,
                        f" page={page_number}" if page_number is not None else "",
                    )

                paths = ingest_document(
                    pdf_path,
                    scope.ingestion_artifacts_dir,
                    config,
                    source_sha256=source_sha256,
                    content_sha256=content_sha256,
                    progress_callback=on_extraction_progress,
                )
            chunks_payload = json.loads(paths.chunks_json.read_text(encoding="utf-8"))
            chunk_records = chunks_payload.get("chunks", chunks_payload) if isinstance(chunks_payload, dict) else chunks_payload
            self._update(
                job_id,
                stage="chunk",
                document_id=paths.document_dir.name,
                chunk_count=len(chunk_records),
            )

            self._update(job_id, stage="embed")
            embedded_count = self._embed_pending_documents(
                job_id,
                scope.ingestion_artifacts_dir,
                paths.document_dir,
            )
            self._update(job_id, stage="store", embedded_count=embedded_count)

            self._update(job_id, stage="index")
            index, refs = build_index_from_output_dir(scope.ingestion_artifacts_dir)
            if not refs:
                raise EmptyIndexError("No non-empty embeddings are available for this user/project scope.")
            save_index(index, refs, scope.index_dir)
            clear_scoped_index_cache()
            self._update(job_id, status="completed", stage="complete")
            logger.info(
                "Completed ingestion job %s: %s chunks, %s embeddings",
                job_id,
                len(chunk_records),
                embedded_count,
            )
        except (IngestionError, EmptyIndexError, OSError, ValueError, RuntimeError) as exc:
            logger.exception("Ingestion job %s failed", job_id)
            self._update(job_id, status="failed", stage="error", error=str(exc))
        except Exception as exc:  # Keep the status endpoint useful for provider failures too.
            logger.exception("Unexpected ingestion job %s failure", job_id)
            self._update(job_id, status="failed", stage="error", error=str(exc))
        finally:
            if source_sha256:
                with self._lock:
                    self._inflight_file_hashes.pop(source_sha256, None)
            if content_sha256:
                with self._lock:
                    self._inflight_content_hashes.pop(content_sha256, None)
            try:
                pdf_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove temporary upload %s", pdf_path)

    @staticmethod
    def _quarantine(job_id: str, pdf_path: Path, scope: CorpusScope) -> Path:
        quarantine_dir = scope.root / "quarantine"
        quarantine_dir.mkdir(parents=True, exist_ok=True)
        quarantine_path = quarantine_dir / f"{job_id}{pdf_path.suffix.lower()}"
        shutil.move(str(pdf_path), str(quarantine_path))
        return quarantine_path
