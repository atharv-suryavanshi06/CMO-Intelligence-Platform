from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import fitz

from multimodal_rag.ingestion.loaders.pdf_loader import (
    PDFCorruptError,
    PDFPageCountError,
    load_pdf,
    normalized_pdf_text_hash,
)


class PDFLimitTests(unittest.TestCase):
    def test_empty_pdf_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            path = Path(temporary_dir) / "empty.pdf"
            path.write_bytes(b"")

            with self.assertRaisesRegex(PDFCorruptError, "empty file"):
                load_pdf(path)

    def test_pdf_over_fifty_pages_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            path = Path(temporary_dir) / "large.pdf"
            document = fitz.open()
            for _ in range(51):
                document.new_page()
            document.save(path)
            document.close()

            with self.assertRaisesRegex(PDFPageCountError, "50 pages"):
                load_pdf(path)

    def test_normalized_text_hash_ignores_pdf_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            first_path = Path(temporary_dir) / "first.pdf"
            second_path = Path(temporary_dir) / "second.pdf"
            for path, title in ((first_path, "First export"), (second_path, "Second export")):
                document = fitz.open()
                page = document.new_page()
                page.insert_text((72, 72), "Identical report content with stable wording.")
                document.set_metadata({"title": title})
                document.save(path)
                document.close()

            self.assertNotEqual(first_path.read_bytes(), second_path.read_bytes())
            self.assertEqual(normalized_pdf_text_hash(first_path), normalized_pdf_text_hash(second_path))


if __name__ == "__main__":
    unittest.main()
