"""Print-only developer evaluation for one ground-truth question.

This module deliberately has no persistence path. It selects one dataset item,
runs the production RAG primitives once, evaluates that one record with the
existing RAGAS implementation, and returns a structured trace suitable for the
    terminal and other evaluation tooling.
"""

from __future__ import annotations

import argparse
import difflib
import json
import math
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence, TextIO

from multimodal_rag.paths import (
    GROUND_TRUTH_PATH,
    INGESTION_ARTIFACTS_DIR,
    LEGACY_GROUND_TRUTH_PATH,
    LEGACY_INGESTION_ARTIFACTS_DIR,
    prefer_new_path,
)
from multimodal_rag.rag.trace import (
    RAGTrace,
    RetrievedItemTrace,
    load_chunk_metadata,
    run_rag_trace,
)


CONFIGURED_TOP_K = 8
METRIC_NAMES = (
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
    "answer_correctness",
)


class SelectionError(ValueError):
    """Raised before any RAG or evaluator component is initialized."""

    def __init__(self, message: str, candidates: Sequence[dict[str, Any]] = ()) -> None:
        super().__init__(message)
        self.candidates = list(candidates)


def _load_chunk_metadata() -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Compatibility seam; metadata loading is implemented by ``rag.trace``."""
    return load_chunk_metadata(
        (INGESTION_ARTIFACTS_DIR, LEGACY_INGESTION_ARTIFACTS_DIR)
    )


@dataclass
class QuestionEvaluationTrace:
    ground_truth_id: Any
    question: str
    reference_answer: str
    rag: RAGTrace
    evaluator_provider: str | None = None
    evaluator_model: str | None = None
    metrics: dict[str, float | None] = field(
        default_factory=lambda: {name: None for name in METRIC_NAMES}
    )
    composite_score: float | None = None
    evaluator_prompt_tokens: int | None = None
    evaluator_completion_tokens: int | None = None
    evaluator_total_tokens: int | None = None
    estimated_evaluator_cost: float | None = None
    evaluation_latency_ms: float | None = None
    total_latency_ms: float | None = None
    status: str = "pending"
    errors: list[str] = field(default_factory=list)


def normalize_question(value: str) -> str:
    """Normalize only for exact matching; never rewrite the executed question."""
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.strip().split()).casefold()


def load_ground_truth(path: Path | None = None) -> list[dict[str, Any]]:
    """Load the dataset without importing the heavy batch evaluator module."""
    selected_path = path or prefer_new_path(GROUND_TRUTH_PATH, LEGACY_GROUND_TRUTH_PATH)
    if not selected_path.exists():
        raise FileNotFoundError(f"Ground truth file not found: {selected_path}")

    try:
        data = json.loads(selected_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Ground truth file is not valid JSON: {selected_path}: {exc}") from exc

    if not isinstance(data, list) or not data:
        raise ValueError(f"Ground truth file must contain a non-empty JSON list: {selected_path}")

    required = {"id", "question", "ground_truth"}
    for position, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise ValueError(f"Ground truth entry at index {position} must be an object.")
        missing = required - entry.keys()
        if missing:
            raise ValueError(
                f"Ground truth entry at index {position} is missing: {sorted(missing)}"
            )
    return data


def select_ground_truth_item(
    items: Sequence[dict[str, Any]],
    *,
    ground_truth_id: str | None = None,
    question: str | None = None,
) -> dict[str, Any]:
    """Select exactly one item by exact ID or exact normalized question."""
    if (ground_truth_id is None) == (question is None):
        raise SelectionError("Provide exactly one selector: ground-truth ID or question.")

    if ground_truth_id is not None:
        wanted_id = ground_truth_id.strip()
        matches = [item for item in items if str(item["id"]).strip() == wanted_id]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise SelectionError(f"Ground-truth ID {wanted_id!r} is not unique.", matches)
        raise SelectionError(f"No ground-truth item has ID {wanted_id!r}.", items)

    assert question is not None
    wanted_question = normalize_question(question)
    matches = [
        item for item in items if normalize_question(str(item["question"])) == wanted_question
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise SelectionError("The normalized question matches more than one item.", matches)

    normalized_to_item = {normalize_question(str(item["question"])): item for item in items}
    close = difflib.get_close_matches(wanted_question, normalized_to_item, n=5, cutoff=0.35)
    candidates = [normalized_to_item[value] for value in close]
    raise SelectionError(
        "No exact normalized ground-truth question matched. Similar entries are suggestions only.",
        candidates or list(items),
    )


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _active_evaluator_model(runner: Any) -> str:
    if runner.EVALUATOR_PROVIDER == "ollama":
        return runner.OLLAMA_MODEL_NAME
    return runner.GROQ_MODEL_NAME


def evaluate_ground_truth_item(entry: dict[str, Any]) -> QuestionEvaluationTrace:
    """Run one selected question and return a reusable structured trace.

    There are deliberately no CSV/report reads or writes in this function.
    The batch runner is imported lazily only after selection has succeeded.
    """
    rag_trace = RAGTrace(original_question=str(entry["question"]))
    trace = QuestionEvaluationTrace(
        ground_truth_id=entry["id"],
        question=str(entry["question"]),
        reference_answer=str(entry["ground_truth"]),
        rag=rag_trace,
        status="running",
    )
    total_start = time.perf_counter()

    try:
        from multimodal_rag.evaluation import runner
        from multimodal_rag.rag.embedding.embedder import EmbeddingConfig
        from multimodal_rag.rag.generation.answer_generator import GenerationConfig

        embedding_config = EmbeddingConfig()
        generation_config = GenerationConfig()
        rag_trace.embedding_model = embedding_config.model_name
        rag_trace.generation_model = generation_config.model_name
        trace.evaluator_provider = runner.EVALUATOR_PROVIDER
        trace.evaluator_model = _active_evaluator_model(runner)

        metadata_by_id, ambiguous_metadata = _load_chunk_metadata()
        rag_trace = run_rag_trace(
            trace.question,
            index=runner.INDEX,
            id_map=runner.ID_MAP,
            top_k=CONFIGURED_TOP_K,
            embedding_config=embedding_config,
            generation_config=generation_config,
            metadata_by_id=metadata_by_id,
            ambiguous_metadata=ambiguous_metadata,
            retrieve_fn=runner.retrieve,
            build_prompt_fn=runner.build_prompt,
            generate_answer_fn=runner.generate_answer_with_metadata,
            resolve_citations_fn=runner.resolve_citations,
        )
        trace.rag = rag_trace
        contexts = [
            f"Page {item.page_numbers}: {item.chunk_text}"
            for item in rag_trace.retrieved_items
        ]
        record = {
            "id": trace.ground_truth_id,
            "question": trace.question,
            "answer": rag_trace.generated_answer,
            "contexts": contexts,
            "ground_truth": trace.reference_answer,
            "retrieval_latency_ms": rag_trace.retrieval_latency_ms,
            "generation_latency_ms": rag_trace.generation_latency_ms,
            "total_latency_ms": rag_trace.rag_latency_ms,
        }

        evaluation_start = time.perf_counter()
        outcome = runner.run_ragas_evaluation([record])
        trace.evaluation_latency_ms = (time.perf_counter() - evaluation_start) * 1000

        row = outcome.scores_df.iloc[0]
        trace.metrics = {name: _finite_float(row.get(name)) for name in METRIC_NAMES}
        trace.composite_score = _finite_float(runner.compute_composite_score(row))

        if outcome.token_usage_available and outcome.total_tokens is not None:
            prompt_tokens = int(outcome.total_tokens.input_tokens)
            completion_tokens = int(outcome.total_tokens.output_tokens)
            trace.evaluator_prompt_tokens = prompt_tokens
            trace.evaluator_completion_tokens = completion_tokens
            trace.evaluator_total_tokens = prompt_tokens + completion_tokens
            trace.estimated_evaluator_cost = _finite_float(outcome.total_cost)

        trace.status = "success"
    except Exception as exc:
        trace.status = "error"
        trace.errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        trace.total_latency_ms = (time.perf_counter() - total_start) * 1000

    return trace


def _display_value(value: Any, *, precision: int = 6) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, float):
        return f"{value:.{precision}f}"
    return str(value)


def _display_latency(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:.2f} ms"


def print_trace(trace: QuestionEvaluationTrace, stream: TextIO = sys.stdout) -> None:
    """Render the complete developer trace; chunk text is never truncated."""
    def line(value: str = "") -> None:
        print(value, file=stream)

    line("GROUND TRUTH")
    line(f"ID: {trace.ground_truth_id}")
    line(f"Question: {trace.question}")
    line("Reference answer:")
    line(trace.reference_answer)
    line()
    line("GENERATED ANSWER")
    line(trace.rag.generated_answer or "unavailable")
    line()
    line("RETRIEVED CHUNKS - FINAL RANKING")

    if not trace.rag.retrieved_items:
        line("No chunks retrieved.")
    for item in trace.rag.retrieved_items:
        line()
        line(f"[{item.rank}]")
        line(
            "Raw vector similarity score: "
            f"{_display_value(item.raw_vector_score, precision=17)}"
        )
        line(
            "Combined rerank score: "
            f"{_display_value(item.combined_rerank_score, precision=17)}"
        )
        line(f"BM25 score: {_display_value(item.bm25_score, precision=17)}")
        line(f"RRF score: {_display_value(item.rrf_score, precision=17)}")
        line(
            "Cross-encoder score: "
            f"{_display_value(item.cross_encoder_score, precision=17)}"
        )
        line(f"Chunk ID: {item.chunk_id}")
        line(f"Document: {item.document_name}")
        line(f"Document ID: {item.document_id}")
        line(f"Page number(s): {item.page_numbers or 'unavailable'}")
        line(f"Section: {item.section_title or 'unavailable'}")
        line("Metadata:")
        if item.metadata is not None:
            line(json.dumps(item.metadata, indent=2, ensure_ascii=False, default=str))
        else:
            line(item.metadata_note or "unavailable")
        line("Complete chunk text:")
        line(item.chunk_text)
        line("-" * 80)

    line()
    line("PIPELINE")
    line(f"Retriever used: {trace.rag.retriever}")
    line(f"Embedding model: {_display_value(trace.rag.embedding_model)}")
    line(f"Generation model: {_display_value(trace.rag.generation_model)}")
    line(f"Evaluator provider: {_display_value(trace.evaluator_provider)}")
    line(f"Evaluator model: {_display_value(trace.evaluator_model)}")
    line(f"Configured top-k: {trace.rag.configured_top_k}")
    line(f"Actual retrieved chunks: {trace.rag.actual_retrieved_count}")

    line()
    line("TIMING")
    line(f"Retrieval latency: {_display_latency(trace.rag.retrieval_latency_ms)}")
    line(f"Generation latency: {_display_latency(trace.rag.generation_latency_ms)}")
    line(f"Complete RAG latency: {_display_latency(trace.rag.rag_latency_ms)}")
    line(
        "Evaluation latency (including evaluator initialization): "
        f"{_display_latency(trace.evaluation_latency_ms)}"
    )
    line(f"Total latency: {_display_latency(trace.total_latency_ms)}")

    line()
    line("TOKEN USAGE")
    line(f"Generation prompt tokens: {_display_value(trace.rag.generation_prompt_tokens)}")
    line(
        "Generation completion tokens: "
        f"{_display_value(trace.rag.generation_completion_tokens)}"
    )
    line(f"Generation total tokens: {_display_value(trace.rag.generation_total_tokens)}")
    line(f"Evaluator prompt tokens: {_display_value(trace.evaluator_prompt_tokens)}")
    line(f"Evaluator completion tokens: {_display_value(trace.evaluator_completion_tokens)}")
    line(f"Evaluator total tokens: {_display_value(trace.evaluator_total_tokens)}")
    line(f"Estimated evaluator cost: {_display_value(trace.estimated_evaluator_cost, precision=8)}")

    line()
    line("RAGAS METRICS")
    labels = {
        "faithfulness": "Faithfulness",
        "answer_relevancy": "Answer Relevancy",
        "context_precision": "Context Precision",
        "context_recall": "Context Recall",
        "answer_correctness": "Answer Correctness",
    }
    for name in METRIC_NAMES:
        line(f"{labels[name]}: {_display_value(trace.metrics.get(name))}")
    line(f"Composite Score: {_display_value(trace.composite_score)}")
    line()
    line(f"STATUS: {trace.status}")
    for error in trace.errors:
        line(f"ERROR: {error}")


def _print_selection_error(exc: SelectionError, stream: TextIO) -> None:
    print(f"Selection error: {exc}", file=stream)
    if exc.candidates:
        print("Candidate ground-truth items (informational only):", file=stream)
        for item in exc.candidates:
            print(f"  {item['id']}: {item['question']}", file=stream)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate exactly one ground-truth question and print a developer trace."
    )
    selectors = parser.add_mutually_exclusive_group(required=True)
    selectors.add_argument("--id", dest="ground_truth_id", help="Exact ground-truth ID.")
    selectors.add_argument(
        "--question",
        help="Exact ground-truth question after whitespace, Unicode, and case normalization.",
    )
    selectors.add_argument(
        "--interactive",
        action="store_true",
        help="List ground-truth items and prompt for one exact ID.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        items = load_ground_truth()
        selected_id = args.ground_truth_id
        if args.interactive:
            print("Available ground-truth questions:")
            for item in items:
                print(f"  {item['id']}: {item['question']}")
            selected_id = input("Ground-truth ID: ")
        selected = select_ground_truth_item(
            items,
            ground_truth_id=selected_id,
            question=args.question,
        )
    except SelectionError as exc:
        _print_selection_error(exc, sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"Ground-truth error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130

    trace = evaluate_ground_truth_item(selected)
    print_trace(trace)
    return 0 if trace.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
