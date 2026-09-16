"""Focused tests for the canonical question-answering CLI."""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from multimodal_rag.cli import ask


class AskCliTopKTests(unittest.TestCase):
    def _run_cli(self, extra_args: list[str], expected_top_k: int) -> None:
        chunk = SimpleNamespace(
            score=0.75,
            page_numbers=[1],
            chunk_text="Retrieved context",
        )
        built_prompt = SimpleNamespace(prompt_text="Grounded prompt", source_map={})
        resolved = SimpleNamespace(answer_text="Generated answer", citations=[])

        with (
            patch.object(sys, "argv", ["ask", "Test question", *extra_args]),
            patch.object(ask, "load_index", return_value=(object(), {})) as load_index,
            patch.object(ask, "retrieve", return_value=[chunk]) as retrieve,
            patch.object(ask, "build_prompt", return_value=built_prompt) as build_prompt,
            patch.object(ask, "generate_answer", return_value="Generated answer") as generate_answer,
            patch.object(ask, "resolve_citations", return_value=resolved) as resolve_citations,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(ask.main(), 0)

        load_index.assert_called_once()
        retrieve.assert_called_once()
        build_prompt.assert_called_once()
        generate_answer.assert_called_once()
        resolve_citations.assert_called_once()
        config = retrieve.call_args.kwargs["retriever_config"]
        self.assertEqual(config.top_k, expected_top_k)

    def test_default_top_k_remains_eight(self) -> None:
        self._run_cli([], 8)

    def test_top_k_three_is_honored(self) -> None:
        self._run_cli(["--top-k", "3"], 3)

    def test_top_k_five_is_honored(self) -> None:
        self._run_cli(["--top-k", "5"], 5)


if __name__ == "__main__":
    unittest.main()
