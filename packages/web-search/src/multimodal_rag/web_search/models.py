"""Normalized models returned by external web-search providers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class SearchResult(BaseModel):
    """Provider-neutral representation of one web-search result."""

    title: str
    url: str
    content: str
    source: str = "tavily"
    published_at: datetime | None = None
    score: float | None = None

    @field_validator("published_at", mode="before")
    @classmethod
    def invalid_publication_date_is_unknown(cls, value: object) -> object:
        if value is None or isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        return None


class CompanyResearchTarget(BaseModel):
    """A company identity and canonical website supplied for research."""

    name: str
    url: str


class ResearchSource(BaseModel):
    """A source cited by a Tavily research report."""

    title: str = ""
    url: str


class CompanyResearchResult(BaseModel):
    """Normalized completed company-research response."""

    report: str
    sources: list[ResearchSource] = Field(default_factory=list)
    request_id: str | None = None
    raw_content: Any = None
