from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from multimodal_rag.api.config import CorpusScope
from multimodal_rag.api.ingestion import IngestionJob, IngestionJobManager
from multimodal_rag.rag.indexing.chroma_index import EmptyIndexError, build_index_from_output_dir
from multimodal_rag.security.clamav import ClamAVScanError, ClamAVThreatDetected


class ThreatScanner:
    fail_closed = True

    def scan(self, path: Path) -> None:
        raise ClamAVThreatDetected("stream: Test.Malware FOUND")


class UnavailableScanner:
    fail_closed = True

    def scan(self, path: Path) -> None:
        raise ClamAVScanError("ClamAV is unavailable")


class IngestionJobTests(unittest.TestCase):
    def test_empty_embedding_artifacts_are_not_indexed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            output_dir = Path(temporary_dir)
            document_dir = output_dir / "doc-empty"
            document_dir.mkdir()
            np.save(document_dir / "embeddings.npy", np.zeros((0, 0), dtype=np.float32))
            (document_dir / "embeddings_metadata.json").write_text(json.dumps({"chunks": []}), encoding="utf-8")

            with self.assertRaises(EmptyIndexError):
                build_index_from_output_dir(output_dir)

    def test_pending_documents_are_embedded_before_index_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            output_dir = Path(temporary_dir)
            first = output_dir / "doc-first"
            second = output_dir / "doc-second"
            for document_dir in (first, second):
                document_dir.mkdir()
                (document_dir / "chunks.json").write_text("[]", encoding="utf-8")

            manager = IngestionJobManager()
            manager._jobs["job-1"] = IngestionJob(
                job_id="job-1", filename="upload.pdf", user_id="alice", project_id=None,
            )
            fake_result = SimpleNamespace(
                embeddings=np.ones((1, 3), dtype=np.float32),
                chunks=[SimpleNamespace(chunk_id="chunk-1")],
            )
            try:
                with patch("multimodal_rag.api.ingestion.embed_document", return_value=fake_result) as embed_mock, patch("multimodal_rag.api.ingestion.write_embeddings") as write_mock:
                    count = manager._embed_pending_documents("job-1", output_dir, first)
            finally:
                manager.shutdown()

            self.assertEqual(count, 2)
            self.assertEqual(embed_mock.call_count, 2)
            self.assertEqual(write_mock.call_count, 2)

    def test_run_keeps_completed_status_after_completion_log(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            upload_path = root / "upload.pdf"
            upload_path.write_bytes(b"temporary upload")
            document_dir = root / "doc-new"
            document_dir.mkdir()
            chunks_path = document_dir / "chunks.json"
            chunks_path.write_text(json.dumps({"chunks": [{"chunk_id": "c-1"}]}), encoding="utf-8")
            scope = CorpusScope(user_id="alice", data_root=root, project_id=None)
            manager = IngestionJobManager()
            manager._jobs["job-complete"] = IngestionJob(
                job_id="job-complete", filename="upload.pdf", user_id="alice", project_id=None,
            )
            fake_paths = SimpleNamespace(chunks_json=chunks_path, document_dir=document_dir)
            try:
                with patch("multimodal_rag.api.ingestion.load_config", return_value=SimpleNamespace()), \
                    patch("multimodal_rag.api.ingestion.ingest_document", return_value=fake_paths), \
                    patch.object(manager, "_embed_pending_documents", return_value=1), \
                    patch("multimodal_rag.api.ingestion.build_index_from_output_dir", return_value=(object(), ["ref"])), \
                    patch("multimodal_rag.api.ingestion.save_index"), \
                    patch("multimodal_rag.api.ingestion.clear_scoped_index_cache"):
                    manager._run("job-complete", upload_path, scope)
            finally:
                manager.shutdown()

            snapshot = manager.get("job-complete")
            self.assertIsNotNone(snapshot)
            self.assertEqual(snapshot["status"], "completed")
            self.assertEqual(snapshot["stage"], "complete")
            self.assertIsNone(snapshot["error"])

    def test_malware_verdict_quarantines_upload_before_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            upload_path = root / "upload.pdf"
            upload_path.write_bytes(b"untrusted upload")
            scope = CorpusScope(user_id="alice", data_root=root, project_id=None)
            manager = IngestionJobManager(scanner=ThreatScanner())
            manager._jobs["job-threat"] = IngestionJob(
                job_id="job-threat", filename="upload.pdf", user_id="alice", project_id=None,
            )
            try:
                with patch("multimodal_rag.api.ingestion.ingest_document") as ingest_mock:
                    manager._run("job-threat", upload_path, scope)
            finally:
                manager.shutdown()

            snapshot = manager.get("job-threat")
            self.assertIsNotNone(snapshot)
            self.assertEqual(snapshot["status"], "failed")
            self.assertEqual(snapshot["stage"], "security_scan")
            self.assertIn("malware scan", snapshot["error"])
            ingest_mock.assert_not_called()
            self.assertFalse(upload_path.exists())
            self.assertTrue((scope.root / "quarantine" / "job-threat.pdf").exists())

    def test_unavailable_enabled_scanner_blocks_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            upload_path = root / "upload.pdf"
            upload_path.write_bytes(b"untrusted upload")
            scope = CorpusScope(user_id="alice", data_root=root, project_id=None)
            manager = IngestionJobManager(scanner=UnavailableScanner())
            manager._jobs["job-unavailable"] = IngestionJob(
                job_id="job-unavailable", filename="upload.pdf", user_id="alice", project_id=None,
            )
            try:
                with patch("multimodal_rag.api.ingestion.ingest_document") as ingest_mock:
                    manager._run("job-unavailable", upload_path, scope)
            finally:
                manager.shutdown()

            snapshot = manager.get("job-unavailable")
            self.assertIsNotNone(snapshot)
            self.assertEqual(snapshot["status"], "failed")
            self.assertEqual(snapshot["stage"], "error")
            self.assertIn("Security scan failed", snapshot["error"])
            ingest_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
