"""Tenant-aware adapters over the existing retrieval and trace pipeline."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from multimodal_rag.api.config import APISettings, CorpusScope
from multimodal_rag.api.schemas import ChunkResponse, SourceResponse
from multimodal_rag.rag.indexing.chroma_index import IndexNotFoundError, load_index
from multimodal_rag.rag.observability import observed
from multimodal_rag.rag.retrieval.retriever_2 import RetrieverConfig, retrieve
from multimodal_rag.rag.trace import (
    RAGTrace,
    expand_parent_context,
    run_rag_trace,
)

logger = logging.getLogger(__name__)


class UserIndexNotFoundError(Exception):
    """The requested user scope has not been indexed yet."""


@lru_cache(maxsize=256)
def _load_scoped_index(index_dir: str):
    return load_index(index_dir)


def clear_scoped_index_cache() -> None:
    """Drop cached Chroma handles after an ingestion job rebuilds an index."""
    _load_scoped_index.cache_clear()


@dataclass(frozen=True)
class RetrievalResult:
    chunks: list[ChunkResponse]


@dataclass(frozen=True)
class AnswerResult:
    answer: str
    chunks: list[ChunkResponse]
    sources: list[SourceResponse]
    trace: RAGTrace


class RAGService:
    """Uses a distinct on-disk Chroma collection and metadata root for each user."""

    def __init__(self, settings: APISettings) -> None:
        self.settings = settings

    def _load_scope(self, scope: CorpusScope):
        try:
            index, id_map = _load_scoped_index(str(scope.index_dir))
            if index.ntotal <= 0 or not id_map:
                clear_scoped_index_cache()
                raise UserIndexNotFoundError(
                    f"No usable embeddings are available for user_id '{scope.user_id}'. "
                    "Run the ingestion embedding stage before asking questions."
                )
            return index, id_map
        except IndexNotFoundError as exc:
            raise UserIndexNotFoundError(
                f"No index is available for user_id '{scope.user_id}'. "
                "Ingest documents and build that user's index first."
            ) from exc

    @staticmethod
    def _chunk_response(chunk: Any, metadata: dict[str, Any] | None = None) -> ChunkResponse:
        pages = list(getattr(chunk, "page_numbers", []))
        enriched_metadata = dict(metadata or {})
        enriched_metadata.setdefault("chunk_id", chunk.chunk_id)
        enriched_metadata.setdefault("document_id", chunk.document_id)
        enriched_metadata.setdefault("source_file", chunk.source_file)
        enriched_metadata.setdefault("page_numbers", pages)
        enriched_metadata.setdefault("section_title", chunk.section_title)
        enriched_metadata.setdefault("combined_rerank_score", getattr(chunk, "combined_rerank_score", None))
        enriched_metadata.setdefault("bm25_score", getattr(chunk, "bm25_score", None))
        enriched_metadata.setdefault("rrf_score", getattr(chunk, "rrf_score", None))
        return ChunkResponse(
            text=chunk.chunk_text,
            source=chunk.source_file,
            document=chunk.document_id,
            page=pages[0] if pages else None,
            score=float(chunk.score) if chunk.score is not None else None,
            metadata=enriched_metadata,
        )

    @staticmethod
    def _sources(trace: RAGTrace) -> list[SourceResponse]:
        document_ids = {item.chunk_id: item.document_id for item in trace.retrieved_items}
        records = trace.citations or [
            {
                "source_file": item.document_name,
                "chunk_id": item.chunk_id,
                "page_numbers": item.page_numbers,
                "section_title": item.section_title,
                "document_id": item.document_id,
            }
            for item in trace.retrieved_items
        ]
        unique: list[SourceResponse] = []
        seen: set[str] = set()
        for record in records:
            chunk_id = str(record["chunk_id"])
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            unique.append(SourceResponse(
                source=str(record["source_file"]),
                document=str(record.get("document_id") or document_ids.get(chunk_id, record["source_file"])),
                pages=list(record.get("page_numbers", [])),
                chunk_id=chunk_id,
                section_title=record.get("section_title"),
            ))
        return unique

    @staticmethod
    def _trace_payload(trace: RAGTrace) -> dict[str, Any]:
        """Return JSON-safe query diagnostics without re-running RAG stages."""
        return {
            "question": getattr(trace, "original_question", None),
            "retrieval_question": getattr(trace, "retrieval_question", None),
            "retriever": getattr(trace, "retriever", None),
            "embedding_model": getattr(trace, "embedding_model", None),
            "generation_model": getattr(trace, "generation_model", None),
            "configured_top_k": getattr(trace, "configured_top_k", None),
            "actual_retrieved_count": getattr(trace, "actual_retrieved_count", None),
            "retrieval_latency_ms": getattr(trace, "retrieval_latency_ms", None),
            "generation_latency_ms": getattr(trace, "generation_latency_ms", None),
            "rag_latency_ms": getattr(trace, "rag_latency_ms", None),
            "generation_prompt_tokens": getattr(trace, "generation_prompt_tokens", None),
            "generation_completion_tokens": getattr(trace, "generation_completion_tokens", None),
            "generation_total_tokens": getattr(trace, "generation_total_tokens", None),
            "estimated_generation_cost": getattr(trace, "estimated_generation_cost", None),
            "citations": getattr(trace, "citations", []),
            "uncited_sources": getattr(trace, "uncited_sources", []),
            "retrieved_items": [
                {
                    "rank": item.rank,
                    "raw_vector_score": item.raw_vector_score,
                    "chunk_id": item.chunk_id,
                    "document_id": item.document_id,
                    "document_name": item.document_name,
                    "page_numbers": item.page_numbers,
                    "section_title": item.section_title,
                    "chunk_text": item.chunk_text,
                    "metadata": item.metadata,
                    "metadata_note": item.metadata_note,
                    "combined_rerank_score": item.combined_rerank_score,
                    "bm25_score": item.bm25_score,
                    "rrf_score": item.rrf_score,
                    "cross_encoder_score": item.cross_encoder_score,
                }
                for item in getattr(trace, "retrieved_items", [])
            ],
        }

    @observed("rag.retrieve", run_type="retriever")
    def retrieve(self, *, question: str, user_id: str, top_k: int, project_id: str | None = None) -> RetrievalResult:
        scope = self.settings.scope_for(user_id, project_id)
        index, id_map = self._load_scope(scope)
        chunks = retrieve(question, index, id_map, retriever_config=RetrieverConfig(top_k=top_k))
        chunks = expand_parent_context(chunks)
        logger.info("Retrieved %d chunk(s) for user_id=%s", len(chunks), user_id)
        return RetrievalResult([
            self._chunk_response(chunk, chunk.metadata) for chunk in chunks
        ])

    @observed("rag.answer")
    def answer(
        self,
        *,
        question: str,
        user_id: str,
        top_k: int,
        project_id: str | None = None,
        conversation_history: list | None = None,
        retrieval_question: str | None = None,
    ) -> AnswerResult:
        scope = self.settings.scope_for(user_id, project_id)
        index, id_map = self._load_scope(scope)
        trace = run_rag_trace(
            retrieval_question or question,
            index=index,
            id_map=id_map,
            top_k=top_k,
            prompt_question=question,
            conversation_history=conversation_history,
        )
        chunks = [
            ChunkResponse(
                text=item.chunk_text,
                source=item.document_name,
                document=item.document_id,
                page=item.page_numbers[0] if item.page_numbers else None,
                score=item.raw_vector_score,
                metadata=dict(item.metadata or {
                    "chunk_id": item.chunk_id,
                    "document_id": item.document_id,
                    "source_file": item.document_name,
                    "page_numbers": item.page_numbers,
                    "section_title": item.section_title,
                    "combined_rerank_score": item.combined_rerank_score,
                    "bm25_score": item.bm25_score,
                    "rrf_score": item.rrf_score,
                }),
            )
            for item in trace.retrieved_items
        ]
        logger.info(
            "RAG latency user_id=%s retrieval_ms=%.1f generation_ms=%.1f total_ms=%.1f",
            user_id,
            trace.retrieval_latency_ms or 0.0,
            trace.generation_latency_ms or 0.0,
            trace.rag_latency_ms or 0.0,
        )
        logger.info("Generated answer for user_id=%s with %d chunk(s)", user_id, len(chunks))
        return AnswerResult(trace.generated_answer, chunks, self._sources(trace), trace)
