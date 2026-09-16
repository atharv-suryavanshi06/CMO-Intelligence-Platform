"""
Citation Mapping Module (RAG Stage 7)
=========================================

Resolves the [S1], [S2]... markers Gemini was instructed (in Stage 5's
prompt) to cite, back to real source_file/page/chunk_id info using the
`source_map` Stage 5 already built. Also reports any SOURCES that were
retrieved but never actually cited in the answer - useful for auditing
retrieval quality (were the top-K results actually relevant?).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from multimodal_rag.rag.retrieval.retriever_2 import RetrievedChunk

_CITATION_MARKER_RE = re.compile(r"\[S(\d+)\]")


@dataclass
class Citation:
    marker: str
    source_file: str
    page_numbers: list[int]
    chunk_id: str
    section_title: str | None


@dataclass
class CitedAnswer:
    answer_text: str
    citations: list[Citation]  # unique, in first-appearance order
    uncited_sources: list[Citation]  # retrieved but never referenced in the answer


def resolve_citations(answer_text: str, source_map: dict[str, RetrievedChunk]) -> CitedAnswer:
    cited_markers_in_order: list[str] = []
    seen = set()
    for match in _CITATION_MARKER_RE.finditer(answer_text):
        marker = f"S{match.group(1)}"
        if marker not in seen and marker in source_map:
            seen.add(marker)
            cited_markers_in_order.append(marker)

    def _to_citation(marker: str) -> Citation:
        chunk = source_map[marker]
        return Citation(
            marker=marker, source_file=chunk.source_file, page_numbers=chunk.page_numbers,
            chunk_id=chunk.chunk_id, section_title=chunk.section_title,
        )

    citations = [_to_citation(m) for m in cited_markers_in_order]
    uncited = [_to_citation(m) for m in source_map if m not in seen]

    return CitedAnswer(answer_text=answer_text, citations=citations, uncited_sources=uncited)
