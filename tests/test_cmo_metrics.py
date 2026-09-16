from multimodal_rag.evaluation.cmo_metrics import (
    _bounded_score,
    _load_completed_rows,
    _write_report,
    chunk_accuracy,
    retriever_accuracy,
    retrieval_scores,
)


def test_retrieval_scores_use_exact_source_labels():
    precision, recall = retrieval_scores(["a", "x", "b"], ["a", "b"])

    assert precision == 2 / 3
    assert recall == 1.0


def test_retriever_and_chunk_accuracy_are_reported_separately():
    assert retriever_accuracy(["noise", "answer"], ["answer"]) == {
        "hit_at_k": 1.0,
        "first_relevant_rank": 2,
        "mrr": 0.5,
    }
    assert chunk_accuracy(["answer", "noise"], ["answer"]) == {
        "precision": 0.5,
        "recall": 1.0,
        "f1": 2 / 3,
    }


def test_bounded_score_handles_invalid_and_out_of_range_judge_values():
    assert _bounded_score("0.75") == 0.75
    assert _bounded_score(2) == 1.0
    assert _bounded_score(-1) == 0.0
    assert _bounded_score("not-a-score") is None


def test_markdown_report_is_used_as_resume_checkpoint(tmp_path):
    report = tmp_path / "evaluation_result.md"
    report.write_text(
        "| ID | Question | Hit@5 | First relevant rank | Chunk precision | Chunk recall | Chunk F1 | Faithfulness | Answer relevancy | Answer correctness | Total latency (ms) | Status |\n"
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |\n"
        "| 7 | Completed \\| question | 1.0000 | 2 | 0.5000 | 1.0000 | 0.6667 | N/A | 0.8000 | 0.9000 | 12.3000 | completed |\n"
        "| 8 | Failed question | 0.0000 | N/A | 0.0000 | 0.0000 | 0.0000 | N/A | N/A | N/A | N/A | failed |\n",
        encoding="utf-8",
    )

    rows = _load_completed_rows(report)

    assert set(rows) == {7}
    assert rows[7]["question"] == "Completed | question"
    assert rows[7]["chunk_recall"] == 1.0


def test_report_contains_status_for_resume(tmp_path):
    report = tmp_path / "evaluation_result.md"
    _write_report(
        [{
            "id": 1,
            "question": "Question",
            "retriever_hit_at_k": 1.0,
            "first_relevant_rank": 1,
            "chunk_precision": 1.0,
            "chunk_recall": 1.0,
            "chunk_f1": 1.0,
            "faithfulness": None,
            "answer_relevancy": None,
            "answer_correctness": None,
            "total_latency_ms": None,
            "status": "completed",
        }],
        report,
    )

    assert "| Status |" in report.read_text(encoding="utf-8")
    assert "| 1 | Question | 1.0000 | 1 | 1.0000 | 1.0000 | 1.0000 | N/A | N/A | N/A | N/A | completed |" in report.read_text(encoding="utf-8")
