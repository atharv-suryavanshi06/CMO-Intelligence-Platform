"""Provider-independent web-search client seam."""

from __future__ import annotations

from typing import Protocol

from multimodal_rag.web_search.models import SearchResult


class WebSearchError(RuntimeError):
    """Base error for web-search operations."""


class WebSearchConfigurationError(WebSearchError):
    """Raised when a provider cannot run with the configured credentials."""


class WebSearchProviderError(WebSearchError):
    """Raised when a provider request or response cannot be completed safely."""


class SearchProvider(Protocol):
    """The minimal provider contract consumed by :class:`WebSearchClient`."""

    def search(self, query: str, max_results: int) -> list[SearchResult]:
        """Return normalized results for a query."""


class WebSearchClient:
    """Small application-facing wrapper around an injected search provider."""

    def __init__(self, provider: SearchProvider) -> None:
        self.provider = provider

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        """Validate a query and delegate it to the configured provider."""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must not be blank")
        if isinstance(max_results, bool) or not isinstance(max_results, int) or not 1 <= max_results <= 20:
            raise ValueError("max_results must be an integer between 1 and 20")
        return self.provider.search(query.strip(), max_results)
