"""Validated contracts for the persistent business-memory boundary."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

MemoryType = Literal[
    "company_context",
    "industry",
    "user_role",
    "target_audience",
    "market",
    "business_goal",
    "strategic_priority",
    "brand_positioning",
    "competitor",
    "budget_constraint",
    "business_constraint",
    "marketing_channel",
    "kpi",
    "user_preference",
]

_MEMORY_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class MemoryCandidate(BaseModel):
    """One LLM-proposed memory; it has no persistence capability."""

    memory_type: MemoryType
    key: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=1_000)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("key")
    @classmethod
    def key_must_be_stable_identifier(cls, value: str) -> str:
        normalized = value.strip().lower().replace(" ", "_").replace("-", "_")
        if not _MEMORY_KEY.fullmatch(normalized):
            raise ValueError("memory key must use lowercase letters, numbers, and underscores")
        return normalized

    @field_validator("value")
    @classmethod
    def value_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("memory value must not be blank")
        return value


class MemoryExtractionResult(BaseModel):
    """Structured output from :class:`MemoryExtractor`."""

    should_store: bool = False
    memories: list[MemoryCandidate] = Field(default_factory=list, max_length=12)
