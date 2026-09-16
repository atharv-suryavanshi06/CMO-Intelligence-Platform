"""Typed RAG seam used by agents; agents do not implement retrieval or HTTP."""

from __future__ import annotations

from typing import Protocol

from multimodal_rag.agents.models import AgentEvidence
from multimodal_rag.api.service import RAGService, UserIndexNotFoundError


class RAGClientError(RuntimeError):
    """Base error for the agent-facing retrieval seam."""


class RAGIndexNotFoundError(RAGClientError):
    """The requested user/project corpus has not been indexed."""


class RAGClient(Protocol):
    def retrieve(
        self,
        *,
        question: str,
        user_id: str,
        top_k: int,
        project_id: str | None = None,
    ) -> list[AgentEvidence]:
        """Retrieve scoped evidence for an agent objective."""


class InProcessRAGClient:
    """Adapter around the existing RAG service for local agent execution."""

    def __init__(self, service: RAGService) -> None:
        self.service = service

    def retrieve(
        self,
        *,
        question: str,
        user_id: str,
        top_k: int,
        project_id: str | None = None,
    ) -> list[AgentEvidence]:
        try:
            result = self.service.retrieve(
                question=question,
                user_id=user_id,
                top_k=top_k,
                project_id=project_id,
            )
        except UserIndexNotFoundError as exc:
            raise RAGIndexNotFoundError(str(exc)) from exc

        evidence: list[AgentEvidence] = []
        for chunk in result.chunks:
            metadata = dict(chunk.metadata or {})
            pages = list(metadata.get("page_numbers") or ([] if chunk.page is None else [chunk.page]))
            evidence.append(
                AgentEvidence(
                    chunk_id=str(metadata.get("chunk_id") or f"{chunk.document}:{chunk.page}"),
                    source=chunk.source,
                    document=chunk.document,
                    page=chunk.page,
                    pages=pages,
                    score=chunk.score,
                    text_excerpt=chunk.text,
                    metadata=metadata,
                )
            )
        return evidence
