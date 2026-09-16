#!/usr/bin/env python3
"""
Ask a question against the ingested, indexed document corpus.

Usage:
    python -m multimodal_rag.cli.ask "What are the main risk factors?"
    python -m multimodal_rag.cli.ask "..." --top-k 8 --index-dir runtime-data/artifacts/index
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from multimodal_rag.rag.generation.answer_generator import (
    AnswerGenerationError,
    AnswerGenerationUnavailableError,
    GenerationConfig,
    generate_answer,
)
from multimodal_rag.rag.generation.citation import resolve_citations
from multimodal_rag.rag.generation.prompt_builder import build_prompt
from multimodal_rag.rag.indexing.chroma_index import IndexNotFoundError, load_index
from multimodal_rag.rag.retrieval.retriever_2 import RetrieverConfig, retrieve
from multimodal_rag.paths import INDEX_DIR, LEGACY_INDEX_DIR, prefer_new_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Ask a question against the ingested document corpus.")
    parser.add_argument("query", help="The question to ask.")
    default_index_dir = prefer_new_path(INDEX_DIR, LEGACY_INDEX_DIR)
    parser.add_argument("--index-dir", default=str(default_index_dir), help=f"Directory containing the ChromaDB index (default: {default_index_dir})")
    parser.add_argument("--top-k", type=int, default=8, help="Number of chunks to retrieve (default: 8)")
    args = parser.parse_args()

    try:
        index, id_map = load_index(args.index_dir)
    except IndexNotFoundError as e:
        print(f"Error: {e}\nRun python -m multimodal_rag.cli.build_index first.")
        return 1

    chunks = retrieve(args.query, index, id_map, retriever_config=RetrieverConfig(top_k=args.top_k))
    if not chunks:
        print("No relevant content found for that query.")
        return 0

    print("\n================ RETRIEVED CHUNKS ================\n")

    for i, chunk in enumerate(chunks, 1):
        print(f"Chunk {i}")
        print("Score:", chunk.score)
        print("Page:", chunk.page_numbers)
        print(chunk.chunk_text[:800])
        print("-" * 80)

    built = build_prompt(args.query, chunks)

    try:
        raw_answer = generate_answer(built.prompt_text, GenerationConfig())
    except AnswerGenerationUnavailableError as e:
        print(f"Error: {e}\nSet GEMINI_API_KEY to enable answer generation.")
        return 1
    except AnswerGenerationError as e:
        print(f"Error: {e}")
        return 1

    result = resolve_citations(raw_answer, built.source_map)

    print(f"\n{result.answer_text}\n")
    if result.citations:
        print("Sources:")
        for c in result.citations:
            pages = ", ".join(str(p) for p in c.page_numbers) or "unknown"
            print(f"  [{c.marker}] {c.source_file}, page(s): {pages}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
