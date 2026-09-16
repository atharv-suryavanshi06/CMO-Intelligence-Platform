from multimodal_rag.evaluation.cmo_retrieval_comparison import _scores


def test_retrieval_comparison_scores_exact_labels():
    metrics = _scores(["noise", "evidence"], {"evidence"})

    assert metrics == {
        "context_precision": 0.5,
        "context_recall": 1.0,
        "mrr": 0.5,
        "ndcg": 1 / 1.584962500721156,
    }
