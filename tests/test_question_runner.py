"""Focused tests for the isolated one-question developer evaluator."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from multimodal_rag.evaluation import question_runner


SAMPLE_ITEMS = [
    {"id": 1, "question": "What is Enterprise AI?", "ground_truth": "Reference one."},
    {"id": 2, "question": "How is synthetic data used?", "ground_truth": "Reference two."},
]


class QuestionSelectionTests(unittest.TestCase):
    def test_selects_exact_id(self) -> None:
        selected = question_runner.select_ground_truth_item(
            SAMPLE_ITEMS, ground_truth_id="1"
        )
        self.assertEqual(selected["id"], 1)

    def test_question_normalization_is_exact_after_nfkc_whitespace_and_casefold(self) -> None:
        selected = question_runner.select_ground_truth_item(
            SAMPLE_ITEMS,
            question="  WHAT   IS  ENTERPRISE AI?  ",
        )
        self.assertEqual(selected["id"], 1)

    def test_conflicting_or_missing_selectors_are_rejected(self) -> None:
        with self.assertRaises(question_runner.SelectionError):
            question_runner.select_ground_truth_item(SAMPLE_ITEMS)
        with self.assertRaises(question_runner.SelectionError):
            question_runner.select_ground_truth_item(
                SAMPLE_ITEMS,
                ground_truth_id="1",
                question="What is Enterprise AI?",
            )

    def test_duplicate_normalized_question_is_rejected(self) -> None:
        duplicated = SAMPLE_ITEMS + [
            {"id": 3, "question": " what  is enterprise ai? ", "ground_truth": "Other."}
        ]
        with self.assertRaises(question_runner.SelectionError) as raised:
            question_runner.select_ground_truth_item(
                duplicated, question="What is Enterprise AI?"
            )
        self.assertEqual(len(raised.exception.candidates), 2)

    def test_no_match_stops_before_evaluation(self) -> None:
        with (
            patch.object(question_runner, "load_ground_truth", return_value=SAMPLE_ITEMS),
            patch.object(question_runner, "evaluate_ground_truth_item") as evaluate,
            patch("sys.stderr", new_callable=io.StringIO),
        ):
            exit_code = question_runner.main(["--id", "missing"])
        self.assertEqual(exit_code, 2)
        evaluate.assert_not_called()


class MetadataTests(unittest.TestCase):
    def test_metadata_is_loaded_read_only_by_chunk_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            document_dir = root / "doc"
            document_dir.mkdir()
            payload = [
                {
                    "chunk_text": "Full text",
                    "metadata": {
                        "chunk_id": "chunk-1",
                        "layout_type": "table",
                        "table_reference": "table-7",
                    },
                }
            ]
            chunks_path = document_dir / "chunks.json"
            chunks_path.write_text(json.dumps(payload), encoding="utf-8")
            before = chunks_path.read_bytes()

            with (
                patch.object(question_runner, "INGESTION_ARTIFACTS_DIR", root),
                patch.object(
                    question_runner,
                    "LEGACY_INGESTION_ARTIFACTS_DIR",
                    root / "absent",
                ),
            ):
                metadata, ambiguous = question_runner._load_chunk_metadata()

            self.assertEqual(metadata["chunk-1"]["layout_type"], "table")
            self.assertEqual(ambiguous, set())
            self.assertEqual(chunks_path.read_bytes(), before)


class EvaluationTraceTests(unittest.TestCase):
    @staticmethod
    def _fake_trace_dependencies():
        chunk = SimpleNamespace(
            chunk_id="chunk-1",
            document_id="doc-1",
            source_file="document.pdf",
            page_numbers=[7],
            section_title="Technology",
            chunk_text="FULL CHUNK TEXT\nSECOND LINE\nEND OF COMPLETE CHUNK",
            score=0.3361,
            combined_rerank_score=0.4861,
        )
        citation = SimpleNamespace(
            marker="S1",
            source_file="document.pdf",
            page_numbers=[7],
            chunk_id="chunk-1",
            section_title="Technology",
        )
        built = SimpleNamespace(prompt_text="prompt", source_map={"S1": chunk})
        cited_answer = SimpleNamespace(answer_text="Generated [S1]", citations=[citation])

        import pandas as pd

        scores = pd.DataFrame(
            [
                {
                    "faithfulness": 0.9,
                    "answer_relevancy": 0.8,
                    "context_precision": 0.7,
                    "context_recall": 0.6,
                    "answer_correctness": 0.5,
                }
            ]
        )
        usage = SimpleNamespace(input_tokens=100, output_tokens=25)
        outcome = SimpleNamespace(
            scores_df=scores,
            total_tokens=usage,
            total_cost=0.001,
            token_usage_available=True,
        )
        metadata = {
            "chunk-1": {
                "chunk_id": "chunk-1",
                "layout_type": "table",
                "table_reference": "table-7",
                "image_reference": None,
            }
        }
        return chunk, built, cited_answer, outcome, metadata

    def test_exactly_one_rag_and_ragas_execution_and_no_persistence(self) -> None:
        from multimodal_rag.evaluation import runner
        from multimodal_rag.rag.generation.answer_generator import GenerationResult

        chunk, built, cited_answer, outcome, metadata = self._fake_trace_dependencies()
        entry = SAMPLE_ITEMS[0]

        with (
            patch.object(question_runner, "_load_chunk_metadata", return_value=(metadata, set())),
            patch.object(runner, "retrieve", return_value=[chunk]) as retrieve,
            patch.object(runner, "build_prompt", return_value=built) as build_prompt,
            patch.object(
                runner,
                "generate_answer_with_metadata",
                return_value=GenerationResult(
                    text="Generated [S1]",
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                ),
            ) as generate,
            patch.object(runner, "resolve_citations", return_value=cited_answer),
            patch.object(runner, "run_ragas_evaluation", return_value=outcome) as ragas,
            patch.object(runner, "compute_composite_score", return_value=0.7),
            patch.object(runner, "get_completed_ids", side_effect=AssertionError("forbidden")) as completed,
            patch.object(runner, "append_row_to_csv", side_effect=AssertionError("forbidden")) as append,
            patch.object(runner, "save_results_csv", side_effect=AssertionError("forbidden")) as save,
            patch.object(runner, "generate_report", side_effect=AssertionError("forbidden")) as report,
        ):
            trace = question_runner.evaluate_ground_truth_item(entry)

        self.assertEqual(trace.status, "success")
        retrieve.assert_called_once()
        build_prompt.assert_called_once()
        generate.assert_called_once()
        ragas.assert_called_once()
        completed.assert_not_called()
        append.assert_not_called()
        save.assert_not_called()
        report.assert_not_called()
        self.assertEqual(trace.rag.configured_top_k, 8)
        self.assertEqual(trace.rag.actual_retrieved_count, 1)
        self.assertEqual(trace.rag.retrieved_items[0].chunk_text, chunk.chunk_text)
        self.assertEqual(trace.rag.retrieved_items[0].metadata["layout_type"], "table")
        self.assertEqual(trace.evaluator_total_tokens, 125)
        self.assertEqual(trace.rag.generation_prompt_tokens, 10)
        self.assertEqual(trace.rag.generation_completion_tokens, 5)
        self.assertEqual(trace.rag.generation_total_tokens, 15)
        self.assertEqual(trace.composite_score, 0.7)

        evaluated_records = ragas.call_args.args[0]
        self.assertEqual(len(evaluated_records), 1)
        self.assertEqual(
            evaluated_records[0]["contexts"],
            ["Page [7]: FULL CHUNK TEXT\nSECOND LINE\nEND OF COMPLETE CHUNK"],
        )

    def test_renderer_prints_complete_text_scores_metadata_and_generation_usage(self) -> None:
        chunk, _built, _cited_answer, _outcome, metadata = self._fake_trace_dependencies()
        rag = question_runner.RAGTrace(
            original_question=SAMPLE_ITEMS[0]["question"],
            generated_answer="Generated answer",
            retrieved_items=[
                question_runner.RetrievedItemTrace(
                    rank=1,
                    raw_vector_score=chunk.score,
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    document_name=chunk.source_file,
                    page_numbers=chunk.page_numbers,
                    section_title=chunk.section_title,
                    chunk_text=chunk.chunk_text,
                    metadata=metadata["chunk-1"],
                    combined_rerank_score=chunk.combined_rerank_score,
                )
            ],
            actual_retrieved_count=1,
            generation_prompt_tokens=10,
            generation_completion_tokens=5,
            generation_total_tokens=15,
        )
        trace = question_runner.QuestionEvaluationTrace(
            ground_truth_id=1,
            question=SAMPLE_ITEMS[0]["question"],
            reference_answer=SAMPLE_ITEMS[0]["ground_truth"],
            rag=rag,
            status="success",
        )
        output = io.StringIO()

        question_runner.print_trace(trace, output)
        rendered = output.getvalue()

        self.assertIn("Raw vector similarity score: 0.33610000000000001", rendered)
        self.assertIn("Combined rerank score: 0.48609999999999998", rendered)
        self.assertIn("FULL CHUNK TEXT\nSECOND LINE\nEND OF COMPLETE CHUNK", rendered)
        self.assertIn('"layout_type": "table"', rendered)
        self.assertIn("Generation prompt tokens: 10", rendered)
        self.assertIn("Generation completion tokens: 5", rendered)
        self.assertIn("Generation total tokens: 15", rendered)


if __name__ == "__main__":
    unittest.main()
