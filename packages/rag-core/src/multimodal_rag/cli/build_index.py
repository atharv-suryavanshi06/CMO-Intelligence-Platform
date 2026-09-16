#!/usr/bin/env python3
"""
Runs Stage 2 (embedding) over every document in
runtime-data/artifacts/ingestion/
that doesn't have embeddings yet, then builds/rebuilds the Stage 3 ChromaDB
index. Run this after the ingestion CLI has ingested your PDFs, and again whenever
new documents are added.

Usage:
    python -m multimodal_rag.cli.build_index
    python -m multimodal_rag.cli.build_index --output-dir runtime-data/artifacts/ingestion --index-dir runtime-data/artifacts/index
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from multimodal_rag.rag.embedding.embedder import EmbeddingConfig, EmbeddingModelUnavailableError, embed_document, write_embeddings
from multimodal_rag.rag.indexing.chroma_index import EmptyIndexError, build_index_from_output_dir, save_index
from multimodal_rag.paths import INDEX_DIR, INGESTION_ARTIFACTS_DIR, LEGACY_INDEX_DIR, LEGACY_OUTPUT_DIR, prefer_new_path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("build_index")


def main() -> int:
    parser = argparse.ArgumentParser(description="Embed ingested documents and build the ChromaDB index.")
    default_output_dir = prefer_new_path(INGESTION_ARTIFACTS_DIR, LEGACY_OUTPUT_DIR)
    default_index_dir = prefer_new_path(INDEX_DIR, LEGACY_INDEX_DIR)
    parser.add_argument("--output-dir", default=str(default_output_dir), help=f"Ingestion output directory (default: {default_output_dir})")
    parser.add_argument("--index-dir", default=str(default_index_dir), help=f"Where to write the ChromaDB index (default: {default_index_dir})")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    if not output_dir.is_dir():
        print(
            f"Error: '{output_dir}' does not exist. Run "
            "python -m multimodal_rag.cli.ingest first."
        )
        return 1

    embedding_config = EmbeddingConfig()
    embedded, skipped_docs = 0, 0
    for doc_dir in sorted(output_dir.iterdir()):
        chunks_json = doc_dir / "chunks.json"
        if not chunks_json.exists():
            continue
        if (doc_dir / "embeddings.npy").exists():
            logger.info("Skipping '%s': embeddings already exist", doc_dir.name)
            continue
        try:
            result = embed_document(
                chunks_json,
                embedding_config,
                checkpoint_dir=doc_dir,
            )
            write_embeddings(result, doc_dir)
            embedded += 1
        except EmbeddingModelUnavailableError as e:
            print(f"Error: embedding model unavailable: {e}")
            return 1
        except Exception as e:
            logger.error("Failed to embed '%s': %s", doc_dir.name, e)
            skipped_docs += 1

    print(f"Embedded {embedded} document(s), {skipped_docs} failed.")

    try:
        index, refs = build_index_from_output_dir(output_dir)
    except EmptyIndexError as e:
        print(f"Error: {e}")
        return 1

    index_path, id_map_path = save_index(index, refs, args.index_dir)
    print(f"Index built: {len(refs)} chunks -> {index_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
