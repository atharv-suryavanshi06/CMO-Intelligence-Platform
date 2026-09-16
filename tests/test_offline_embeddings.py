"""Focused tests for the Gemini Embedding 2 integration."""

from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import numpy as np


MODEL_NAME = "gemini-embedding-2"


class _FakeEmbedding:
    def __init__(self, values):
        self.values = values


class _FakeResponse:
    def __init__(self, count: int):
        self.embeddings = [_FakeEmbedding([float(i + 1), 0.0, 0.0]) for i in range(count)]


class _FakeModels:
    def __init__(self):
        self.calls = []

    def embed_content(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResponse(len(kwargs["contents"]))


class _FakeClient:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.models = _FakeModels()
        self.__class__.instances.append(self)


class _FakeGenai(types.ModuleType):
    Client = _FakeClient


class _FakePart:
    @classmethod
    def from_text(cls, *, text):
        return {"text": text}


class _FakeContent:
    def __init__(self, *, parts):
        self.parts = parts


class _FakeEmbedContentConfig:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class GeminiEmbeddingTests(unittest.TestCase):
    def setUp(self):
        from multimodal_rag.rag.embedding import embedder

        self.embedder = embedder
        self.old_client = embedder._client
        self.old_client_key = embedder._client_key
        embedder._client = None
        embedder._client_key = None
        _FakeClient.instances.clear()
        self.fake_google_genai = _FakeGenai("google.genai")
        self.fake_google_genai.types = types.SimpleNamespace(
            Part=_FakePart,
            Content=_FakeContent,
            EmbedContentConfig=_FakeEmbedContentConfig,
        )
        self.fake_google = types.ModuleType("google")
        self.fake_google.genai = self.fake_google_genai

    def tearDown(self):
        self.embedder._client = self.old_client
        self.embedder._client_key = self.old_client_key

    def test_default_model_is_gemini_embedding_2(self):
        self.assertEqual(self.embedder.EmbeddingConfig().model_name, MODEL_NAME)
        self.assertEqual(self.embedder.EmbeddingConfig().output_dimensionality, 768)

    def test_gemini_client_requires_api_key(self):
        from multimodal_rag.rag.embedding.embedder import EmbeddingModelUnavailableError

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(EmbeddingModelUnavailableError):
                self.embedder._get_model(self.embedder.EmbeddingConfig())

    def test_embedding_requests_use_document_and_query_formats(self):
        with patch.dict(
            sys.modules,
            {"google": self.fake_google, "google.genai": self.fake_google_genai},
        ), patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            document_config = self.embedder.EmbeddingConfig(batch_size=2)
            document_vectors = self.embedder._embed_texts(["alpha", "beta"], document_config)
            query_config = self.embedder.EmbeddingConfig(input_type="query")
            query_vectors = self.embedder._embed_texts(["question"], query_config)

        client = _FakeClient.instances[0]
        self.assertEqual(client.kwargs, {"api_key": "test-key"})
        self.assertEqual(len(client.models.calls), 2)
        first_contents = client.models.calls[0]["contents"]
        self.assertEqual(first_contents[0].parts[0]["text"], "title: none | text: alpha")
        query_contents = client.models.calls[1]["contents"]
        self.assertEqual(query_contents[0].parts[0]["text"], "task: search result | query: question")
        self.assertEqual(tuple(document_vectors.shape), (2, 3))
        self.assertEqual(tuple(query_vectors.shape), (1, 3))
        self.assertAlmostEqual(float(document_vectors[0] @ document_vectors[0]), 1.0)

    def test_retriever_marks_query_input_without_changing_injected_seam(self):
        from multimodal_rag.rag.retrieval.retriever_2 import retrieve

        captured = []

        def fake_embed(texts, config):
            captured.append(config.input_type)
            import numpy as np
            return np.asarray([[1.0, 0.0]], dtype=np.float32)

        class FakeIndex:
            pass

        with patch("multimodal_rag.rag.retrieval.retriever_2.search", return_value=[]):
            retrieve("question", FakeIndex(), {}, embed_fn=fake_embed)
        self.assertEqual(captured, ["query"])

    def test_langchain_adapter_uses_same_gemini_provider(self):
        with patch.dict(
            sys.modules,
            {"google": self.fake_google, "google.genai": self.fake_google_genai},
        ), patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            adapter = self.embedder.GeminiEmbeddings()
            self.assertEqual(len(adapter.embed_documents(["document"])), 1)
            self.assertEqual(len(adapter.embed_query("query")), 3)

    def test_rate_limit_retries_the_same_batch_using_provider_delay(self):
        class RateLimitedModels:
            def __init__(self):
                self.calls = 0

            def embed_content(self, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("429 RESOURCE_EXHAUSTED: retry in 0s")
                return _FakeResponse(len(kwargs["contents"]))

        self.embedder._client = types.SimpleNamespace(models=RateLimitedModels())
        self.embedder._client_key = "test-key"
        with patch.dict(sys.modules, {"google": self.fake_google, "google.genai": self.fake_google_genai}), patch.dict(
            os.environ, {"GEMINI_API_KEY": "test-key"}, clear=True,
        ), patch("multimodal_rag.rag.embedding.embedder.time.sleep") as sleep:
            vectors = self.embedder._embed_texts(
                ["alpha"],
                self.embedder.EmbeddingConfig(max_inputs_per_minute=0),
            )

        self.assertEqual(self.embedder._client.models.calls, 2)
        sleep.assert_called_once_with(0.0)
        self.assertEqual(tuple(vectors.shape), (1, 3))

    def test_embedding_checkpoint_resumes_only_unfinished_chunks_and_is_cleared_on_success(self):
        records = [
            {"chunk_text": text, "metadata": {"chunk_id": f"c-{index}", "document_id": "doc"}}
            for index, text in enumerate(["first complete sentence.", "second complete sentence.", "third complete sentence."])
        ]
        config = self.embedder.EmbeddingConfig(max_inputs_per_minute=0)
        stubs = [
            self.embedder.EmbeddedChunk(
                chunk_id=f"c-{index}", document_id="doc", source_file="", page_numbers=[],
                section_title=None, chunk_text=record["chunk_text"], embedding_index=-1,
            )
            for index, record in enumerate(records)
        ]
        captured = {}

        def fake_embed(texts, _config, *, initial_vectors=None, on_batch_complete=None):
            captured["texts"] = texts
            captured["initial_vectors"] = initial_vectors.copy()
            vectors = np.vstack([initial_vectors, [[0.0, 0.0, 1.0]]]).astype(np.float32)
            on_batch_complete(vectors)
            return vectors

        with tempfile.TemporaryDirectory() as temporary_dir:
            self.embedder._write_embedding_progress(
                temporary_dir, np.asarray([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32), stubs, config,
            )
            with patch.object(self.embedder, "_embed_texts", side_effect=fake_embed):
                result = self.embedder.embed_chunks(records, config, checkpoint_dir=temporary_dir)
            self.embedder.write_embeddings(result, temporary_dir)

            self.assertEqual(captured["texts"], [record["chunk_text"] for record in records])
            self.assertEqual(tuple(captured["initial_vectors"].shape), (2, 3))
            self.assertEqual(len(result.chunks), 3)
            vectors_path, metadata_path = self.embedder._progress_paths(temporary_dir)
            self.assertFalse(vectors_path.exists())
            self.assertFalse(metadata_path.exists())

    def test_parent_context_is_stored_but_not_embedded(self):
        records = [
            {"chunk_text": "Complete parent section.", "metadata": {"chunk_id": "parent", "chunk_level": "parent"}},
            {"chunk_text": "Precise child evidence.", "metadata": {"chunk_id": "child", "chunk_level": "child"}},
        ]
        captured = []

        def fake_embed(texts, _config):
            captured.extend(texts)
            return np.asarray([[1.0, 0.0]], dtype=np.float32)

        result = self.embedder.embed_chunks(records, embed_fn=fake_embed)

        self.assertEqual(captured, ["Precise child evidence."])
        self.assertEqual([chunk.chunk_id for chunk in result.chunks], ["child"])
        self.assertEqual(result.skipped[0].reason, "stored parent context; child chunks are embedded for retrieval")


if __name__ == "__main__":
    unittest.main()
