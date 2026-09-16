"""Provider-independent company research workflow."""

from __future__ import annotations

from typing import Protocol

from multimodal_rag.web_search.client import WebSearchProviderError
from multimodal_rag.web_search.models import CompanyResearchResult, CompanyResearchTarget, SearchResult
from multimodal_rag.web_search.source_guard import SourceGuard, SourceGuardService


class CompanyResearchProvider(Protocol):
    """Provider contract for a completed multi-source research report."""

    def research(self, prompt: str) -> CompanyResearchResult:
        """Research a prompt and return a normalized report with citations."""


class CompanyResearchClient:
    """Build a bounded, citation-focused research request for companies."""

    def __init__(self, provider: CompanyResearchProvider, source_guard: SourceGuard | None = None) -> None:
        self.provider = provider
        self.source_guard = source_guard or SourceGuardService()

    def research(
        self,
        question: str,
        companies: list[CompanyResearchTarget],
    ) -> CompanyResearchResult:
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question must not be blank")
        if len(companies) > 10:
            raise ValueError("a maximum of 10 companies can be researched at once")

        targets = "\n".join(f"- {company.name}: {company.url}" for company in companies)
        target_instructions = (
            f"Company targets:\n{targets}"
            if targets
            else "No company targets were supplied; identify the relevant companies from the user's question."
        )
        prompt = f"""Research the following company or companies for a CMO.

User question: {question.strip()}

{target_instructions}

Use the supplied company websites as primary anchors, but search multiple
reliable and recent external sources for corroboration. Focus on information
relevant to the user's question. Return a concise executive summary, not a
long raw webpage dump. Clearly separate verified facts from reasonable
inferences, compare companies when more than one is supplied, and include
numbered source citations in the report. Prioritize current information and
mention uncertainty when evidence is incomplete.
"""
        result = self.provider.research(prompt)
        if not result.sources:
            raise WebSearchProviderError("Tavily research returned no source URLs, so the report could not be safety-validated.")

        report_check = SearchResult(
            title="Tavily research report",
            url=result.sources[0].url,
            content=result.report,
            source="tavily-research",
        )
        if not self.source_guard.filter_results([report_check]):
            raise WebSearchProviderError("Tavily research report was blocked by source security checks.")

        source_checks = [
            SearchResult(title=source.title or "Tavily research source", url=source.url, content="Research citation URL", source="tavily-research")
            for source in result.sources
        ]
        approved_urls = {item.url for item in self.source_guard.filter_results(source_checks)}
        approved_sources = [source for source in result.sources if source.url in approved_urls]
        if not approved_sources:
            raise WebSearchProviderError("All Tavily research sources were blocked by source security checks.")
        return result.model_copy(update={"sources": approved_sources})
