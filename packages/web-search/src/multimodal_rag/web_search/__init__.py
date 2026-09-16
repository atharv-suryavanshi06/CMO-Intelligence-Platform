"""Provider-independent web search contracts and implementations."""

from multimodal_rag.web_search.client import (
    SearchProvider,
    WebSearchClient,
    WebSearchConfigurationError,
    WebSearchError,
    WebSearchProviderError,
)
from multimodal_rag.web_search.models import SearchResult
from multimodal_rag.web_search.models import CompanyResearchResult, CompanyResearchTarget, ResearchSource
from multimodal_rag.web_search.providers.tavily import TavilySearchProvider
from multimodal_rag.web_search.research import CompanyResearchClient, CompanyResearchProvider
from multimodal_rag.web_search.source_guard import SearchSecurityResult, SourceGuard, SourceGuardService

__all__ = [
    "SearchProvider",
    "SearchResult",
    "CompanyResearchClient",
    "CompanyResearchProvider",
    "CompanyResearchResult",
    "CompanyResearchTarget",
    "ResearchSource",
    "TavilySearchProvider",
    "WebSearchClient",
    "WebSearchConfigurationError",
    "WebSearchError",
    "WebSearchProviderError",
    "SearchSecurityResult",
    "SourceGuard",
    "SourceGuardService",
]
