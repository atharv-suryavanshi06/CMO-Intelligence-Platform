from __future__ import annotations

import tempfile
import unittest

from multimodal_rag.ingestion.output.deduplicator import (
    deduplicate_chunks,
    find_content_duplicate,
    find_file_duplicate,
    persist_deduplication,
)
from multimodal_rag.ingestion.processing.chunker import _build_chunk


def _child(text: str):
    return _build_chunk(text, "doc", "source.pdf", [1], None, "text", "native", 1.0, "ok", [])


class DeduplicationTests(unittest.TestCase):
    def test_exact_chunks_are_reused_and_partial_documents_keep_only_new_chunks(self):
        with tempfile.TemporaryDirectory() as directory:
            first, registry, _ = deduplicate_chunks(directory, "doc-1", "first.pdf", [_child("A"), _child("B")])
            persist_deduplication(directory, directory, registry, {})

            second, registry, report = deduplicate_chunks(directory, "doc-2", "second.pdf", [_child("A"), _child("C")])

            self.assertEqual([chunk.chunk_text for chunk in second], ["C"])
            self.assertEqual(report["stored_retrieval_chunks"], 1)
            self.assertEqual(len(report["reused_retrieval_chunks"]), 1)
            self.assertEqual(report["reused_retrieval_chunks"][0]["canonical"]["source_file"], "first.pdf")

    def test_exact_duplicate_drops_all_chunks(self):
        with tempfile.TemporaryDirectory() as directory:
            first, registry, _ = deduplicate_chunks(directory, "doc-1", "first.pdf", [_child("A")])
            persist_deduplication(directory, directory, registry, {})
            duplicate, _, report = deduplicate_chunks(directory, "doc-2", "copy.pdf", [_child("A")])

            self.assertEqual(duplicate, [])
            self.assertTrue(report["exact_document_duplicate"])

    def test_exact_file_hash_is_persisted_for_preflight_detection(self):
        with tempfile.TemporaryDirectory() as directory:
            _, registry, _ = deduplicate_chunks(
                directory,
                "doc-1",
                "first.pdf",
                [_child("A")],
                source_sha256="file-hash-1",
            )
            persist_deduplication(directory, directory, registry, {})

            self.assertEqual(
                find_file_duplicate(directory, "file-hash-1"),
                {"document_id": "doc-1", "source_file": "first.pdf"},
            )

    def test_normalized_content_hash_is_persisted_for_preflight_detection(self):
        with tempfile.TemporaryDirectory() as directory:
            _, registry, _ = deduplicate_chunks(
                directory,
                "doc-1",
                "first.pdf",
                [_child("A")],
                content_sha256="content-hash-1",
            )
            persist_deduplication(directory, directory, registry, {})

            self.assertEqual(
                find_content_duplicate(directory, "content-hash-1"),
                {"document_id": "doc-1", "source_file": "first.pdf"},
            )


if __name__ == "__main__":
    unittest.main()
