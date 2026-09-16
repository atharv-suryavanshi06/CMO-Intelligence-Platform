"""Deterministic tests for Groq-only metric token allowances."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from multimodal_rag.evaluation import runner


RECORD = {
    "id": 1,
    "question": "Question",
    "answer": "Answer",
    "contexts": ["Context"],
    "ground_truth": "Reference",
}


class _CloneableChatModel:
    """Small direct-model double that mirrors ChatGroq.model_copy semantics."""

    def __init__(
        self,
        parent: "_CloneableChatModel | None" = None,
        *,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> None:
        self.parent = parent or self
        self.max_tokens = max_tokens
        self.temperature = temperature
        if parent is None:
            self.model_copy_calls: list[dict] = []

    def generate_prompt(self, *args, **kwargs):  # pragma: no cover - never called
        raise AssertionError("No live model call is allowed in this test.")

    async def agenerate_prompt(self, *args, **kwargs):  # pragma: no cover - never called
        raise AssertionError("No live model call is allowed in this test.")

    def model_copy(self, *, update: dict | None = None):
        update = dict(update or {})
        self.parent.model_copy_calls.append(update)
        return _CloneableChatModel(
            self.parent,
            max_tokens=update.get("max_tokens", self.max_tokens),
            temperature=update.get("temperature", self.temperature),
        )


class _EvaluationResult:
    def to_pandas(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "user_input": RECORD["question"],
                    "response": RECORD["answer"],
                    "faithfulness": 1.0,
                    "answer_relevancy": 1.0,
                    "context_precision": 1.0,
                    "context_recall": 1.0,
                    "answer_correctness": 1.0,
                }
            ]
        )

    def total_tokens(self):
        return None


class FaithfulnessConfigurationTests(unittest.TestCase):
    def _run_with_provider(self, provider: str):
        from ragas.llms import LangchainLLMWrapper
        from ragas.metrics.base import MetricWithLLM

        chat_model = _CloneableChatModel()
        shared_llm = LangchainLLMWrapper(chat_model)
        captured: dict[str, object] = {}

        def fake_evaluate(*, metrics, llm, **kwargs):
            changed = []
            for metric in metrics:
                if isinstance(metric, MetricWithLLM) and metric.llm is None:
                    metric.llm = llm
                    changed.append(metric)
            captured["metric_names"] = [metric.name for metric in metrics]
            captured["metric_llms"] = [getattr(metric, "llm", None) for metric in metrics]
            for metric in changed:
                metric.llm = None
            return _EvaluationResult()

        with (
            patch.object(runner, "EVALUATOR_PROVIDER", provider),
            patch("ragas.evaluate", side_effect=fake_evaluate) as evaluate,
        ):
            outcome = runner.run_ragas_evaluation(
                [RECORD],
                ragas_llm=shared_llm,
                ragas_embeddings=object(),
            )

        self.assertEqual(len(outcome.scores_df), 1)
        evaluate.assert_called_once()
        return chat_model, shared_llm, captured

    def test_groq_applies_independent_metric_specific_token_limits(self) -> None:
        from ragas.llms import LangchainLLMWrapper

        chat_model, shared_llm, captured = self._run_with_provider("groq")
        metric_names = captured["metric_names"]
        metric_llms = captured["metric_llms"]

        self.assertEqual(runner.GROQ_FAITHFULNESS_MAX_TOKENS, 4096)
        self.assertEqual(runner.GROQ_ANSWER_CORRECTNESS_MAX_TOKENS, 4096)
        self.assertEqual(
            metric_names,
            [
                "faithfulness",
                "answer_relevancy",
                "context_precision",
                "context_recall",
                "answer_correctness",
            ],
        )
        self.assertEqual(
            chat_model.model_copy_calls,
            [{"max_tokens": 4096}, {"max_tokens": 4096}],
        )

        faithfulness_llm = metric_llms[0]
        self.assertIsInstance(faithfulness_llm, LangchainLLMWrapper)
        self.assertIsNot(faithfulness_llm, shared_llm)
        self.assertIsInstance(faithfulness_llm.langchain_llm, _CloneableChatModel)
        self.assertEqual(faithfulness_llm.langchain_llm.max_tokens, 4096)
        self.assertEqual(faithfulness_llm.langchain_llm.temperature, 0.0)
        self.assertFalse(hasattr(faithfulness_llm.langchain_llm, "kwargs"))
        for metric_llm in metric_llms[1:4]:
            self.assertIs(metric_llm, shared_llm)
        answer_correctness_llm = metric_llms[4]
        self.assertIsInstance(answer_correctness_llm, LangchainLLMWrapper)
        self.assertIsNot(answer_correctness_llm, shared_llm)
        self.assertEqual(answer_correctness_llm.langchain_llm.max_tokens, 4096)
        self.assertEqual(answer_correctness_llm.langchain_llm.temperature, 0.0)
        self.assertIsNone(shared_llm.langchain_llm.max_tokens)

    def test_remaining_groq_metrics_keep_shared_provider_default(self) -> None:
        chat_model, shared_llm, captured = self._run_with_provider("groq")

        self.assertEqual(
            chat_model.model_copy_calls,
            [{"max_tokens": 4096}, {"max_tokens": 4096}],
        )
        for metric_llm in captured["metric_llms"][1:4]:
            self.assertIs(metric_llm, shared_llm)
            self.assertIsNone(metric_llm.langchain_llm.max_tokens)

    def test_ollama_keeps_one_unchanged_shared_wrapper_for_all_metrics(self) -> None:
        chat_model, shared_llm, captured = self._run_with_provider("ollama")

        self.assertEqual(chat_model.model_copy_calls, [])
        for metric_llm in captured["metric_llms"]:
            self.assertIs(metric_llm, shared_llm)
        self.assertIsNone(shared_llm.langchain_llm.max_tokens)


class BatchResumeCompatibilityTests(unittest.TestCase):
    def test_batch_still_skips_completed_ids_and_persists_only_unfinished(self) -> None:
        ground_truth = [
            {"id": 1, "question": "Completed", "ground_truth": "Reference 1"},
            {"id": 2, "question": "Pending", "ground_truth": "Reference 2"},
        ]
        pipeline_record = {
            "id": 2,
            "question": "Pending",
            "answer": "Generated",
            "contexts": ["Context"],
            "ground_truth": "Reference 2",
            "retrieval_latency_ms": 1.0,
            "generation_latency_ms": 2.0,
            "total_latency_ms": 3.0,
        }
        scores = pd.DataFrame(
            [
                {
                    "id": 2,
                    "question": "Pending",
                    "generated_answer": "Generated",
                    "faithfulness": 1.0,
                    "answer_relevancy": 1.0,
                    "context_precision": 1.0,
                    "context_recall": 1.0,
                    "answer_correctness": 1.0,
                }
            ]
        )
        outcome = SimpleNamespace(
            scores_df=scores,
            total_tokens=None,
            total_cost=None,
            token_usage_available=False,
        )
        cumulative = scores.assign(
            retrieval_latency_ms=1.0,
            generation_latency_ms=2.0,
            total_latency_ms=3.0,
            prompt_tokens=pd.NA,
            completion_tokens=pd.NA,
            total_tokens=pd.NA,
            estimated_cost=pd.NA,
        )

        with (
            patch.object(runner, "_warn_if_legacy_files_present"),
            patch.object(runner, "load_ground_truth", return_value=ground_truth),
            patch.object(runner, "get_completed_ids", return_value={"1"}),
            patch.object(
                runner,
                "build_ragas_llm_and_embeddings",
                return_value=(object(), object()),
            ),
            patch.object(runner, "run_rag_on_dataset", return_value=[pipeline_record]) as run_rag,
            patch.object(runner, "run_ragas_evaluation", return_value=outcome),
            patch.object(runner, "append_row_to_csv") as append,
            patch.object(runner.pd, "read_csv", return_value=cumulative),
            patch.object(runner, "update_token_stats_from_df"),
            patch.object(runner, "generate_report") as report,
            patch.object(runner, "_print_summary"),
            patch.object(runner, "tqdm", side_effect=lambda iterable, **kwargs: iterable),
        ):
            runner.main()

        run_rag.assert_called_once_with([ground_truth[1]])
        append.assert_called_once()
        self.assertEqual(append.call_args.args[0]["id"], 2)
        self.assertGreaterEqual(report.call_count, 1)


if __name__ == "__main__":
    unittest.main()
