"""
Embedding Generator Module (RAG Stage 2)
===========================================

First module of the RAG system proper, sitting immediately after
ingestion. Consumes the `chunks.json` files ingestion (Modules 1-10)
already produces per document, and turns each chunk's text into a
 dense vector embedding, ready for ChromaDB indexing (Stage 3 - not built
yet).

This module does NOT modify, import internals from, or depend on
anything in `ingestion/` beyond reading its already-finalized JSON
output - kept deliberately decoupled, per the "don't rewrite existing
modules" requirement. It reads a public, stable file format
(`chunks.json`), not ingestion's internal Python objects.

Design (consistent with the ingestion pipeline's established patterns):
- Config-driven (EmbeddingConfig), with Gemini Embedding 2 as the default.
- The Gemini client is loaded lazily, once, as a module-level singleton -
  client construction and API setup should not happen per document.
- Never silently drops a chunk: if a chunk's text is empty/unembeddable,
  it's skipped with a logged reason and reported in the returned
  EmbeddingResult's `skipped` list, not just absent with no explanation.

Gemini Embedding 2 is a hosted model and requires GEMINI_API_KEY. Every
piece of this module's OWN logic (chunk loading, batching, skip-handling,
output writing) remains testable against an injected embedding function;
the real provider seam is `_embed_texts`.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_MODEL_NAME = "gemini-embedding-2"


def _default_output_dimension() -> int:
    configured = os.getenv("GEMINI_EMBEDDING_DIMENSION", str(768))
    try:
        dimension = int(configured)
    except ValueError:
        logger.warning("Invalid GEMINI_EMBEDDING_DIMENSION=%r; using 768", configured)
        return 768
    if not 128 <= dimension <= 3072:
        logger.warning("GEMINI_EMBEDDING_DIMENSION must be between 128 and 3072; using 768")
        return 768
    return dimension


DEFAULT_OUTPUT_DIMENSION = _default_output_dimension()


class EmbeddingError(Exception):
    """Base exception for embedding generation failures."""


class EmbeddingModelUnavailableError(EmbeddingError):
    """Raised when the embedding model's weights could not be loaded
    (e.g. blocked Hugging Face access, no cached weights) - an
    operational/environment problem, distinguished from a genuine bug."""


@dataclass
class EmbeddingConfig:
    model_name: str = DEFAULT_MODEL_NAME
    device: str = "auto"
    batch_size: int = 32
    # Optional pacing between Gemini batch requests. This is useful for the
    # free-tier per-minute input quota; production callers can leave it at 0.
    request_delay_seconds: float = 0.0
    # Gemini's free tier counts each input in a batch against a per-minute
    # quota. Leave a small buffer below the published limit so a document
    # with more than one batch can continue in the next minute.
    max_inputs_per_minute: int = 96
    rate_limit_window_seconds: float = 60.0
    max_rate_limit_retries: int = 8
    output_dimensionality: int = DEFAULT_OUTPUT_DIMENSION
    input_type: str = "document"
    normalize_embeddings: bool = True
    # Gemini Embedding 2 normalizes supported truncated dimensions, and we
    # retain explicit normalization for compatibility with any future model
    # or dimension override. IndexFlatIP therefore remains cosine similarity.
    min_chunk_chars: int = 3
    # Chunks shorter than this after stripping are skipped rather than
    # embedded - an empty or near-empty chunk produces a low-information
    # embedding that mostly adds retrieval noise, not signal.
    # NOTE: left unchanged/untouched by the heading-only filter below -
    # this still governs "is there any text at all", nothing more.

    heading_only_char_threshold: int = 15
    # Separate, narrower check than min_chunk_chars: a chunk can be
    # longer than min_chunk_chars in raw length yet still be almost
    # entirely headings/page artifacts (e.g. "Long-term implications /
    # Technology / Knowledge Institute"), which otherwise get an
    # artificially strong embedding similarity from having almost no
    # real content to dilute the topic words. Chunks whose EFFECTIVE
    # body length (see _effective_body_length) falls below this are
    # skipped as heading-only, regardless of raw length. Deliberately
    # does NOT change min_chunk_chars behavior for legitimate small
    # chunks - this only catches the heading-artifact case.


@dataclass
class EmbeddedChunk:
    chunk_id: str
    document_id: str
    source_file: str
    page_numbers: list[int]
    section_title: str | None
    chunk_text: str
    embedding_index: int
    # Position of this chunk's vector within the returned embeddings
    # array - the join key between EmbeddingResult.embeddings[i] and
    # EmbeddingResult.chunks[i]. Kept explicit (not just relying on list
    # order) because Stage 3 will persist these to disk separately from
    # the raw vector array, where list-order-as-identity is fragile.
    parent_chunk_text: str | None = None
    # Resolved from this child's parent_chunk_id against the SAME
    # chunks.json record set (parents are skipped for embedding, not
    # dropped) - carried alongside the child so Stage 3 can store it
    # directly on the Chroma record. This is what lets query-time parent
    # context expansion read from Chroma alone, with no disk lookup.
    metadata: dict = field(default_factory=dict)
    # The full ChunkMetadata dict from chunks.json, preserved verbatim so
    # Stage 3/4 can round-trip it through Chroma for trace/debug display
    # without re-reading chunks.json at query time.


@dataclass
class SkippedChunk:
    chunk_id: str
    reason: str


@dataclass
class EmbeddingResult:
    embeddings: np.ndarray  # shape (N, embedding_dim), float32
    chunks: list[EmbeddedChunk]  # len N, chunks[i] corresponds to embeddings[i]
    skipped: list[SkippedChunk]
    model_name: str
    embedding_dim: int


_PROGRESS_VECTORS_FILE = ".embedding_progress.npy"
_PROGRESS_METADATA_FILE = ".embedding_progress.json"


def _progress_paths(output_dir: str | Path) -> tuple[Path, Path]:
    directory = Path(output_dir)
    return directory / _PROGRESS_VECTORS_FILE, directory / _PROGRESS_METADATA_FILE


def _load_embedding_progress(
    output_dir: str | Path,
    chunks: list[EmbeddedChunk],
    config: EmbeddingConfig,
) -> np.ndarray | None:
    """Load a compatible per-document batch checkpoint, if one exists."""
    vectors_path, metadata_path = _progress_paths(output_dir)
    if not vectors_path.exists() or not metadata_path.exists():
        return None
    try:
        metadata = json.loads(metadata_path.read_text())
        chunk_ids = [chunk.chunk_id for chunk in chunks]
        if (
            metadata.get("model_name") != config.model_name
            or metadata.get("output_dimensionality") != config.output_dimensionality
            or metadata.get("chunk_ids") != chunk_ids
        ):
            raise ValueError("checkpoint does not match the current embedding input")
        vectors = np.load(vectors_path, allow_pickle=False).astype(np.float32)
        completed = metadata.get("completed_count")
        if vectors.ndim != 2 or vectors.shape[0] != completed or completed > len(chunks):
            raise ValueError("checkpoint vectors are incomplete or malformed")
        logger.info("Resuming %d completed embedding chunks from %s", completed, vectors_path)
        return vectors
    except (OSError, ValueError, json.JSONDecodeError) as error:
        logger.warning("Ignoring invalid embedding checkpoint in %s: %s", vectors_path.parent, error)
        return None


def _write_embedding_progress(
    output_dir: str | Path,
    vectors: np.ndarray,
    chunks: list[EmbeddedChunk],
    config: EmbeddingConfig,
) -> None:
    """Persist successfully embedded batches so a rate-limit retry can resume."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    vectors_path, metadata_path = _progress_paths(output_dir)
    temporary_vectors = vectors_path.with_name(f"{vectors_path.stem}.tmp.npy")
    np.save(temporary_vectors, vectors.astype(np.float32))
    temporary_vectors.replace(vectors_path)
    metadata = {
        "model_name": config.model_name,
        "output_dimensionality": config.output_dimensionality,
        "chunk_ids": [chunk.chunk_id for chunk in chunks],
        "completed_count": int(vectors.shape[0]),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2))


def _clear_embedding_progress(output_dir: str | Path) -> None:
    for path in _progress_paths(output_dir):
        path.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# Heading-only / page-artifact detection
# --------------------------------------------------------------------------

_HEADING_LINE_RE = re.compile(r"^[A-Z][\w\s\-&]{0,40}$")
# A line counts as "heading-like" if it starts capitalized, is short
# (<=~6 words judged below), and carries no terminal sentence
# punctuation - matches patterns like "Long-term implications" or
# "Knowledge Institute", not real body sentences.


def _effective_body_length(text: str) -> int:
    """
    Length of `text` with heading-like lines stripped out. Used only to
    detect chunks that are almost entirely headings/page artifacts (a
    narrower, separate check from min_chunk_chars - see
    EmbeddingConfig.heading_only_char_threshold). Such chunks otherwise
    pass the normal length check while carrying almost no real content,
    letting them dominate cosine similarity with topic words alone.
    """
    body_lines = [
        line for line in text.splitlines()
        if not (
            _HEADING_LINE_RE.match(line.strip())
            and len(line.split()) <= 6
            and not line.strip().endswith((".", "!", "?", ":", ";", ","))
        )
    ]
    return len(" ".join(body_lines).strip())


# --------------------------------------------------------------------------
# Loading chunks.json (ingestion's public output format)
# --------------------------------------------------------------------------

def load_chunks_json(chunks_json_path: str | Path) -> list[dict]:
    """
    Load a document's chunks.json, exactly as Module 9's Output Writer
    produces it: `[{"chunk_text": ..., "metadata": {...}}, ...]`.

    Raises FileNotFoundError / json.JSONDecodeError directly (not wrapped)
    - a missing or malformed chunks.json is a whole-document problem for
    THIS stage, analogous to how ingestion's own PDFLoadError works: fail
    loudly and immediately, don't guess at partial content.
    """
    path = Path(chunks_json_path)
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise ValueError(f"'{path}' does not contain a JSON list of chunks (got {type(data).__name__})")
    return data


# --------------------------------------------------------------------------
# Model loading (lazy Gemini client singleton)
# --------------------------------------------------------------------------

_client = None
_client_key: str | None = None


def _get_model(config: EmbeddingConfig):
    """Return the lazily-created Gemini client (legacy function name kept)."""
    global _client, _client_key

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise EmbeddingModelUnavailableError(
            "GEMINI_API_KEY or GOOGLE_API_KEY is not set; Gemini embedding generation is unavailable."
        )
    if _client is not None and _client_key == api_key:
        return _client

    try:
        from google import genai
    except ImportError as e:
        raise EmbeddingModelUnavailableError(
            f"google-genai is not installed: {e}"
        ) from e

    try:
        logger.info("Initializing Gemini embedding client for '%s'", config.model_name)
        _client = genai.Client(api_key=api_key)
        _client_key = api_key
    except Exception as e:
        raise EmbeddingModelUnavailableError(f"Could not initialize Gemini embedding client: {e}") from e

    return _client


def _retry_delay_seconds(error: Exception) -> float | None:
    """Return Gemini's suggested retry delay when present in an API error."""
    match = re.search(r"retry in\s+([\d.]+)s", str(error), flags=re.IGNORECASE)
    return float(match.group(1)) if match else None


def _embed_texts(
    texts: list[str],
    config: EmbeddingConfig,
    *,
    initial_vectors: np.ndarray | None = None,
    on_batch_complete=None,
) -> np.ndarray:
    """
    The one function that actually calls the model - deliberately
    isolated as a single seam so every OTHER piece of this module's logic
    (chunk loading, filtering, batching orchestration, result assembly,
    output writing) can be tested against a fake/injected version of just
    this function, without needing the real model to load. See module
    docstring's HONEST LIMITATION section.
    """
    client = _get_model(config)
    try:
        from google.genai import types

        prepared = []
        for text in texts:
            if config.input_type == "query":
                prepared.append(f"task: search result | query: {text}")
            else:
                prepared.append(f"title: none | text: {text}")

        vectors: list[list[float]] = (
            initial_vectors.astype(np.float32).tolist()
            if initial_vectors is not None else []
        )
        start_index = len(vectors)
        if start_index > len(prepared):
            raise EmbeddingError("Embedding checkpoint contains more vectors than input chunks.")

        window_started_at = time.monotonic()
        inputs_in_window = 0
        batch_size = max(1, config.batch_size)
        for start in range(start_index, len(prepared), batch_size):
            batch = prepared[start : start + batch_size]
            if config.max_inputs_per_minute > 0:
                now = time.monotonic()
                elapsed = now - window_started_at
                if elapsed >= config.rate_limit_window_seconds:
                    window_started_at, inputs_in_window = now, 0
                if inputs_in_window and inputs_in_window + len(batch) > config.max_inputs_per_minute:
                    delay = max(0.0, config.rate_limit_window_seconds - elapsed)
                    if delay:
                        logger.info("Embedding rate-limit window reached; waiting %.1fs", delay)
                        time.sleep(delay)
                    window_started_at, inputs_in_window = time.monotonic(), 0
            contents = [
                types.Content(parts=[types.Part.from_text(text=value)])
                for value in batch
            ]
            for attempt in range(config.max_rate_limit_retries + 1):
                try:
                    result = client.models.embed_content(
                        model=config.model_name,
                        contents=contents,
                        config=types.EmbedContentConfig(
                            output_dimensionality=config.output_dimensionality,
                        ),
                    )
                    break
                except Exception as error:
                    retry_after = _retry_delay_seconds(error)
                    if retry_after is None or attempt >= config.max_rate_limit_retries:
                        raise
                    delay = max(config.request_delay_seconds, retry_after)
                    logger.warning(
                        "Gemini rate-limited embedding batch %d; retrying in %.1fs (%d/%d)",
                        start // batch_size + 1, delay, attempt + 1, config.max_rate_limit_retries,
                    )
                    time.sleep(delay)
                    window_started_at, inputs_in_window = time.monotonic(), 0
            embeddings = getattr(result, "embeddings", None) or []
            if len(embeddings) != len(batch):
                raise EmbeddingError(
                    f"Gemini returned {len(embeddings)} embeddings for {len(batch)} inputs."
                )
            vectors.extend([list(embedding.values) for embedding in embeddings])
            inputs_in_window += len(batch)
            if on_batch_complete:
                on_batch_complete(np.asarray(vectors, dtype=np.float32))
            if config.request_delay_seconds > 0 and start + len(batch) < len(prepared):
                time.sleep(config.request_delay_seconds)

        matrix = np.asarray(vectors, dtype=np.float32)
        if config.normalize_embeddings and matrix.size:
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            matrix = matrix / np.maximum(norms, 1e-12)
        return matrix
    except EmbeddingError:
        raise
    except Exception as e:
        raise EmbeddingError(f"Gemini embedding request failed: {e}") from e


class GeminiEmbeddings:
    """Small LangChain-compatible adapter for RAGAS evaluator embeddings."""

    def __init__(self, config: EmbeddingConfig | None = None) -> None:
        self.config = config or EmbeddingConfig()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        config = replace(self.config, input_type="document")
        return _embed_texts(texts, config).tolist()

    def embed_query(self, text: str) -> list[float]:
        config = replace(self.config, input_type="query")
        return _embed_texts([text], config)[0].tolist()


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------

def embed_chunks(
    chunk_records: list[dict],
    config: EmbeddingConfig | None = None,
    embed_fn=None,
    checkpoint_dir: str | Path | None = None,
) -> EmbeddingResult:
    """
    Embed a list of chunk records (as loaded via `load_chunks_json`).

    `embed_fn` is an injectable dependency defaulting to `_embed_texts`
    (the real model call) - the same seam-injection pattern already used
    for Docling's `segment_fn` in the orchestrator, for the same reason:
    the real model call can't run in this sandbox, but every other piece
    of logic here can and is tested against an injected fake.
    """
    config = config or EmbeddingConfig()
    use_default_embedder = embed_fn is None
    embed_fn = embed_fn or _embed_texts

    texts_to_embed: list[str] = []
    embedded_chunk_stubs: list[EmbeddedChunk] = []
    skipped: list[SkippedChunk] = []

    # Parent sections are never embedded (see the chunk_level=="parent"
    # skip below), but their TEXT is still needed at query time for
    # context expansion. Resolve it here, once, from the same record set,
    # and carry it on each child - this is what lets retrieval read
    # parent context straight from Chroma instead of chunks.json.
    parent_text_by_id: dict[str, str] = {
        record["metadata"]["chunk_id"]: (record.get("chunk_text") or "")
        for record in chunk_records
        if record.get("metadata", {}).get("chunk_level") == "parent"
        and record.get("metadata", {}).get("chunk_id")
    }

    for record in chunk_records:
        text = (record.get("chunk_text") or "").strip()
        metadata = record.get("metadata", {})
        chunk_id = metadata.get("chunk_id", "<unknown>")

        if metadata.get("chunk_level") == "parent":
            skipped.append(SkippedChunk(
                chunk_id=chunk_id,
                reason="stored parent context; child chunks are embedded for retrieval",
            ))
            continue

        if len(text) < config.min_chunk_chars:
            skipped.append(SkippedChunk(
                chunk_id=chunk_id,
                reason=f"chunk_text below min_chunk_chars ({len(text)} < {config.min_chunk_chars})",
            ))
            continue

        effective_len = _effective_body_length(text)
        if effective_len < config.heading_only_char_threshold:
            skipped.append(SkippedChunk(
                chunk_id=chunk_id,
                reason=(
                    f"heading-only/page-artifact chunk (effective body "
                    f"length {effective_len} < {config.heading_only_char_threshold}, "
                    f"raw length {len(text)})"
                ),
            ))
            continue

        texts_to_embed.append(text)
        embedded_chunk_stubs.append(EmbeddedChunk(
            chunk_id=chunk_id,
            document_id=metadata.get("document_id", ""),
            source_file=metadata.get("source_file", ""),
            page_numbers=metadata.get("page_numbers", []),
            section_title=metadata.get("section_title"),
            chunk_text=text,
            embedding_index=-1,  # filled in below once final count is known
            parent_chunk_text=parent_text_by_id.get(metadata.get("parent_chunk_id")),
            metadata=dict(metadata),
        ))

    if not texts_to_embed:
        logger.warning("No embeddable chunks found (%d skipped) - returning empty result", len(skipped))
        return EmbeddingResult(
            embeddings=np.zeros((0, 0), dtype=np.float32), chunks=[], skipped=skipped,
            model_name=config.model_name, embedding_dim=0,
        )

    if use_default_embedder and checkpoint_dir is not None:
        initial_vectors = _load_embedding_progress(checkpoint_dir, embedded_chunk_stubs, config)
        embeddings = _embed_texts(
            texts_to_embed,
            config,
            initial_vectors=initial_vectors,
            on_batch_complete=lambda vectors: _write_embedding_progress(
                checkpoint_dir, vectors, embedded_chunk_stubs, config,
            ),
        )
    else:
        embeddings = embed_fn(texts_to_embed, config)

    if embeddings.shape[0] != len(embedded_chunk_stubs):
        raise EmbeddingError(
            f"Embedding count mismatch: got {embeddings.shape[0]} vectors for "
            f"{len(embedded_chunk_stubs)} chunks - refusing to guess an alignment."
        )

    for i, chunk in enumerate(embedded_chunk_stubs):
        chunk.embedding_index = i

    logger.info(
        "Embedded %d chunks (dim=%d, model=%s), skipped %d",
        len(embedded_chunk_stubs), embeddings.shape[1], config.model_name, len(skipped),
    )

    return EmbeddingResult(
        embeddings=embeddings, chunks=embedded_chunk_stubs, skipped=skipped,
        model_name=config.model_name, embedding_dim=embeddings.shape[1],
    )


def embed_document(
    chunks_json_path: str | Path,
    config: EmbeddingConfig | None = None,
    embed_fn=None,
    checkpoint_dir: str | Path | None = None,
) -> EmbeddingResult:
    """Convenience wrapper: load a document's chunks.json and embed it in one call."""
    records = load_chunks_json(chunks_json_path)
    return embed_chunks(records, config, embed_fn, checkpoint_dir)


# --------------------------------------------------------------------------
# Output writing
# --------------------------------------------------------------------------

def write_embeddings(result: EmbeddingResult, output_dir: str | Path) -> tuple[Path, Path]:
    """
    Write an EmbeddingResult to disk, into the SAME per-document output
    folder ingestion already uses (alongside chunks.json, metadata.json,
    etc) - consistent with the existing project convention of one
    self-contained folder per document.

    Writes:
    - embeddings.npy: the raw (N, dim) float32 vector array - compact,
      fast to load, standard format for feeding into ChromaDB.
    - embeddings_metadata.json: chunk_id/document_id/page_numbers/
      section_title/embedding_index/parent_chunk_text/metadata for every
      embedded chunk, PLUS the skipped list with reasons -
      human-inspectable, and this is what Stage 3 (ChromaDB indexing)
      reads to populate each Chroma record's stored metadata, which is
      in turn the ONLY thing retrieval reads at query time (this file
      itself is ingestion audit output, not re-read by retrieval).

    Returns (embeddings_npy_path, embeddings_metadata_json_path).
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    npy_path = output_dir / "embeddings.npy"
    np.save(npy_path, result.embeddings)

    metadata = {
        "model_name": result.model_name,
        "embedding_dim": result.embedding_dim,
        "total_embedded": len(result.chunks),
        "total_skipped": len(result.skipped),
        "chunks": [
            {
                "chunk_id": c.chunk_id, "document_id": c.document_id,
                "source_file": c.source_file, "page_numbers": c.page_numbers,
                "section_title": c.section_title, "embedding_index": c.embedding_index,
                "chunk_text": c.chunk_text,
                "parent_chunk_text": c.parent_chunk_text,
                "metadata": c.metadata,
            }
            for c in result.chunks
        ],
        "skipped": [{"chunk_id": s.chunk_id, "reason": s.reason} for s in result.skipped],
    }
    metadata_path = output_dir / "embeddings_metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2, default=str))
    _clear_embedding_progress(output_dir)

    logger.info("Wrote embeddings for %d chunks to %s", len(result.chunks), output_dir)
    return npy_path, metadata_path
