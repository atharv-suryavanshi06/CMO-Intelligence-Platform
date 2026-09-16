from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from dataclasses import dataclass
from pathlib import Path

from multimodal_rag.paths import INDEX_DIR, LEGACY_INDEX_DIR, prefer_new_path
from multimodal_rag.rag.indexing.chroma_index import load_index
from multimodal_rag.rag.retrieval.retriever_2 import RetrieverConfig, retrieve

DEFAULT_LABELS_PATH = Path("evaluation/datasets/retrieval_relevance.json")
DEFAULT_GROUND_TRUTH_PATH = Path("evaluation/datasets/ground_truth.json")
MODE_NAMES = ("Baseline", "Hybrid", "Hybrid+CE")
K_VALUES = (3, 5, 8)


@dataclass(frozen=True)
class Label:
    id: int
    question: str
    relevant_chunk_ids: tuple[str, ...]
    label_status: str
    notes: str


def load_labels(path: Path) -> list[Label]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [
        Label(
            id=item["id"],
            question=item["question"],
            relevant_chunk_ids=tuple(item.get("relevant_chunk_ids", [])),
            label_status=item["label_status"],
            notes=item["notes"],
        )
        for item in raw
    ]


def load_ground_truth(path: Path) -> dict[int, dict]:
    return {item["id"]: item for item in json.loads(path.read_text(encoding="utf-8"))}


def build_config(mode: str, top_k: int) -> RetrieverConfig:
    if mode == "Baseline":
        return RetrieverConfig(top_k=top_k, enable_hybrid=False, enable_cross_encoder=False)
    if mode == "Hybrid":
        return RetrieverConfig(top_k=top_k, enable_hybrid=True)
    return RetrieverConfig(top_k=top_k, enable_hybrid=True, enable_cross_encoder=True)


def dcg(relevances: list[int]) -> float:
    return sum(rel / math.log2(rank + 1) for rank, rel in enumerate(relevances, 1))


def ndcg(retrieved_ids: list[str], relevant_ids: set[str], k: int) -> float:
    rels = [1 if chunk_id in relevant_ids else 0 for chunk_id in retrieved_ids[:k]]
    ideal = [1] * min(len(relevant_ids), k)
    ideal_dcg = dcg(ideal)
    if ideal_dcg == 0:
        return 0.0
    return dcg(rels) / ideal_dcg


def first_relevant_rank(retrieved_ids: list[str], relevant_ids: set[str]) -> int | None:
    for rank, chunk_id in enumerate(retrieved_ids, 1):
        if chunk_id in relevant_ids:
            return rank
    return None


def benchmark(labels: list[Label], index, id_map: dict[int, object]) -> tuple[dict[str, dict[str, float]], list[dict]]:
    aggregate: dict[str, list[dict[str, float]]] = {mode: [] for mode in MODE_NAMES}
    latency_samples: dict[str, list[float]] = {mode: [] for mode in MODE_NAMES}
    rows: list[dict] = []
    verified = [label for label in labels if label.label_status == "verified"]

    for label in labels:
        relevant_ids = set(label.relevant_chunk_ids)
        row = {
            "id": label.id,
            "question": label.question,
            "label_status": label.label_status,
            "relevant_chunk_ids": list(label.relevant_chunk_ids),
        }
        for mode in MODE_NAMES:
            rank_8 = None
            for k in K_VALUES:
                config = build_config(mode, k)
                started = time.perf_counter()
                results = retrieve(label.question, index, id_map, retriever_config=config)
                latency_samples[mode].append((time.perf_counter() - started) * 1000.0)
                retrieved_ids = [chunk.chunk_id for chunk in results]
                if k == 8:
                    rank_8 = first_relevant_rank(retrieved_ids, relevant_ids)
                if label.label_status == "verified":
                    aggregate[mode].append(
                        {
                            f"recall@{k}": (
                                len([chunk_id for chunk_id in retrieved_ids[:k] if chunk_id in relevant_ids])
                                / max(len(relevant_ids), 1)
                            ),
                            f"precision@{k}": (
                                len([chunk_id for chunk_id in retrieved_ids[:k] if chunk_id in relevant_ids]) / k
                            ),
                            f"ndcg@{k}": ndcg(retrieved_ids, relevant_ids, k),
                        }
                    )
            row[f"{mode.lower()}_first_relevant_rank"] = rank_8 if rank_8 is not None else "n/a"
        rows.append(row)

    summary: dict[str, dict[str, float]] = {}
    for mode in MODE_NAMES:
        summary[mode] = {}
        if not verified:
            continue
        flattened: dict[str, list[float]] = {}
        for sample in aggregate[mode]:
            for key, value in sample.items():
                flattened.setdefault(key, []).append(value)
        for key, values in flattened.items():
            summary[mode][key] = statistics.fmean(values)
        reciprocal_ranks = []
        for row in rows:
            if row["label_status"] != "verified":
                continue
            rank = row[f"{mode.lower()}_first_relevant_rank"]
            reciprocal_ranks.append(1.0 / rank if isinstance(rank, int) else 0.0)
        summary[mode]["mrr"] = statistics.fmean(reciprocal_ranks) if reciprocal_ranks else 0.0
        summary[mode]["avg_latency_ms"] = statistics.fmean(latency_samples[mode]) if latency_samples[mode] else 0.0
    return summary, rows


def _format_rank(value: int | str | None) -> str:
    return str(value) if value is not None else "n/a"


def print_report(summary: dict[str, dict[str, float]], rows: list[dict], labels: list[Label]) -> None:
    verified = sum(1 for label in labels if label.label_status == "verified")
    needs_review = sum(1 for label in labels if label.label_status == "needs_review")
    print("Metric             Baseline       Hybrid        Hybrid+CE")
    labels_map = {
        "recall@3": "Recall@3",
        "recall@5": "Recall@5",
        "recall@8": "Recall@8",
        "precision@3": "Precision@3",
        "precision@5": "Precision@5",
        "precision@8": "Precision@8",
        "mrr": "MRR",
        "ndcg@3": "nDCG@3",
        "ndcg@5": "nDCG@5",
        "ndcg@8": "nDCG@8",
        "avg_latency_ms": "Avg latency ms",
    }
    for metric in ("recall@3", "recall@5", "recall@8", "precision@3", "precision@5", "precision@8", "mrr", "ndcg@3", "ndcg@5", "ndcg@8", "avg_latency_ms"):
        label = labels_map[metric]
        print(
            f"{label:<18} "
            f"{summary['Baseline'].get(metric, 0.0):<13.3f} "
            f"{summary['Hybrid'].get(metric, 0.0):<13.3f} "
            f"{summary['Hybrid+CE'].get(metric, 0.0):<13.3f}"
        )
    print()
    print("ID  Status       Relevant chunk IDs                             Baseline  Hybrid    Hybrid+CE")
    for row in rows:
        chunk_ids = ",".join(row["relevant_chunk_ids"]) if row["relevant_chunk_ids"] else "n/a"
        print(
            f"{row['id']:>2}  {row['label_status']:<11} {chunk_ids:<44} "
            f"{_format_rank(row['baseline_first_relevant_rank']):<8} "
            f"{_format_rank(row['hybrid_first_relevant_rank']):<8} "
            f"{_format_rank(row['hybrid+ce_first_relevant_rank']):<8}"
        )
    print()
    print(f"Verified questions: {verified} / {len(labels)}")
    print(f"Needs review: {needs_review} / {len(labels)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Retrieval-only benchmark for the current 25-question dataset.")
    parser.add_argument("--labels-path", default=str(DEFAULT_LABELS_PATH))
    parser.add_argument("--ground-truth-path", default=str(DEFAULT_GROUND_TRUTH_PATH))
    args = parser.parse_args(argv)

    labels = load_labels(Path(args.labels_path))
    ground_truth = load_ground_truth(Path(args.ground_truth_path))
    missing = sorted(label.id for label in labels if label.id not in ground_truth)
    if missing:
        raise SystemExit(f"Ground truth ids missing from dataset: {missing}")

    index, id_map = load_index(str(prefer_new_path(INDEX_DIR, LEGACY_INDEX_DIR)))
    summary, rows = benchmark(labels, index, id_map)
    print_report(summary, rows, labels)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
