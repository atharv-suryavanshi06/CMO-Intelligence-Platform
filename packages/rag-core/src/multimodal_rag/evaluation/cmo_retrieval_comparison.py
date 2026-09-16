"""Compare keyword-only and hybrid retrieval on the CMO ground-truth set."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import mean

from multimodal_rag.paths import INDEX_DIR, LEGACY_INDEX_DIR, prefer_new_path
from multimodal_rag.rag.indexing.chroma_index import load_index
from multimodal_rag.rag.retrieval.retriever_2 import RetrieverConfig, retrieve

DEFAULT_DATASET = Path("evaluation/datasets/cmo_intelligence_ground_truth.json")
DEFAULT_OUTPUT = Path("evaluation/results/cmo_retrieval_comparison.json")


def _ndcg(ids: list[str], relevant: set[str]) -> float:
    if not relevant:
        return 0.0
    dcg = sum((1.0 / math.log2(rank + 1)) for rank, chunk_id in enumerate(ids, 1) if chunk_id in relevant)
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(len(relevant), len(ids)) + 1))
    return dcg / ideal if ideal else 0.0


def _scores(ids: list[str], relevant: set[str]) -> dict[str, float]:
    hits = len(set(ids) & relevant)
    first_rank = next((rank for rank, chunk_id in enumerate(ids, 1) if chunk_id in relevant), None)
    return {
        "context_precision": hits / len(ids) if ids else 0.0,
        "context_recall": hits / len(relevant) if relevant else 0.0,
        "mrr": 1.0 / first_rank if first_rank else 0.0,
        "ndcg": _ndcg(ids, relevant),
    }


def run(dataset_path: Path, output_path: Path, top_k: int = 8) -> dict:
    entries = json.loads(dataset_path.read_text(encoding="utf-8"))
    index, id_map = load_index(str(prefer_new_path(INDEX_DIR, LEGACY_INDEX_DIR)))
    configs = {
        "keyword": RetrieverConfig(top_k=top_k, enable_keyword_only=True),
        # Candidate pools are widened so fusion can recover evidence that is
        # strong in one signal but only moderately ranked in the other.
        "hybrid": RetrieverConfig(top_k=top_k, enable_hybrid=True, dense_candidate_k=40, sparse_candidate_k=40),
    }
    per_question: list[dict] = []
    aggregates: dict[str, list[dict[str, float]]] = {name: [] for name in configs}

    for entry in entries:
        relevant = set(entry.get("expected_chunk_ids", []))
        row = {"id": entry["id"], "question": entry["question"], "expected_chunk_ids": sorted(relevant)}
        for name, config in configs.items():
            ids = [chunk.chunk_id for chunk in retrieve(entry["question"], index, id_map, retriever_config=config)]
            metrics = _scores(ids, relevant)
            row[name] = {"retrieved_chunk_ids": ids, **metrics}
            aggregates[name].append(metrics)
        per_question.append(row)

    summary = {
        name: {metric: round(mean(item[metric] for item in values), 4) for metric in values[0]}
        for name, values in aggregates.items()
    }
    payload = {"questions": len(entries), "top_k": top_k, "summary": summary, "per_question": per_question}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare keyword-only and hybrid CMO retrieval.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--top-k", type=int, default=8)
    args = parser.parse_args()
    result = run(args.dataset, args.output, args.top_k)
    print(json.dumps({"questions": result["questions"], "top_k": result["top_k"], "summary": result["summary"]}, indent=2))


if __name__ == "__main__":
    main()
