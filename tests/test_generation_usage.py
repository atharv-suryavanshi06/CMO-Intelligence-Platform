"""Deterministic tests for Gemini response usage metadata propagation."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from multimodal_rag.rag.generation.answer_generator import (
    GenerationResult,
    generate_answer,
    generate_answer_with_metadata,
)


class GenerationUsageTests(unittest.TestCase):
    @staticmethod
    def _client_for(response):
        generate_content = Mock(return_value=response)
        client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
        return client, generate_content

    def test_usage_metadata_is_copied_exactly_in_one_generation_call(self) -> None:
        response = SimpleNamespace(
            text="  grounded answer  ",
            usage_metadata=SimpleNamespace(
                prompt_token_count=101,
                candidates_token_count=23,
                total_token_count=124,
            ),
        )
        client, generate_content = self._client_for(response)
        with (
            patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}),
            patch("google.genai.Client", return_value=client),
        ):
            result = generate_answer_with_metadata("prompt")

        self.assertEqual(
            result,
            GenerationResult(
                text="grounded answer",
                prompt_tokens=101,
                completion_tokens=23,
                total_tokens=124,
            ),
        )
        generate_content.assert_called_once()

    def test_missing_usage_metadata_remains_unavailable(self) -> None:
        client, generate_content = self._client_for(
            SimpleNamespace(text="answer", usage_metadata=None)
        )
        with (
            patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}),
            patch("google.genai.Client", return_value=client),
        ):
            result = generate_answer_with_metadata("prompt")

        self.assertIsNone(result.prompt_tokens)
        self.assertIsNone(result.completion_tokens)
        self.assertIsNone(result.total_tokens)
        generate_content.assert_called_once()

    def test_generate_answer_remains_a_string_returning_wrapper(self) -> None:
        client, generate_content = self._client_for(
            SimpleNamespace(text=" answer ", usage_metadata=None)
        )
        with (
            patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}),
            patch("google.genai.Client", return_value=client),
        ):
            answer = generate_answer("prompt")

        self.assertIsInstance(answer, str)
        self.assertEqual(answer, "answer")
        generate_content.assert_called_once()


if __name__ == "__main__":
    unittest.main()
