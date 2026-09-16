"""Deterministic tests for the shared generic RAG trace."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from multimodal_rag.rag.generation.answer_generator import GenerationResult
from multimodal_rag.paths import INDEX_DIR
from multimodal_rag.rag.indexing.chroma_index import IndexedChunkRef, load_index
from multimodal_rag.rag.retrieval.retriever_2 import (
    RetrievedChunk,
    RetrieverConfig,
    retrieve as retrieve_chunks,
)
from multimodal_rag.rag.trace import expand_parent_context, run_rag_trace


class SharedRAGTraceTests(unittest.TestCase):
    def test_parent_context_replaces_retrieved_child_text_without_changing_its_citation_id(self) -> None:
        child = RetrievedChunk(
            "child", "doc", "guide.pdf", [1], "Section", "small match", 0.9,
            parent_chunk_text="Complete parent section context.",
        )
        expanded = expand_parent_context([child])

        self.assertEqual(expanded[0].chunk_id, "child")
        self.assertEqual(expanded[0].chunk_text, "Complete parent section context.")

    def test_expand_parent_context_leaves_chunk_unchanged_without_parent_text(self) -> None:
        child = RetrievedChunk("child", "doc", "guide.pdf", [1], "Section", "small match", 0.9)
        expanded = expand_parent_context([child])

        self.assertEqual(expanded[0].chunk_text, "small match")

    def test_trace_executes_each_backend_step_once_and_keeps_diagnostics(self) -> None:
        chunk = SimpleNamespace(
            chunk_id="chunk-1",
            document_id="doc-1",
            source_file="guide.pdf",
            page_numbers=[4],
            section_title="Overview",
            chunk_text="Complete chunk text",
            score=0.91,
            combined_rerank_score=0.97,
            bm25_score=1.25,
            rrf_score=0.02,
            cross_encoder_score=0.88,
        )
        citation = SimpleNamespace(
            marker="S1",
            source_file="guide.pdf",
            page_numbers=[4],
            chunk_id="chunk-1",
            section_title="Overview",
        )
        retrieve = Mock(return_value=[chunk])
        build_prompt = Mock(return_value=SimpleNamespace(prompt_text="prompt", source_map={"S1": chunk}))
        generate = Mock(return_value="Answer [S1]")
        resolve = Mock(
            return_value=SimpleNamespace(
                answer_text="Answer [S1]",
                citations=[citation],
                uncited_sources=[],
            )
        )

        trace = run_rag_trace(
            "Question",
            index=object(),
            id_map={},
            top_k=5,
            metadata_by_id={"chunk-1": {"layout_type": "table"}},
            retrieve_fn=retrieve,
            build_prompt_fn=build_prompt,
            generate_answer_fn=generate,
            resolve_citations_fn=resolve,
        )

        retrieve.assert_called_once()
        build_prompt.assert_called_once()
        generate.assert_called_once()
        resolve.assert_called_once()
        self.assertEqual(trace.configured_top_k, 5)
        self.assertEqual(trace.actual_retrieved_count, 1)
        self.assertEqual(trace.retrieved_items[0].raw_vector_score, 0.91)
        self.assertEqual(trace.retrieved_items[0].combined_rerank_score, 0.97)
        self.assertEqual(trace.retrieved_items[0].bm25_score, 1.25)
        self.assertEqual(trace.retrieved_items[0].rrf_score, 0.02)
        self.assertEqual(trace.retrieved_items[0].cross_encoder_score, 0.88)
        self.assertEqual(trace.retrieved_items[0].metadata["layout_type"], "table")
        self.assertEqual(trace.citations[0]["marker"], "S1")
        self.assertIsNotNone(trace.retrieval_latency_ms)
        self.assertIsNotNone(trace.generation_latency_ms)
        self.assertIsNotNone(trace.rag_latency_ms)

    def test_structured_generation_usage_is_copied_without_an_extra_call(self) -> None:
        chunk = SimpleNamespace(
            chunk_id="chunk-1",
            document_id="doc-1",
            source_file="guide.pdf",
            page_numbers=[4],
            section_title="Overview",
            chunk_text="Complete chunk text",
            score=0.91,
            combined_rerank_score=0.97,
        )
        generate = Mock(
            return_value=GenerationResult(
                text="Answer",
                prompt_tokens=12,
                completion_tokens=7,
                total_tokens=19,
            )
        )
        trace = run_rag_trace(
            "Question",
            index=object(),
            id_map={},
            top_k=5,
            metadata_by_id={},
            retrieve_fn=Mock(return_value=[chunk]),
            build_prompt_fn=Mock(
                return_value=SimpleNamespace(prompt_text="prompt", source_map={})
            ),
            generate_answer_fn=generate,
            resolve_citations_fn=Mock(
                return_value=SimpleNamespace(
                    answer_text="Answer", citations=[], uncited_sources=[]
                )
            ),
        )

        generate.assert_called_once()
        self.assertEqual(trace.generation_prompt_tokens, 12)
        self.assertEqual(trace.generation_completion_tokens, 7)
        self.assertEqual(trace.generation_total_tokens, 19)


class RetrieverScoreTelemetryTests(unittest.TestCase):
    @staticmethod
    def _ref(index_id: int, chunk_id: str, text: str) -> IndexedChunkRef:
        return IndexedChunkRef(
            index_id=str(index_id),
            chunk_id=chunk_id,
            document_id="doc",
            source_file="guide.pdf",
            page_numbers=[index_id + 1],
            section_title=None,
            chunk_text=text,
        )

    def test_ranking_matches_existing_formula_and_raw_scores_are_unchanged(self) -> None:
        less_overlap = self._ref(0, "less-overlap", "alpha")
        full_overlap = self._ref(1, "full-overlap", "alpha beta")
        raw_results = [(less_overlap, 0.80), (full_overlap, 0.75)]

        with patch(
            "multimodal_rag.rag.retrieval.retriever_2.search",
            return_value=raw_results,
        ):
            results = retrieve_chunks(
                "alpha beta",
                object(),
                {},
                retriever_config=RetrieverConfig(
                    top_k=2,
                    enable_hybrid=False,
                    lexical_rerank_weight=0.15,
                ),
                embed_fn=lambda texts, config: [[0.0]],
            )

        expected_legacy_order = sorted(
            raw_results,
            key=lambda pair: pair[1]
            + 0.15 * (len(set("alpha beta".split()) & set(pair[0].chunk_text.split())) / 2),
            reverse=True,
        )
        self.assertEqual(
            [result.chunk_id for result in results],
            [ref.chunk_id for ref, _score in expected_legacy_order],
        )
        self.assertEqual(
            {result.chunk_id: result.score for result in results},
            {ref.chunk_id: score for ref, score in raw_results},
        )
        self.assertAlmostEqual(results[0].combined_rerank_score, 0.90)
        self.assertAlmostEqual(results[1].combined_rerank_score, 0.875)

    def test_candidate_pool_is_expanded_but_returned_top_k_is_preserved(self) -> None:
        refs = [self._ref(i, f"chunk-{i}", "alpha") for i in range(20)]
        raw_results = [(ref, 1.0 - i / 100.0) for i, ref in enumerate(refs)]
        embed = Mock(return_value=[[0.0]])

        with patch(
            "multimodal_rag.rag.retrieval.retriever_2.search",
            return_value=raw_results,
        ) as search_mock:
            results = retrieve_chunks(
                "alpha",
                object(),
                {},
                retriever_config=RetrieverConfig(top_k=5),
                embed_fn=embed,
            )

        search_mock.assert_called_once()
        embed.assert_called_once()
        self.assertEqual(search_mock.call_args.kwargs["top_k"], 40)
        self.assertEqual(len(results), 5)

    def test_top_k_eight_uses_pool_forty_and_returns_at_most_eight(self) -> None:
        refs = [self._ref(i, f"chunk-{i}", "alpha") for i in range(20)]
        raw_results = [(ref, 1.0 - i / 100.0) for i, ref in enumerate(refs)]

        with patch(
            "multimodal_rag.rag.retrieval.retriever_2.search",
            return_value=raw_results,
        ) as search_mock:
            results = retrieve_chunks(
                "alpha",
                object(),
                {},
                retriever_config=RetrieverConfig(top_k=8),
                embed_fn=lambda texts, config: [[0.0]],
            )

        search_mock.assert_called_once()
        self.assertEqual(search_mock.call_args.kwargs["top_k"], 40)
        self.assertEqual(len(results), 8)

    def test_candidate_pool_is_never_smaller_than_requested_top_k(self) -> None:
        refs = [self._ref(i, f"chunk-{i}", "alpha") for i in range(25)]
        raw_results = [(ref, 1.0 - i / 100.0) for i, ref in enumerate(refs)]

        with patch(
            "multimodal_rag.rag.retrieval.retriever_2.search",
            return_value=raw_results,
        ) as search_mock:
            results = retrieve_chunks(
                "alpha",
                object(),
                {},
                retriever_config=RetrieverConfig(top_k=25),
                embed_fn=lambda texts, config: [[0.0]],
            )

        search_mock.assert_called_once()
        self.assertEqual(search_mock.call_args.kwargs["top_k"], 40)
        self.assertEqual(len(results), 25)

    def test_generic_hyphen_normalization_matches_spaced_terms(self) -> None:
        hyphenated = self._ref(0, "hyphenated", "long-term short-term")
        spaced = self._ref(1, "spaced", "long term short term")

        with patch(
            "multimodal_rag.rag.retrieval.retriever_2.search",
            return_value=[(hyphenated, 0.5), (spaced, 0.5)],
        ):
            results = retrieve_chunks(
                "long term short term",
                object(),
                {},
                retriever_config=RetrieverConfig(top_k=2, enable_hybrid=False),
                embed_fn=lambda texts, config: [[0.0]],
            )

        self.assertAlmostEqual(results[0].combined_rerank_score, 0.65)
        self.assertAlmostEqual(results[1].combined_rerank_score, 0.65)

    def test_known_regression_query_returns_both_page_six_data_chunks(self) -> None:
        index_path = INDEX_DIR / "chroma_manifest.json"
        id_map_path = INDEX_DIR / "chroma_manifest.json"
        if not index_path.exists() or not id_map_path.exists():
            self.skipTest("existing local ChromaDB artifacts are unavailable")

        index, id_map = load_index(INDEX_DIR)
        results = retrieve_chunks(
            "state short and long term implications of data",
            index,
            id_map,
            retriever_config=RetrieverConfig(top_k=5),
        )

        result_ids = {result.chunk_id for result in results}
        self.assertTrue({"c_f3f98ad9ec56", "c_6294b8586915"}.issubset(result_ids))

    def test_min_score_filters_expanded_candidates_before_final_slice(self) -> None:
        refs = [self._ref(0, "below", "alpha"), self._ref(1, "above", "alpha")]

        with patch(
            "multimodal_rag.rag.retrieval.retriever_2.search",
            return_value=[(refs[0], 0.2), (refs[1], 0.8)],
        ):
            results = retrieve_chunks(
                "alpha",
                object(),
                {},
                retriever_config=RetrieverConfig(top_k=5, min_score=0.5),
                embed_fn=lambda texts, config: [[0.0]],
            )

        self.assertEqual([result.chunk_id for result in results], ["above"])

    def test_disabled_reranking_preserves_order_and_uses_raw_combined_score(self) -> None:
        first = self._ref(0, "first", "alpha")
        second = self._ref(1, "second", "alpha beta")
        with patch(
            "multimodal_rag.rag.retrieval.retriever_2.search",
            return_value=[(first, 0.80), (second, 0.75)],
        ):
            results = retrieve_chunks(
                "alpha beta",
                object(),
                {},
                retriever_config=RetrieverConfig(top_k=2, lexical_rerank_weight=0.0, enable_hybrid=False),
                embed_fn=lambda texts, config: [[0.0]],
            )

        self.assertEqual([result.chunk_id for result in results], ["first", "second"])
        self.assertEqual(
            [result.combined_rerank_score for result in results], [0.80, 0.75]
        )

    def test_legacy_retrieved_chunk_construction_still_works(self) -> None:
        chunk = RetrievedChunk("id", "doc", "file.pdf", [1], None, "text", 0.5)
        self.assertEqual(chunk.score, 0.5)
        self.assertIsNone(chunk.combined_rerank_score)

    def test_hybrid_retrieval_deduplicates_and_exposes_scores(self) -> None:
        dense_only = self._ref(0, "dense-only", "alpha evidence")
        shared = self._ref(1, "shared", "alpha beta evidence")
        sparse_only = self._ref(2, "sparse-only", "beta evidence")
        with (
            patch(
                "multimodal_rag.rag.retrieval.retriever_2.search",
                return_value=[(shared, 0.80), (dense_only, 0.70)],
            ) as dense_search,
            patch(
                "multimodal_rag.rag.retrieval.retriever_2._bm25_rank",
                return_value=[(shared, 2.0), (sparse_only, 1.5)],
            ) as sparse_search,
        ):
            results = retrieve_chunks(
                "alpha beta",
                object(),
                {},
                retriever_config=RetrieverConfig(
                    top_k=3,
                    enable_hybrid=True,
                    dense_candidate_k=2,
                    sparse_candidate_k=2,
                ),
                embed_fn=lambda texts, config: [[0.0]],
            )

        dense_search.assert_called_once()
        sparse_search.assert_called_once()
        self.assertEqual(len(results), 3)
        self.assertEqual(len({result.chunk_id for result in results}), 3)
        shared_result = next(result for result in results if result.chunk_id == "shared")
        self.assertEqual(shared_result.score, 0.80)
        self.assertEqual(shared_result.bm25_score, 2.0)
        self.assertIsNotNone(shared_result.rrf_score)
        self.assertIsNone(shared_result.cross_encoder_score)

    def test_rrf_rewards_candidates_present_in_both_rankings(self) -> None:
        both = self._ref(0, "both", "alpha")
        dense_only = self._ref(1, "dense-only", "alpha")
        sparse_only = self._ref(2, "sparse-only", "alpha")
        with (
            patch(
                "multimodal_rag.rag.retrieval.retriever_2.search",
                return_value=[(both, 0.8), (dense_only, 0.79)],
            ),
            patch(
                "multimodal_rag.rag.retrieval.retriever_2._bm25_rank",
                return_value=[(both, 2.0), (sparse_only, 1.9)],
            ),
        ):
            results = retrieve_chunks(
                "alpha",
                object(),
                {},
                retriever_config=RetrieverConfig(top_k=3, enable_hybrid=True),
                embed_fn=lambda texts, config: [[0.0]],
            )

        self.assertEqual(results[0].chunk_id, "both")
        self.assertGreater(results[0].rrf_score, results[1].rrf_score)

    def test_cross_encoder_scores_only_fused_candidates(self) -> None:
        dense = self._ref(0, "dense", "alpha")
        shared = self._ref(1, "shared", "alpha beta")
        sparse = self._ref(2, "sparse", "beta")
        with (
            patch(
                "multimodal_rag.rag.retrieval.retriever_2.search",
                return_value=[(dense, 0.8), (shared, 0.7)],
            ),
            patch(
                "multimodal_rag.rag.retrieval.retriever_2._bm25_rank",
                return_value=[(shared, 2.0), (sparse, 1.5)],
            ),
            patch(
                "multimodal_rag.rag.retrieval.retriever_2._cross_encoder_rank",
                side_effect=lambda _query, candidates, _model: [
                    {"dense": 0.2, "shared": 0.9, "sparse": 0.4}[candidate.chunk_id]
                    for candidate in candidates
                ],
            ) as cross_rank,
        ):
            results = retrieve_chunks(
                "alpha beta",
                object(),
                {},
                retriever_config=RetrieverConfig(
                    top_k=2,
                    enable_hybrid=True,
                    enable_cross_encoder=True,
                ),
                embed_fn=lambda texts, config: [[0.0]],
            )

        cross_rank.assert_called_once()
        self.assertEqual(len(cross_rank.call_args.args[1]), 3)
        self.assertEqual(cross_rank.call_args.args[0], "alpha beta")
        self.assertEqual(results[0].chunk_id, "shared")
        self.assertEqual(results[0].cross_encoder_score, 0.9)


if __name__ == "__main__":
    unittest.main()
