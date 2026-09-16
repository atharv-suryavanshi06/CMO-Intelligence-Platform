"""Package, path, and migration-structure smoke tests.

The test intentionally uses only the standard library so it can run before
the existing application modules are moved beneath ``src/``.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from multimodal_rag import paths


class PackageSmokeTests(unittest.TestCase):
    def test_package_imports(self) -> None:
        self.assertEqual(paths.PACKAGE_ROOT.name, "multimodal_rag")

    def test_paths_resolve_from_repository_root(self) -> None:
        self.assertEqual(paths.PROJECT_ROOT, PROJECT_ROOT)
        self.assertEqual(paths.INPUT_DIR, PROJECT_ROOT / "runtime-data" / "input")
        self.assertEqual(paths.INDEX_DIR, PROJECT_ROOT / "runtime-data" / "artifacts" / "index")
        self.assertEqual(
            paths.GROUND_TRUTH_PATH,
            PROJECT_ROOT / "evaluation" / "datasets" / "ground_truth.json",
        )

    def test_canonical_modules_exist_and_root_wrappers_are_absent(self) -> None:
        canonical_modules = (
            "multimodal_rag.cli.ingest",
            "multimodal_rag.cli.build_index",
            "multimodal_rag.cli.ask",
            "multimodal_rag.rag.indexing.chroma_index",
            "multimodal_rag.evaluation.runner",
            "multimodal_rag.evaluation.question_runner",
            "multimodal_rag.tools.comparison.marker_extract",
            "multimodal_rag.tools.comparison.unstructured_extract",
            "multimodal_rag.tools.diagnostics.trace_page",
        )
        for module_name in canonical_modules:
            with self.subTest(module=module_name):
                __import__(module_name)

        wrappers = (
            "main.py",
            "build_index.py",
            "ask.py",
            "app.py",
            "marker_extract.py",
            "unstructured_extract.py",
            "trace_page.py",
            "ragas_eval.py",
            "ragas_eval_question.py",
        )
        for wrapper in wrappers:
            with self.subTest(wrapper=wrapper):
                self.assertFalse((PROJECT_ROOT / wrapper).exists())

    def test_legacy_source_directories_are_absent(self) -> None:
        for directory in (
            "ingestion",
            "rag",
            "input",
            "output",
            "index",
            "logs",
            "ingestion_output",
            "comparison_output",
        ):
            with self.subTest(directory=directory):
                self.assertFalse((PROJECT_ROOT / directory).exists())


if __name__ == "__main__":
    unittest.main()
