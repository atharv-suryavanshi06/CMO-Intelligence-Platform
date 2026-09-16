"""
Retriever Module (RAG Stage 4)
=================================

Turns a user's text query into ranked, relevant chunks: embeds the query
using the SAME model/config as Stage 2 (embedder.py), then searches the
ChromaDB index built in Stage 3 (chroma_index.py). Deliberately thin - all
the real logic (embedding, index search) already exists; this module's
job is just correct composition + result shaping.
"""

from __future__ import annotations

import logging
import math
import re
import unicodedata
from dataclasses import dataclass, field
from dataclasses import replace

from multimodal_rag.rag.embedding.embedder import EmbeddingConfig, _embed_texts
from multimodal_rag.rag.indexing.chroma_index import IndexedChunkRef, search

logger = logging.getLogger(__name__)

_STOPWORDS = {
    "what", "are", "is", "was", "were", "a", "an", "the", "of", "in", "on",
    "for", "to", "and", "or", "with", "as", "by", "at", "from", "be", "been",
    "it", "its", "do", "does", "did", "have", "has", "had", "can", "could",
    "will", "would", "should", "this", "that", "these", "those",
}
# Standard English stopwords - excluded so overlap scoring reflects real
# topic/polarity words rather than near-universal function words that would
# overlap with almost any chunk.

# Hybrid fusion needs a broader pool than its final top-k so an evidence chunk
# that is moderately ranked by dense search but strongly ranked by BM25 (or
# vice versa) is still eligible for fusion.  40 raised exact-label recall on
# the verified CMO evaluation without increasing the API embedding calls.
_DEFAULT_CANDIDATE_POOL = 40
_DEFAULT_RRF_K = 60
_DEFAULT_CROSS_ENCODER_MODEL = "BAAI/bge-reranker-base"


def _normalize_lexical_text(text: str) -> str:
    """Normalize Unicode and punctuation boundaries before lexical scoring."""
    normalized = unicodedata.normalize("NFKC", text or "").lower()
    return re.sub(r"[^a-z0-9]+", " ", normalized)


def _tokenize(text: str) -> set[str]:
    return {
        token for token in _normalize_lexical_text(text).split()
        if token not in _STOPWORDS
    }


def _lexical_overlap(query_tokens: set[str], chunk_text: str) -> float:
    """
    Fraction of query_tokens also present in chunk_text (0..1). The dense
    captures topic similarity well but underweights lexical/polarity
    differences (e.g. "short-term" vs "long-term" score as near-
    identical topically) - this recovers that signal cheaply, as a
    post-vector reranking pass, without touching the embedding model.
    """
    if not query_tokens:
        return 0.0
    return len(query_tokens & _tokenize(chunk_text)) / len(query_tokens)


@dataclass
class RetrievedChunk:
    chunk_id: str
    document_id: str
    source_file: str
    page_numbers: list[int]
    section_title: str | None
    chunk_text: str
    score: float | None
    combined_rerank_score: float | None = field(default=None, compare=False)
    bm25_score: float | None = field(default=None, compare=False)
    rrf_score: float | None = field(default=None, compare=False)
    cross_encoder_score: float | None = field(default=None, compare=False)
    metadata: dict | None = field(default=None, compare=False)
    parent_chunk_text: str | None = field(default=None, compare=False)
    # Sourced directly from the matched Chroma record (see chroma_index.py)
    # so downstream trace/context-expansion never needs to read chunks.json.


@dataclass
class RetrieverConfig:
    top_k: int = 5
    min_score: float = 0.0
    # Results below this cosine-similarity score are dropped - default 0
    # keeps all top_k results; raise it to filter out weak matches when a
    # query has no genuinely relevant chunks in the corpus.
    lexical_rerank_weight: float = 0.15
    # Blends a lexical token-overlap score into cosine ranking:
    # final_score = cosine_score + lexical_rerank_weight * overlap_fraction.
    # 0.15 is the minimum weight (derived from real scored examples, not
    # guessed) that corrects cases where the dense model ranks a topically-similar
    # but lexically/polarity-wrong chunk above the correct one - e.g. for
    # "long-term technology implications", a "Measurement" chunk (cosine
    # 0.3799, overlap 2/3 query terms) was outranking the correct
    # "Technology" chunk (cosine 0.3361, overlap 3/3 query terms).
    # Minimum flip weight = (0.3799-0.3361)/(1.0-0.667) ≈ 0.1315; 0.15
    # adds a small margin. Set to 0.0 to disable reranking entirely.
    enable_hybrid: bool = True
    enable_keyword_only: bool = False
    dense_candidate_k: int = _DEFAULT_CANDIDATE_POOL
    sparse_candidate_k: int = _DEFAULT_CANDIDATE_POOL
    rrf_k: int = _DEFAULT_RRF_K
    enable_cross_encoder: bool = False
    cross_encoder_model: str = _DEFAULT_CROSS_ENCODER_MODEL
    bm25_k1: float = 1.5
    bm25_b: float = 0.75


_cross_encoder = None
_cross_encoder_name: str | None = None


def _bm25_rank(
    query: str,
    id_map: dict[int, IndexedChunkRef],
    top_k: int,
    *,
    k1: float,
    b: float,
) -> list[tuple[IndexedChunkRef, float]]:
    """Rank the indexed chunk corpus with a dependency-free BM25 scorer."""
    query_tokens = _tokenize(query)
    if not query_tokens or top_k <= 0:
        return []

    refs = list(id_map.values())
    tokenized = [(ref, _tokenize(ref.chunk_text)) for ref in refs]
    document_frequency = {
        token: sum(token in tokens for _ref, tokens in tokenized)
        for token in query_tokens
    }
    average_length = sum(len(tokens) for _ref, tokens in tokenized) / max(len(tokenized), 1)
    document_count = len(tokenized)
    scored: list[tuple[IndexedChunkRef, float]] = []
    for ref, tokens in tokenized:
        document_length = len(tokens)
        score = 0.0
        for token in query_tokens:
            frequency = sum(1 for value in tokens if value == token)
            if not frequency:
                continue
            df = document_frequency[token]
            idf = math.log(1.0 + (document_count - df + 0.5) / (df + 0.5))
            denominator = frequency + k1 * (
                1.0 - b + b * document_length / max(average_length, 1.0)
            )
            score += idf * (frequency * (k1 + 1.0)) / denominator
        if score > 0.0:
            scored.append((ref, score))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[:top_k]


def _get_cross_encoder(model_name: str):
    global _cross_encoder, _cross_encoder_name
    if _cross_encoder is not None and _cross_encoder_name == model_name:
        return _cross_encoder
    try:
        from sentence_transformers import CrossEncoder
        _cross_encoder = CrossEncoder(model_name, local_files_only=True)
    except Exception as exc:
        raise RuntimeError(
            f"Cross-encoder '{model_name}' is not available locally. "
            "Provision its cached model before enabling cross-encoder reranking."
        ) from exc
    _cross_encoder_name = model_name
    return _cross_encoder


def _cross_encoder_rank(query: str, candidates: list[IndexedChunkRef], model_name: str):
    model = _get_cross_encoder(model_name)
    pairs = [(query, candidate.chunk_text) for candidate in candidates]
    scores = model.predict(pairs)
    return [float(score) for score in scores]


def retrieve(
    query: str,
    index,
    id_map: dict[int, IndexedChunkRef],
    embedding_config: EmbeddingConfig | None = None,
    retriever_config: RetrieverConfig | None = None,
    embed_fn=None,
) -> list[RetrievedChunk]:
    """
    Embed `query` and return its top-K most similar chunks from the
    index. `embed_fn` is injectable (same seam as embedder.py) for
    testing without the real model.
    """
    embedding_config = embedding_config or EmbeddingConfig()
    retriever_config = retriever_config or RetrieverConfig()
    embed_fn = embed_fn or _embed_texts

    if not query or not query.strip():
        raise ValueError("Query must not be empty.")

    # A BM25-only path is useful both as a low-latency fallback and as an
    # independent baseline in retrieval evaluation.  It intentionally avoids
    # an embedding API call altogether.
    if retriever_config.enable_keyword_only:
        sparse_results = _bm25_rank(
            query,
            id_map,
            retriever_config.top_k,
            k1=retriever_config.bm25_k1,
            b=retriever_config.bm25_b,
        )
        results = [
            RetrievedChunk(
                chunk_id=ref.chunk_id,
                document_id=ref.document_id,
                source_file=ref.source_file,
                page_numbers=ref.page_numbers,
                section_title=ref.section_title,
                chunk_text=ref.chunk_text,
                score=None,
                combined_rerank_score=score,
                bm25_score=score,
                metadata=ref.metadata,
                parent_chunk_text=ref.parent_chunk_text,
            )
            for ref, score in sparse_results
        ]
        logger.info("Retrieved %d keyword-only chunk(s) for query (top_k=%d)", len(results), retriever_config.top_k)
        return results

    query_config = replace(embedding_config, input_type="query")
    query_vector = embed_fn([query.strip()], query_config)[0]
    candidate_k = max(
        retriever_config.top_k,
        retriever_config.dense_candidate_k if retriever_config.enable_hybrid else _DEFAULT_CANDIDATE_POOL,
    )
    raw_results = search(index, id_map, query_vector, top_k=candidate_k)

    if retriever_config.enable_hybrid:
        sparse_results = _bm25_rank(
            query,
            id_map,
            retriever_config.sparse_candidate_k,
            k1=retriever_config.bm25_k1,
            b=retriever_config.bm25_b,
        )
        dense_by_id = {ref.chunk_id: (ref, score, rank) for rank, (ref, score) in enumerate(raw_results, 1)}
        sparse_by_id = {ref.chunk_id: (ref, score, rank) for rank, (ref, score) in enumerate(sparse_results, 1)}
        candidate_ids = set(dense_by_id) | set(sparse_by_id)
        fused = []
        for chunk_id in candidate_ids:
            dense = dense_by_id.get(chunk_id)
            sparse = sparse_by_id.get(chunk_id)
            ref = dense[0] if dense is not None else sparse[0]
            raw_score = dense[1] if dense is not None else None
            bm25_score = sparse[1] if sparse is not None else None
            rrf_score = (
                (1.0 / (retriever_config.rrf_k + dense[2])) if dense is not None else 0.0
            ) + (
                (1.0 / (retriever_config.rrf_k + sparse[2])) if sparse is not None else 0.0
            )
            fused.append((ref, raw_score, bm25_score, rrf_score))
        # Prior code calculated ``combined_rerank_score`` below but sorted
        # solely by RRF first, making lexical_rerank_weight a no-op whenever
        # hybrid retrieval was enabled. Apply the documented lexical overlap
        # signal to the actual fusion order, with RRF retaining both dense and
        # BM25 rank evidence.
        query_tokens = _tokenize(query) if retriever_config.lexical_rerank_weight else set()
        fused.sort(
            key=lambda item: (
                item[3] + retriever_config.lexical_rerank_weight * _lexical_overlap(query_tokens, item[0].chunk_text),
                item[0].chunk_id,
            ),
            reverse=True,
        )
        if retriever_config.enable_cross_encoder:
            cross_scores = _cross_encoder_rank(
                query,
                [item[0] for item in fused],
                retriever_config.cross_encoder_model,
            )
            fused = [
                item + (cross_score,)
                for item, cross_score in zip(fused, cross_scores)
            ]
            fused.sort(key=lambda item: (item[4], item[0].chunk_id), reverse=True)
        else:
            fused = [item + (None,) for item in fused]

        eligible_results = [
            RetrievedChunk(
                chunk_id=ref.chunk_id,
                document_id=ref.document_id,
                source_file=ref.source_file,
                page_numbers=ref.page_numbers,
                section_title=ref.section_title,
                chunk_text=ref.chunk_text,
                score=raw_score,
                combined_rerank_score=(
                    rrf_score + retriever_config.lexical_rerank_weight
                    * _lexical_overlap(query_tokens, ref.chunk_text)
                ),
                bm25_score=bm25_score,
                rrf_score=rrf_score,
                cross_encoder_score=cross_score,
                metadata=ref.metadata,
                parent_chunk_text=ref.parent_chunk_text,
            )
            for ref, raw_score, bm25_score, rrf_score, cross_score in fused
            if raw_score is None or raw_score >= retriever_config.min_score
        ]
        results = eligible_results[:retriever_config.top_k]
        logger.info("Retrieved %d hybrid chunk(s) for query (top_k=%d)", len(results), retriever_config.top_k)
        return results

    query_tokens = _tokenize(query) if retriever_config.lexical_rerank_weight else set()
    scored_results = [
        (
            ref,
            score,
            score
            + retriever_config.lexical_rerank_weight
            * _lexical_overlap(query_tokens, ref.chunk_text),
        )
        for ref, score in raw_results
    ]
    if retriever_config.lexical_rerank_weight:
        scored_results = sorted(scored_results, key=lambda result: result[2], reverse=True)

    eligible_results = [
        RetrievedChunk(
            chunk_id=ref.chunk_id, document_id=ref.document_id, source_file=ref.source_file,
            page_numbers=ref.page_numbers, section_title=ref.section_title,
            chunk_text=ref.chunk_text, score=score,
            combined_rerank_score=combined_score,
            metadata=ref.metadata,
            parent_chunk_text=ref.parent_chunk_text,
        )
        for ref, score, combined_score in scored_results
        if score >= retriever_config.min_score
    ]
    results = eligible_results[:retriever_config.top_k]
    logger.info("Retrieved %d chunk(s) for query (top_k=%d)", len(results), retriever_config.top_k)
    return results
