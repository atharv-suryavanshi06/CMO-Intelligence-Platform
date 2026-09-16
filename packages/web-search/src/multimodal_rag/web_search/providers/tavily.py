"""Tavily-backed implementation of the shared web-search provider seam."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from multimodal_rag.web_search.client import WebSearchConfigurationError, WebSearchProviderError
from multimodal_rag.web_search.models import CompanyResearchResult, ResearchSource, SearchResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TavilySearchProvider:
    """Synchronous HTTP adapter for Tavily's general search endpoint."""

    api_key: str | None = None
    timeout_seconds: float = 30.0
    research_timeout_seconds: float = 120.0
    research_poll_seconds: float = 2.0

    def search(self, query: str, max_results: int) -> list[SearchResult]:
        api_key = self.api_key or os.getenv("TAVILY_API_KEY")
        if not api_key:
            raise WebSearchConfigurationError("TAVILY_API_KEY is not configured.")

        request = Request(
            "https://api.tavily.com/search",
            data=json.dumps(
                {
                    "api_key": api_key,
                    "query": query,
                    "max_results": max_results,
                    "search_depth": "basic",
                    "topic": "general",
                    "include_answer": False,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            logger.warning("Tavily search failed with HTTP status %s", exc.code)
            raise WebSearchProviderError(f"Tavily search failed with HTTP status {exc.code}.") from exc
        except (URLError, TimeoutError, OSError) as exc:
            logger.warning("Tavily search request failed: %s", type(exc).__name__)
            raise WebSearchProviderError("Tavily search request failed.") from exc
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise WebSearchProviderError("Tavily returned an invalid response.") from exc

        if not isinstance(payload, dict):
            raise WebSearchProviderError("Tavily returned an invalid response.")
        raw_results = payload.get("results") or []
        if not isinstance(raw_results, list):
            raise WebSearchProviderError("Tavily returned an invalid results collection.")
        try:
            return [self._normalize_result(item) for item in raw_results]
        except (TypeError, ValueError) as exc:
            raise WebSearchProviderError("Tavily returned an invalid search result.") from exc

    def research(self, prompt: str) -> CompanyResearchResult:
        """Run Tavily Research and poll until a report is available."""
        api_key = self.api_key or os.getenv("TAVILY_API_KEY")
        if not api_key:
            raise WebSearchConfigurationError("TAVILY_API_KEY is not configured.")

        created = self._research_request(
            api_key,
            "POST",
            "https://api.tavily.com/research",
            {"input": prompt, "model": "mini", "stream": False, "citation_format": "numbered"},
        )
        request_id = created.get("request_id")
        if not request_id:
            raise WebSearchProviderError("Tavily did not return a research request ID.")

        deadline = time.monotonic() + self.research_timeout_seconds
        payload = created
        while payload.get("status") not in {"completed", "failed"}:
            if time.monotonic() >= deadline:
                raise WebSearchProviderError("Tavily research timed out.")
            time.sleep(self.research_poll_seconds)
            payload = self._research_request(
                api_key,
                "GET",
                f"https://api.tavily.com/research/{request_id}",
            )

        if payload.get("status") == "failed":
            raise WebSearchProviderError("Tavily research failed.")
        report = payload.get("content")
        if isinstance(report, dict):
            report = json.dumps(report, indent=2)
        if not isinstance(report, str) or not report.strip():
            raise WebSearchProviderError("Tavily returned an empty research report.")
        raw_sources = payload.get("sources") or []
        if not isinstance(raw_sources, list):
            raise WebSearchProviderError("Tavily returned an invalid source collection.")
        sources = [
            ResearchSource(
                title=str(item.get("title") or ""),
                url=str(item.get("url") or ""),
            )
            for item in raw_sources
            if isinstance(item, dict) and item.get("url")
        ]
        return CompanyResearchResult(report=report.strip(), sources=sources, request_id=str(request_id), raw_content=payload)

    def _research_request(
        self,
        api_key: str,
        method: str,
        url: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request = Request(
            url,
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method=method,
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            logger.warning("Tavily research request failed with HTTP status %s", exc.code)
            raise WebSearchProviderError(f"Tavily research failed with HTTP status {exc.code}.") from exc
        except (URLError, TimeoutError, OSError) as exc:
            logger.warning("Tavily research request failed: %s", type(exc).__name__)
            raise WebSearchProviderError("Tavily research request failed.") from exc
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise WebSearchProviderError("Tavily returned an invalid research response.") from exc
        if not isinstance(payload, dict):
            raise WebSearchProviderError("Tavily returned an invalid research response.")
        return payload

    @staticmethod
    def _normalize_result(item: Any) -> SearchResult:
        if not isinstance(item, dict):
            raise TypeError("search result must be an object")
        score = item.get("score")
        try:
            normalized_score = None if score is None else float(score)
        except (TypeError, ValueError):
            normalized_score = None
        return SearchResult(
            title=str(item.get("title") or ""),
            url=str(item.get("url") or ""),
            content=str(item.get("content") or ""),
            published_at=item.get("published_date") or item.get("published_at") or item.get("published"),
            score=normalized_score,
        )
