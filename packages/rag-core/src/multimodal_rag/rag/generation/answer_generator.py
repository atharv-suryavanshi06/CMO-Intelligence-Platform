"""
Answer Generation Module (RAG Stage 6)
==========================================

Calls Gemini with the prompt built in Stage 5 to produce the final
answer. Reuses the exact API-key/error-handling pattern already
established in ingestion/extractors/vision_describer.py - same
GEMINI_API_KEY env var, same "unavailable vs failed" exception split -
so this module behaves consistently with the rest of the project rather
than inventing a new convention.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from dotenv import load_dotenv

from multimodal_rag.rag.observability import observed
load_dotenv()

logger = logging.getLogger(__name__)


class AnswerGenerationError(Exception):
    """Raised when the Gemini call itself fails (bad response, API error, timeout)."""


class AnswerGenerationUnavailableError(AnswerGenerationError):
    """Raised when no API key is configured."""


@dataclass
class GenerationConfig:
    model_name: str = "gemini-3.1-flash-lite"
    temperature: float = 0.2
    # Low temperature by default: RAG answers should be grounded in the
    # provided sources, not creative - matches the same reasoning used
    # for the diagram-description prompt in vision_describer.py.


@dataclass(frozen=True)
class GenerationResult:
    text: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


def _get_api_key() -> str:
    # The embedding stage already accepts GOOGLE_API_KEY.  Support the same
    # standard name here while retaining GEMINI_API_KEY for existing setups,
    # so a single Gemini key configures the complete RAG pipeline.
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise AnswerGenerationUnavailableError(
            "GEMINI_API_KEY or GOOGLE_API_KEY is not set in the environment - answer generation is unavailable."
        )
    return api_key


@observed("gemini.generate", run_type="llm")
def generate_answer_with_metadata(
    prompt_text: str,
    config: GenerationConfig | None = None,
) -> GenerationResult:
    config = config or GenerationConfig()
    api_key = _get_api_key()

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=config.model_name,
            contents=prompt_text,
            config=types.GenerateContentConfig(temperature=config.temperature),
        )
        text = getattr(response, "text", None)
        if not text:
            raise AnswerGenerationError("Gemini returned a response with no text content.")
        usage = getattr(response, "usage_metadata", None)
        return GenerationResult(
            text=text.strip(),
            prompt_tokens=getattr(usage, "prompt_token_count", None),
            completion_tokens=getattr(usage, "candidates_token_count", None),
            total_tokens=getattr(usage, "total_token_count", None),
        )
    except AnswerGenerationError:
        raise
    except Exception as e:
        raise AnswerGenerationError(f"Gemini answer generation failed: {e}") from e


def generate_answer(prompt_text: str, config: GenerationConfig | None = None) -> str:
    """Compatibility wrapper preserving the original string-returning API."""
    return generate_answer_with_metadata(prompt_text, config).text
