"""ChromaDB vector indexing for the document retrieval pipeline."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import chromadb
import numpy as np

logger = logging.getLogger(__name__)


class IndexError_(Exception):
    """Base exception for indexing failures."""


class IndexNotFoundError(IndexError_):
    """Raised when a persisted Chroma collection does not exist."""


class EmptyIndexError(IndexError_):
    """Raised when attempting to build an index from zero embeddings."""


@dataclass
class IndexConfig:
    collection_name: str = "document_chunks"
    manifest_filename: str = "chroma_manifest.json"


@dataclass
class IndexedChunkRef:
    index_id: str
    chunk_id: str
    document_id: str
    source_file: str
    page_numbers: list[int]
    section_title: str | None
    chunk_text: str
    parent_chunk_text: str | None = None
    # Stored parent-section text for query-time context expansion, read
    # straight from this Chroma record - retrieval never reads chunks.json.
    metadata: dict[str, Any] = field(default_factory=dict)
    # Full ChunkMetadata dict, round-tripped through Chroma metadata as a
    # JSON string (see save_index/load_index) for trace/debug display.


@dataclass
class ChromaIndex:
    """A queryable Chroma collection or an in-memory build result."""

    collection: object | None = None
    vectors: np.ndarray | None = None
    refs: list[IndexedChunkRef] | None = None

    @property
    def ntotal(self) -> int:
        if self.collection is not None:
            return int(self.collection.count())
        return len(self.refs or [])


def _discover_embedding_files(output_dir: str | Path) -> list[tuple[Path, Path]]:
    output_dir = Path(output_dir)
    if not output_dir.is_dir():
        raise EmptyIndexError(f"'{output_dir}' does not exist or is not a directory - nothing to index.")
    pairs: list[tuple[Path, Path]] = []
    for doc_dir in sorted(output_dir.iterdir()):
        if not doc_dir.is_dir():
            continue
        npy_path = doc_dir / "embeddings.npy"
        meta_path = doc_dir / "embeddings_metadata.json"
        if npy_path.exists() and meta_path.exists():
            pairs.append((npy_path, meta_path))
        elif npy_path.exists() or meta_path.exists():
            logger.warning("Skipping '%s': incomplete embedding artifact", doc_dir)
    return pairs


def build_index_from_output_dir(
    output_dir: str | Path, config: IndexConfig | None = None,
) -> tuple[ChromaIndex, list[IndexedChunkRef]]:
    """Read complete embedding artifacts and prepare a Chroma-compatible index."""
    _ = config or IndexConfig()
    all_vectors: list[np.ndarray] = []
    all_refs: list[IndexedChunkRef] = []

    for npy_path, meta_path in _discover_embedding_files(output_dir):
        vectors = np.load(npy_path)
        if vectors.ndim != 2 or vectors.shape[0] == 0 or vectors.shape[1] == 0:
            logger.warning("Skipping '%s': embedding array is empty or malformed (%s)", npy_path.parent, vectors.shape)
            continue
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        chunks = metadata.get("chunks", [])
        if vectors.shape[0] != len(chunks):
            logger.warning("Skipping '%s': vector count does not match chunk metadata", npy_path.parent)
            continue
        for chunk_meta in chunks:
            index_id = str(chunk_meta["chunk_id"])
            all_refs.append(IndexedChunkRef(
                index_id=index_id,
                chunk_id=chunk_meta["chunk_id"],
                document_id=chunk_meta["document_id"],
                source_file=chunk_meta["source_file"],
                page_numbers=chunk_meta.get("page_numbers", []),
                section_title=chunk_meta.get("section_title"),
                chunk_text=chunk_meta["chunk_text"],
                parent_chunk_text=chunk_meta.get("parent_chunk_text"),
                metadata=chunk_meta.get("metadata") or {},
            ))
        all_vectors.append(vectors)

    if not all_vectors:
        raise EmptyIndexError(f"No usable embeddings found under '{output_dir}' - nothing to index.")
    matrix = np.concatenate(all_vectors, axis=0).astype(np.float32)
    if len({ref.index_id for ref in all_refs}) != len(all_refs):
        raise IndexError_("Chunk IDs must be unique within a Chroma collection.")
    logger.info("Prepared Chroma index: %d vectors, dim=%d", len(all_refs), matrix.shape[1])
    return ChromaIndex(vectors=matrix, refs=all_refs), all_refs


def save_index(
    index: ChromaIndex, refs: list[IndexedChunkRef], index_dir: str | Path,
    config: IndexConfig | None = None,
) -> tuple[Path, Path]:
    """Persist vectors, documents, and metadata into a scoped Chroma collection."""
    config = config or IndexConfig()
    index_dir = Path(index_dir)
    index_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(index_dir))
    try:
        client.delete_collection(config.collection_name)
    except Exception:
        pass
    collection = client.get_or_create_collection(
        name=config.collection_name,
        metadata={"hnsw:space": "cosine"},
    )
    if index.vectors is None:
        raise IndexError_("Cannot persist a Chroma index without vectors.")
    collection.add(
        ids=[ref.index_id for ref in refs],
        embeddings=index.vectors.tolist(),
        documents=[ref.chunk_text for ref in refs],
        metadatas=[{
            "chunk_id": ref.chunk_id,
            "document_id": ref.document_id,
            "source_file": ref.source_file,
            "page_numbers": json.dumps(ref.page_numbers),
            "section_title": ref.section_title or "",
            "parent_chunk_text": ref.parent_chunk_text or "",
            "metadata_json": json.dumps(ref.metadata or {}, default=str),
        } for ref in refs],
    )
    manifest_path = index_dir / config.manifest_filename
    manifest_path.write_text(json.dumps({"collection_name": config.collection_name, "count": len(refs)}), encoding="utf-8")
    logger.info("Saved Chroma collection (%d vectors) to %s", len(refs), index_dir)
    return index_dir, manifest_path


def load_index(
    index_dir: str | Path, config: IndexConfig | None = None,
) -> tuple[ChromaIndex, dict[str, IndexedChunkRef]]:
    config = config or IndexConfig()
    index_dir = Path(index_dir)
    manifest_path = index_dir / config.manifest_filename
    if not manifest_path.exists():
        raise IndexNotFoundError(f"No Chroma index found at '{index_dir}'")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        client = chromadb.PersistentClient(path=str(index_dir))
        collection = client.get_collection(manifest.get("collection_name", config.collection_name))
        data = collection.get(include=["documents", "metadatas"])
    except Exception as exc:
        raise IndexNotFoundError(f"Could not load Chroma index at '{index_dir}'") from exc
    refs: dict[str, IndexedChunkRef] = {}
    for index_id, document, metadata in zip(data["ids"], data.get("documents", []), data.get("metadatas", [])):
        try:
            chunk_metadata = json.loads(metadata.get("metadata_json") or "{}")
        except json.JSONDecodeError:
            chunk_metadata = {}
        refs[str(index_id)] = IndexedChunkRef(
            index_id=str(index_id),
            chunk_id=str(metadata["chunk_id"]),
            document_id=str(metadata["document_id"]),
            source_file=str(metadata["source_file"]),
            page_numbers=json.loads(metadata.get("page_numbers", "[]")),
            section_title=metadata.get("section_title") or None,
            chunk_text=document or "",
            parent_chunk_text=metadata.get("parent_chunk_text") or None,
            metadata=chunk_metadata,
        )
    return ChromaIndex(collection=collection, refs=list(refs.values())), refs


def search(
    index: ChromaIndex, id_map: dict[str, IndexedChunkRef], query_vector: np.ndarray, top_k: int = 5,
) -> list[tuple[IndexedChunkRef, float]]:
    """Return cosine-similarity results from Chroma, best match first."""
    if top_k <= 0 or index.ntotal == 0:
        return []
    if index.collection is not None:
        result = index.collection.query(query_embeddings=np.asarray(query_vector, dtype=np.float32).reshape(1, -1).tolist(), n_results=min(top_k, index.ntotal))
        ids = result.get("ids", [[]])[0]
        distances = result.get("distances", [[]])[0]
        return [(id_map[str(item_id)], 1.0 - float(distance)) for item_id, distance in zip(ids, distances) if str(item_id) in id_map]
    matrix = np.asarray(index.vectors, dtype=np.float32)
    query = np.asarray(query_vector, dtype=np.float32).reshape(-1)
    scores = matrix @ query
    order = np.argsort(-scores)[:top_k]
    return [(index.refs[int(i)], float(scores[int(i)])) for i in order]
