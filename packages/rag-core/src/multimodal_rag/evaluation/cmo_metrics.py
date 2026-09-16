"""Source-linked evaluation for the CMO intelligence corpus.

This is an offline CLI, not part of the API request path.  It deliberately
reuses ``ask_rag_timed`` from the existing evaluation runner, so each question
uses the production retriever, prompt builder, Gemini answer generator, and
citation handling.  Retrieval labels come from ``expected_chunk_ids`` in the
ground-truth JSON; Gemini is used only to judge answer quality.

Run:
    python -m multimodal_rag.evaluation.cmo_metrics
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from statistics import mean
from typing import Any

from dotenv import load_dotenv

# Support both `python -m ...` from the repository root and direct execution
# of this file from any working directory. The canonical path helper searches
# upward for the platform pyproject regardless of the component source root.
REPO_ROOT = next(
    parent for parent in Path(__file__).resolve().parents
    if (parent / "pyproject.toml").is_file()
)

from multimodal_rag.evaluation.runner import ask_rag_timed, load_ground_truth

logger = logging.getLogger(__name__)

DEFAULT_DATASET = REPO_ROOT / "evaluation/datasets/cmo_intelligence_ground_truth.json"
DEFAULT_REPORT_PATH = REPO_ROOT / "evaluation/evaluation_result.md"
RETRIEVER_EVAL_TOP_K = 5
JUDGE_MODEL = os.getenv("EVALUATION_JUDGE_MODEL", "gemini-3.1-flash-lite")


def _bounded_score(value: Any) -> float | None:
    """Return a valid 0-1 score, or None for an unavailable judge result."""
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return None


def retrieval_scores(retrieved_chunk_ids: list[str], expected_chunk_ids: list[str]) -> tuple[float, float]:
    """Compute exact-label chunk precision and recall for one question."""
    retrieved = set(retrieved_chunk_ids)
    expected = set(expected_chunk_ids)
    hits = len(retrieved & expected)
    precision = hits / len(retrieved_chunk_ids) if retrieved_chunk_ids else 0.0
    recall = hits / len(expected) if expected else 0.0
    return precision, recall


def chunk_accuracy(retrieved_chunk_ids: list[str], expected_chunk_ids: list[str]) -> dict[str, float]:
    """Return exact expected-chunk precision, recall, and F1."""
    precision, recall = retrieval_scores(retrieved_chunk_ids, expected_chunk_ids)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def retriever_accuracy(retrieved_chunk_ids: list[str], expected_chunk_ids: list[str]) -> dict[str, float | int | None]:
    """Return question-level hit, first relevant rank, and reciprocal rank."""
    expected = set(expected_chunk_ids)
    rank = next(
        (position for position, chunk_id in enumerate(retrieved_chunk_ids, start=1) if chunk_id in expected),
        None,
    )
    return {
        "hit_at_k": float(rank is not None),
        "first_relevant_rank": rank,
        "mrr": 1.0 / rank if rank is not None else 0.0,
    }


def _judge_answer(question: str, answer: str, reference: str, contexts: list[str]) -> dict[str, Any]:
    """Score three answer-quality metrics in one deterministic Gemini call."""
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise EnvironmentError("Set GEMINI_API_KEY (or GOOGLE_API_KEY) before running answer-quality evaluation.")

    prompt = f"""You are a strict evaluator of a retrieval-augmented answer. Score each metric from 0.0 to 1.0.
Return JSON only with keys faithfulness, answer_relevancy, answer_correctness, and rationale.

Definitions:
- faithfulness: every material claim in ANSWER is supported by CONTEXTS. Do not use outside knowledge.
- answer_relevancy: ANSWER directly and completely addresses QUESTION without irrelevant content.
- answer_correctness: ANSWER agrees with REFERENCE on the important facts, values, and recommendations.

QUESTION:
{question}

ANSWER:
{answer}

REFERENCE:
{reference}

CONTEXTS:
{chr(10).join(f'[{i + 1}] {context}' for i, context in enumerate(contexts))}
"""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=JUDGE_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
        ),
    )
    raw = getattr(response, "text", "") or ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Judge returned invalid JSON: {raw[:240]!r}") from exc
    return {
        "faithfulness": _bounded_score(parsed.get("faithfulness")),
        "answer_relevancy": _bounded_score(parsed.get("answer_relevancy")),
        "answer_correctness": _bounded_score(parsed.get("answer_correctness")),
        "rationale": str(parsed.get("rationale", "")),
    }


def _is_rate_limited(error: Exception) -> bool:
    return bool(re.search(
        r"(?:429|resource[_ ]exhausted|rate limit|quota(?: exceeded| exhausted)?|daily limit)",
        str(error),
        re.IGNORECASE,
    ))


def _reload_gemini_client_after_key_change(previous_key: str | None) -> bool:
    """Reload .env and reset the cached embedding client after key rotation."""
    load_dotenv(dotenv_path=REPO_ROOT / ".env", override=True)
    current_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not current_key or current_key == previous_key:
        return False

    import multimodal_rag.rag.embedding.embedder as embedder

    embedder._client = None
    embedder._client_key = None
    return True


def _wait_for_api_key_change(error: Exception) -> None:
    """Pause on provider quota exhaustion until the user rotates the key."""
    previous_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    while True:
        print(
            "\nGemini API quota/rate limit reached while evaluating a question.\n"
            "Change GEMINI_API_KEY or GOOGLE_API_KEY in the project .env file, "
            "then press Enter to retry the same question.\n"
            f"Provider message: {error}\n"
        )
        input("Press Enter after changing the API key...")
        if _reload_gemini_client_after_key_change(previous_key):
            print("New Gemini API key loaded. Retrying the same question...\n")
            return
        print("A different non-empty API key was not detected in .env. Please change it and try again.")


def judge_with_retry(*args: Any, attempts: int = 5) -> dict[str, Any]:
    for attempt in range(attempts):
        try:
            return _judge_answer(*args)
        except Exception as exc:
            if not _is_rate_limited(exc) or attempt == attempts - 1:
                raise
            delay = min(60, 2 ** (attempt + 1))
            logger.warning("Gemini judge rate-limited; retrying in %ss", delay)
            time.sleep(delay)
    raise RuntimeError("unreachable")


def _load_completed_rows(report_path: Path) -> dict[Any, dict[str, Any]]:
    """Read completed per-question rows from the Markdown checkpoint."""
    if not report_path.exists():
        return {}

    def split_row(line: str) -> list[str]:
        cells: list[str] = []
        current: list[str] = []
        escaped = False
        for character in line.strip()[1:-1]:
            if character == "|" and not escaped:
                cells.append("".join(current).strip())
                current = []
                continue
            current.append(character)
            escaped = character == "\\" and not escaped
            if character != "\\":
                escaped = False
        cells.append("".join(current).strip())
        return cells

    completed: dict[Any, dict[str, Any]] = {}
    for line in report_path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or line.startswith("| ---"):
            continue
        cells = split_row(line)
        if len(cells) != 12 or cells[0] == "ID" or cells[11].lower() != "completed":
            continue
        try:
            question_id: Any = int(cells[0])
        except ValueError:
            question_id = cells[0]

        def parse_score(value: str) -> float | None:
            return None if value == "N/A" else float(value)

        completed[question_id] = {
            "id": question_id,
            "question": cells[1].replace("\\|", "|"),
            "retriever_hit_at_k": parse_score(cells[2]),
            "first_relevant_rank": int(cells[3]) if cells[3] != "N/A" else None,
            "chunk_precision": parse_score(cells[4]),
            "chunk_recall": parse_score(cells[5]),
            "chunk_f1": parse_score(cells[6]),
            "faithfulness": parse_score(cells[7]),
            "answer_relevancy": parse_score(cells[8]),
            "answer_correctness": parse_score(cells[9]),
            "total_latency_ms": parse_score(cells[10]),
            "status": "completed",
        }
    return completed


def _write_report(rows: list[dict[str, Any]], report_path: Path) -> dict[str, float | int | None]:
    metric_names = ["chunk_precision", "chunk_recall", "chunk_f1", "faithfulness", "answer_relevancy", "answer_correctness"]
    summary: dict[str, float | int | None] = {"questions": len(rows)}
    for metric in metric_names:
        values = [row[metric] for row in rows if row.get(metric) is not None]
        summary[metric] = round(mean(values), 4) if values else None
    summary["answer_quality_coverage"] = sum(row.get("faithfulness") is not None for row in rows)
    latencies = [row["total_latency_ms"] for row in rows if row.get("total_latency_ms") is not None]
    summary["mean_total_latency_ms"] = round(mean(latencies), 2) if latencies else None
    lines = [
        "# RAG Evaluation Results",
        "",
        f"Ground truth: `{DEFAULT_DATASET.as_posix()}`",
        "",
        f"Questions evaluated: **{summary['questions']}**",
        "",
        "## Retrieval configuration",
        "",
        "The evaluator measures the production **hybrid retriever** only: semantic embedding retrieval fused with keyword/BM25 retrieval and the configured reranking weights. Separate keyword-only and semantic-only scores are not reported.",
        "",
        f"Top-K: **{RETRIEVER_EVAL_TOP_K}**",
        "",
        "## Retriever accuracy",
        "",
        "Retriever accuracy is question-level: a question is successful when at least one expected chunk appears in the top-K results.",
        "",
        "| Metric | Score |",
        "| --- | ---: |",
        f"| Hit@{RETRIEVER_EVAL_TOP_K} (retriever accuracy) | {mean([row.get('retriever_hit_at_k', 0.0) for row in rows]):.4f} |" if rows else f"| Hit@{RETRIEVER_EVAL_TOP_K} (retriever accuracy) | N/A |",
        f"| MRR | {mean([row.get('mrr', 0.0) for row in rows]):.4f} |" if rows else "| MRR | N/A |",
        f"| Average first relevant rank | {mean([row['first_relevant_rank'] for row in rows if row.get('first_relevant_rank') is not None]):.2f} |" if any(row.get('first_relevant_rank') is not None for row in rows) else "| Average first relevant rank | N/A |",
        "",
        "## Chunk accuracy",
        "",
        "Chunk accuracy compares retrieved chunk IDs with the manually verified expected chunk IDs.",
        "",
        "| Metric | Score |",
        "| --- | ---: |",
    ]
    for metric in metric_names:
        if metric not in {"chunk_precision", "chunk_recall", "chunk_f1"}:
            continue
        score = summary[metric]
        label = metric.replace("chunk_", "").upper()
        if label in {"PRECISION", "RECALL", "F1"}:
            label += f"@{RETRIEVER_EVAL_TOP_K}"
        lines.append(f"| {label} | {'N/A' if score is None else f'{score:.4f}'} |")
    lines.extend([
        "",
        "## Answer quality",
        "",
        "| Metric | Score |",
        "| --- | ---: |",
    ])
    for metric in ("faithfulness", "answer_relevancy", "answer_correctness"):
        score = summary[metric]
        lines.append(f"| {metric.replace('_', ' ').title()} | {'N/A' if score is None else f'{score:.4f}'} |")
    lines.extend([
        "",
        f"Answer-quality judge coverage: {summary['answer_quality_coverage']}/{summary['questions']}.",
        "Faithfulness, answer relevancy, and answer correctness are Gemini judge scores from 0 to 1.",
        "",
        "## Per-question results",
        "",
        f"| ID | Question | Hit@{RETRIEVER_EVAL_TOP_K} | First relevant rank | Chunk precision@{RETRIEVER_EVAL_TOP_K} | Chunk recall@{RETRIEVER_EVAL_TOP_K} | Chunk F1@{RETRIEVER_EVAL_TOP_K} | Faithfulness | Answer relevancy | Answer correctness | Total latency (ms) | Status |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ])
    for row in rows:
        def display(name: str) -> str:
            value = row.get(name)
            return "N/A" if value is None else f"{value:.4f}" if isinstance(value, float) else str(value)

        question = str(row["question"]).replace("|", "\\|")
        lines.append(
            f"| {row['id']} | {question} | {display('retriever_hit_at_k')} | {display('first_relevant_rank')} | "
            f"{display('chunk_precision')} | {display('chunk_recall')} | {display('chunk_f1')} | "
            f"{display('faithfulness')} | {display('answer_relevancy')} | {display('answer_correctness')} | "
            f"{display('total_latency_ms')} | {row.get('status', 'completed')} |"
        )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def run(
    dataset: Path,
    report_path: Path,
    limit: int | None = None,
) -> dict[str, float | int | None]:
    """Evaluate every JSON question and replace the single Markdown report."""
    entries = load_ground_truth(dataset)
    if limit:
        entries = entries[:limit]
    rows_by_id = _load_completed_rows(report_path)
    if rows_by_id:
        logger.info("Resuming: skipping %d completed question(s) from %s", len(rows_by_id), report_path)

    def ordered_rows() -> list[dict[str, Any]]:
        return [rows_by_id[entry["id"]] for entry in entries if entry["id"] in rows_by_id]

    for number, entry in enumerate(entries, start=1):
        if entry["id"] in rows_by_id:
            continue
        logger.info("Evaluating question %d/%d (id=%s)", number, len(entries), entry["id"])
        while True:
            try:
                result = ask_rag_timed(entry["question"], top_k=RETRIEVER_EVAL_TOP_K)
                break
            except Exception as exc:
                if _is_rate_limited(exc):
                    _wait_for_api_key_change(exc)
                    continue
                logger.exception("Production RAG failed for question id=%s: %s", entry["id"], exc)
                result = None
                break

        if result is None:
            # Keep a visible failed row in the Markdown report.
            rows_by_id[entry["id"]] = {
                "id": entry["id"], "question": entry["question"], "answer": "",
                "expected_chunk_ids": entry.get("expected_chunk_ids", []), "retrieved_chunk_ids": [],
                "retriever_hit_at_k": 0.0, "first_relevant_rank": None, "mrr": 0.0,
                "chunk_precision": 0.0, "chunk_recall": 0.0, "chunk_f1": 0.0,
                "faithfulness": None, "answer_relevancy": None, "answer_correctness": None,
                "rationale": "Production RAG failed; see the evaluation log for details.",
                "retrieval_latency_ms": None, "generation_latency_ms": None, "total_latency_ms": None,
                "status": "failed",
            }
            _write_report(ordered_rows(), report_path)
            continue
        # The timed production pipeline returns the exact IDs of the chunks it
        # retrieved, before citation rendering removes internal chunk tags.
        retrieved_ids = result.retrieved_chunk_ids
        expected_ids = entry.get("expected_chunk_ids", [])
        retriever_metrics = retriever_accuracy(retrieved_ids, expected_ids)
        chunk_metrics = chunk_accuracy(retrieved_ids, expected_ids)
        row: dict[str, Any] = {
            "id": entry["id"], "question": entry["question"], "answer": result.answer,
            "expected_chunk_ids": expected_ids, "retrieved_chunk_ids": retrieved_ids,
            "retriever_hit_at_k": retriever_metrics["hit_at_k"],
            "first_relevant_rank": retriever_metrics["first_relevant_rank"],
            "mrr": retriever_metrics["mrr"],
            "chunk_precision": chunk_metrics["precision"],
            "chunk_recall": chunk_metrics["recall"],
            "chunk_f1": chunk_metrics["f1"],
            "retrieval_latency_ms": result.retrieval_latency_ms,
            "generation_latency_ms": result.generation_latency_ms,
            "total_latency_ms": result.total_latency_ms,
            "status": "completed",
        }
        try:
            row.update(judge_with_retry(entry["question"], result.answer, entry["ground_truth"], result.retrieved_context))
        except Exception as exc:
            logger.exception("Answer judge failed for question id=%s: %s", entry["id"], exc)
            row.update({"faithfulness": None, "answer_relevancy": None, "answer_correctness": None, "rationale": str(exc)})
        rows_by_id[entry["id"]] = row
        _write_report(ordered_rows(), report_path)
    ordered_rows = [rows_by_id[entry["id"]] for entry in entries if entry["id"] in rows_by_id]
    return _write_report(ordered_rows, report_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the CMO RAG corpus with source labels and Gemini judging.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    load_dotenv()
    summary = run(args.dataset, args.report, args.limit)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
