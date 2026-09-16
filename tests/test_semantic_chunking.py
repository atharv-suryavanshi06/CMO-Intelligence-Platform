from __future__ import annotations

import unittest

from multimodal_rag.ingestion.processing.chunker import (
    ChunkerConfig,
    _BufferedRegion,
    _flush_paragraph_buffer,
)


class SemanticChunkingTests(unittest.TestCase):
    def test_semantic_children_preserve_sentence_boundaries_and_hierarchy(self) -> None:
        buffer = [
            _BufferedRegion(
                text=(
                    "First complete sentence. Second complete sentence. "
                    "Third complete sentence. Fourth complete sentence."
                ),
                page_number=1,
                region_id="r-1",
                extraction_method="native",
                confidence=0.99,
                validation_status="ok",
                layout_type="text",
            )
        ]

        chunks = _flush_paragraph_buffer(
            buffer,
            "doc-1",
            "report.pdf",
            "Executive summary",
            "Executive summary\n\n",
            ChunkerConfig(chunk_size=55, chunk_overlap=20),
        )

        parents = [chunk for chunk in chunks if chunk.metadata.chunk_level == "parent"]
        children = [chunk for chunk in chunks if chunk.metadata.chunk_level == "child"]
        self.assertEqual(len(parents), 1)
        self.assertGreater(len(children), 1)
        self.assertTrue(all(chunk.chunk_text.rstrip().endswith(".") for chunk in children))
        parent_ids = {chunk.metadata.parent_section_id for chunk in children}
        self.assertEqual(len(parent_ids), 1)
        self.assertEqual([chunk.metadata.child_index for chunk in children], list(range(len(children))))
        self.assertTrue(all(chunk.metadata.child_count == len(children) for chunk in children))
        self.assertTrue(all(chunk.metadata.parent_chunk_id == parents[0].metadata.chunk_id for chunk in children))

    def test_single_oversized_sentence_is_not_cut(self) -> None:
        text = "A " + ("very " * 30) + "long sentence."
        chunks = _flush_paragraph_buffer(
            [_BufferedRegion(text, 1, "r-1", "native", 1.0, "ok", "text")],
            "doc-1", "report.pdf", None, "", ChunkerConfig(chunk_size=40, chunk_overlap=10),
        )

        children = [chunk for chunk in chunks if chunk.metadata.chunk_level == "child"]
        self.assertEqual([chunk.chunk_text for chunk in children], [text])
