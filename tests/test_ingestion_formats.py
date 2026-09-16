from __future__ import annotations

import unittest

from multimodal_rag.ingestion.formats import (
    is_active_ingestion_file,
    is_supported_ingestion_file,
)


class IngestionFormatTests(unittest.TestCase):
    def test_allowlist_contains_planned_formats(self) -> None:
        for filename in ("brief.pdf", "clip.mp4", "interview.mp3", "memo.doc", "memo.docx", "deck.ppt", "deck.pptx"):
            self.assertTrue(is_supported_ingestion_file(filename))

    def test_unrelated_formats_are_not_allowed(self) -> None:
        self.assertFalse(is_supported_ingestion_file("sheet.xlsx"))
        self.assertFalse(is_supported_ingestion_file("notes.txt"))

    def test_all_allowlisted_formats_have_active_api_workers(self) -> None:
        for filename in ("brief.PDF", "clip.mp4", "interview.mp3", "memo.doc", "memo.docx", "deck.ppt", "deck.pptx"):
            self.assertTrue(is_active_ingestion_file(filename))


if __name__ == "__main__":
    unittest.main()
