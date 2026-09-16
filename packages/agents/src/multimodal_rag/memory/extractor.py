"""LLM-assisted extraction of durable business context from user messages."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from multimodal_rag.memory.models import MemoryExtractionResult
from multimodal_rag.rag.generation.answer_generator import GenerationResult, generate_answer_with_metadata

Generator = Callable[[str], GenerationResult | str]


class MemoryExtractor:
    """Extract structured memory candidates; this class never accesses persistence."""

    def __init__(self, generator: Generator = generate_answer_with_metadata) -> None:
        self._generator = generator

    def extract(self, message: str) -> MemoryExtractionResult:
        message = message.strip()
        if not message:
            return MemoryExtractionResult()
        try:
            raw = self._generator(self._prompt(message))
            text = raw.text if isinstance(raw, GenerationResult) else str(raw)
            return self._parse(text)
        except (ValueError, json.JSONDecodeError):
            return MemoryExtractionResult()

    @staticmethod
    def _prompt(message: str) -> str:
        return f"""Extract only durable, explicitly stated business context from this user message.

Message:
{message}

Allowed memory_type values: company_context, industry, user_role, target_audience, market, business_goal, strategic_priority, brand_positioning, competitor, budget_constraint, business_constraint, marketing_channel, kpi, user_preference.

Store only facts that could improve future personalization across conversations: company details, role, market, audience, goals, positioning, channels, competitors, KPIs, priorities, constraints, or durable preferences. Do not store ordinary one-time questions, temporary instructions, assumptions, secrets, passwords, API keys, tokens, authentication credentials, sensitive personal information, or facts not explicitly stated. When no durable fact exists, return should_store=false and memories=[].

Return ONLY JSON in this shape:
{{"should_store":true,"memories":[{{"memory_type":"business_goal","key":"current_priority","value":"Improve customer retention","confidence":0.95}}]}}
Use lowercase snake_case keys. The output proposes candidates only; it must not perform database operations."""

    @staticmethod
    def _parse(text: str) -> MemoryExtractionResult:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I | re.S).strip()
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("memory extraction did not return a JSON object")
        payload: Any = json.loads(cleaned[start : end + 1])
        result = MemoryExtractionResult.model_validate(payload)
        return result if result.should_store and result.memories else MemoryExtractionResult()
