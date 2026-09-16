"""Structured, provider-agnostic trace for one production RAG execution.

This module owns the generic retrieval -> prompt -> generation path and its
diagnostic data. Evaluation and UI layers consume the returned ``RAGTrace``
without re-running any backend step.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

from multimodal_rag.paths import (
    INGESTION_ARTIFACTS_DIR,
    LEGACY_INGESTION_ARTIFACTS_DIR,
)
from multimodal_rag.rag.embedding.embedder import EmbeddingConfig
from multimodal_rag.rag.generation.answer_generator import (
    GenerationConfig,
    GenerationResult,
    generate_answer_with_metadata,
)
from multimodal_rag.rag.generation.citation import resolve_citations
from multimodal_rag.rag.generation.prompt_builder import ConversationTurn, build_prompt
from multimodal_rag.rag.observability import observed
from multimodal_rag.rag.retrieval.retriever_2 import RetrieverConfig, retrieve


@dataclass
class RetrievedItemTrace:
    rank: int
    raw_vector_score: float | None
    chunk_id: str
    document_id: str
    document_name: str
    page_numbers: list[int]
    section_title: str | None
    chunk_text: str
    metadata: dict[str, Any] | None = None
    metadata_note: str | None = None
    combined_rerank_score: float | None = None
    bm25_score: float | None = None
    rrf_score: float | None = None
    cross_encoder_score: float | None = None


@dataclass
class RAGTrace:
    original_question: str
    retrieval_question: str | None = None
    generated_answer: str = ""
    retrieved_items: list[RetrievedItemTrace] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    uncited_sources: list[dict[str, Any]] = field(default_factory=list)
    retriever: str = "multimodal_rag.rag.retrieval.retriever_2"
    embedding_model: str | None = None
    generation_model: str | None = None
    configured_top_k: int = 0
    actual_retrieved_count: int = 0
    retrieval_latency_ms: float | None = None
    generation_latency_ms: float | None = None
    rag_latency_ms: float | None = None
    generation_prompt_tokens: int | None = None
    generation_completion_tokens: int | None = None
    generation_total_tokens: int | None = None
    estimated_generation_cost: float | None = None


def load_chunk_metadata(
    roots: tuple[Path, ...] = (INGESTION_ARTIFACTS_DIR, LEGACY_INGESTION_ARTIFACTS_DIR),
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Read existing ingestion metadata by chunk ID without writing anything."""
    metadata_by_id: dict[str, dict[str, Any]] = {}
    ambiguous: set[str] = set()

    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("chunks.json")):
            try:
                records = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"Could not read chunk metadata from {path}: {exc}") from exc
            if not isinstance(records, list):
                raise RuntimeError(f"Chunk metadata file must contain a JSON list: {path}")

            for record in records:
                if not isinstance(record, dict):
                    continue
                metadata = record.get("metadata")
                if not isinstance(metadata, dict) or metadata.get("chunk_id") is None:
                    continue
                chunk_id = str(metadata["chunk_id"])
                prior = metadata_by_id.get(chunk_id)
                if prior is not None and prior != metadata:
                    ambiguous.add(chunk_id)
                    continue
                metadata_by_id[chunk_id] = dict(metadata)

    return metadata_by_id, ambiguous


def expand_parent_context(chunks: list[Any]) -> list[Any]:
    """Replace a retrieved child body with its stored parent section when available.

    Each retrieved chunk already carries its own resolved ``parent_chunk_text``
    (populated at embedding time and stored on the matched Chroma record - see
    embedder.py and chroma_index.py), so this needs no external lookup and
    never reads chunks.json.
    """
    expanded: list[Any] = []
    for chunk in chunks:
        parent_text = getattr(chunk, "parent_chunk_text", None)
        expanded.append(replace(chunk, chunk_text=parent_text) if parent_text else chunk)
    return expanded


def _citation_dict(citation: Any) -> dict[str, Any]:
    return {
        "marker": citation.marker,
        "source_file": citation.source_file,
        "page_numbers": list(citation.page_numbers),
        "chunk_id": citation.chunk_id,
        "section_title": citation.section_title,
    }


@observed("rag.pipeline")
def run_rag_trace(
    question: str,
    *,
    index,
    id_map,
    top_k: int,
    embedding_config: EmbeddingConfig | None = None,
    generation_config: GenerationConfig | None = None,
    conversation_history: list[ConversationTurn] | None = None,
    max_history_turns: int = 5,
    prompt_question: str | None = None,
    metadata_by_id: dict[str, dict[str, Any]] | None = None,
    ambiguous_metadata: set[str] | None = None,
    retrieve_fn: Callable[..., list[Any]] = retrieve,
    build_prompt_fn: Callable[..., Any] = build_prompt,
    generate_answer_fn: Callable[..., str | GenerationResult] = generate_answer_with_metadata,
    resolve_citations_fn: Callable[..., Any] = resolve_citations,
) -> RAGTrace:
    """Execute retrieval, prompt construction, generation, and citation resolution once.

    ``metadata_by_id``/``ambiguous_metadata`` are optional overrides (used by
    the legacy disk-scanning CLI/evaluation path via ``load_chunk_metadata()``
    - see evaluation/question_runner.py). When omitted, metadata is built
    directly from each retrieved chunk's own ``.metadata`` (sourced from
    Chroma), and no chunks.json is read.

    ``question`` drives retrieval (it can be a narrowed delta query for a
    conversational refinement); ``prompt_question`` - when given - is what
    the user actually asked, and is what gets shown to the LLM as the
    "--- QUESTION ---" and recorded as ``RAGTrace.original_question``, so a
    delta-only retrieval query never leaks into the visible trace or prompt.
    """
    embedding_config = embedding_config or EmbeddingConfig()
    generation_config = generation_config or GenerationConfig()
    if ambiguous_metadata is None:
        ambiguous_metadata = set()
    asked_question = prompt_question or question

    trace = RAGTrace(
        original_question=asked_question,
        retrieval_question=question if prompt_question and prompt_question != question else None,
        embedding_model=embedding_config.model_name,
        generation_model=generation_config.model_name,
        configured_top_k=top_k,
    )

    import time

    rag_start = time.perf_counter()
    retrieval_start = time.perf_counter()
    chunks = retrieve_fn(
        question,
        index,
        id_map,
        embedding_config=embedding_config,
        retriever_config=RetrieverConfig(top_k=top_k),
    )
    trace.retrieval_latency_ms = (time.perf_counter() - retrieval_start) * 1000
    trace.actual_retrieved_count = len(chunks)

    chunks = expand_parent_context(chunks)

    if metadata_by_id is None:
        metadata_by_id = {
            chunk.chunk_id: chunk.metadata
            for chunk in chunks
            if getattr(chunk, "metadata", None)
        }

    for rank, chunk in enumerate(chunks, start=1):
        metadata = metadata_by_id.get(chunk.chunk_id)
        metadata_note = None
        if chunk.chunk_id in ambiguous_metadata:
            metadata = None
            metadata_note = "unavailable: conflicting metadata records for this chunk ID"
        elif metadata is None:
            metadata_note = "unavailable: no stored metadata for this chunk"

        trace.retrieved_items.append(
            RetrievedItemTrace(
                rank=rank,
                raw_vector_score=float(chunk.score) if chunk.score is not None else None,
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                document_name=chunk.source_file,
                page_numbers=list(chunk.page_numbers),
                section_title=chunk.section_title,
                chunk_text=chunk.chunk_text,
                metadata=metadata,
                metadata_note=metadata_note,
                combined_rerank_score=getattr(chunk, "combined_rerank_score", None),
                bm25_score=getattr(chunk, "bm25_score", None),
                rrf_score=getattr(chunk, "rrf_score", None),
                cross_encoder_score=getattr(chunk, "cross_encoder_score", None),
            )
        )

    if chunks:
        built = build_prompt_fn(
            asked_question,
            chunks,
            conversation_history=conversation_history,
            max_history_turns=max_history_turns,
        )
        generation_start = time.perf_counter()
        generation_result = generate_answer_fn(built.prompt_text, generation_config)
        trace.generation_latency_ms = (time.perf_counter() - generation_start) * 1000
        if isinstance(generation_result, GenerationResult):
            raw_answer = generation_result.text
            trace.generation_prompt_tokens = generation_result.prompt_tokens
            trace.generation_completion_tokens = generation_result.completion_tokens
            trace.generation_total_tokens = generation_result.total_tokens
        else:
            raw_answer = generation_result
        cited_answer = resolve_citations_fn(raw_answer, built.source_map)
        trace.generated_answer = cited_answer.answer_text
        trace.citations = [_citation_dict(citation) for citation in cited_answer.citations]
        trace.uncited_sources = [
            _citation_dict(citation) for citation in cited_answer.uncited_sources
        ] if hasattr(cited_answer, "uncited_sources") else []
    else:
        trace.generation_latency_ms = 0.0
        trace.generated_answer = "I couldn't find anything relevant to that in the documents."

    trace.rag_latency_ms = (time.perf_counter() - rag_start) * 1000
    return trace
