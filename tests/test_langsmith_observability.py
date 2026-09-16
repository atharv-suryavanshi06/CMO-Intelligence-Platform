from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import multimodal_rag.rag.observability as observability
from multimodal_rag.api.config import APISettings
from multimodal_rag.rag.observability import (
    LangSmithObservability,
    active_request_context,
    observed,
    serialize_for_trace,
)


class _FakeRun:
    def __init__(self) -> None:
        self.outputs = None
        self.metadata = []

    def end(self, *, outputs=None, **_kwargs) -> None:
        self.outputs = outputs

    def add_metadata(self, metadata) -> None:
        self.metadata.append(metadata)

    def add_tags(self, _tags) -> None:
        pass


class _FakeTrace:
    def __init__(self, calls) -> None:
        self.calls = calls
        self.run = _FakeRun()

    def __enter__(self):
        self.calls.append(self)
        return self.run

    def __exit__(self, _exc_type, _exc, _tb):
        return False


class LangSmithObservabilityTests(unittest.TestCase):
    def test_serializer_preserves_content_but_redacts_sensitive_keys(self) -> None:
        value = serialize_for_trace(
            {
                "question": "Give me more questions for the CMO.",
                "document_context": "Full meeting context",
                "api_key": "do-not-log",
                "Authorization": "Bearer do-not-log",
                "nested": {"database_url": "postgresql://secret"},
            }
        )
        self.assertEqual(value["question"], "Give me more questions for the CMO.")
        self.assertEqual(value["document_context"], "Full meeting context")
        self.assertNotIn("api_key", value)
        self.assertNotIn("Authorization", value)
        self.assertEqual(value["nested"], {})

    def test_disabled_tracing_is_a_noop(self) -> None:
        calls = []

        @observed("test.disabled")
        def operation() -> str:
            calls.append(True)
            return "ok"

        with active_request_context(
            LangSmithObservability(False, "test", "test", 1.0),
            path="/answer",
            method="POST",
        ):
            self.assertEqual(operation(), "ok")
        self.assertEqual(calls, [True])

    def test_enabled_tracing_records_safe_inputs_and_outputs(self) -> None:
        trace_calls = []

        def fake_trace(*_args, **_kwargs):
            return _FakeTrace(trace_calls)

        def fake_context(**_kwargs):
            class Context:
                def __enter__(self):
                    return self

                def __exit__(self, _exc_type, _exc, _tb):
                    return False

            return Context()

        @observed("test.enabled")
        def operation(question: str, api_key: str) -> dict[str, str]:
            return {"answer": question, "api_key": api_key}

        configured = LangSmithObservability(True, "test-project", "test", 1.0, object())
        with patch.object(observability, "langsmith_trace", fake_trace), patch.object(
            observability, "tracing_context", fake_context
        ):
            with active_request_context(configured, path="/answer", method="POST"):
                result = operation("full user content", "secret")
        self.assertEqual(result["answer"], "full user content")
        self.assertEqual(len(trace_calls), 1)
        self.assertEqual(trace_calls[0].run.outputs["result"]["answer"], "full user content")
        self.assertNotIn("api_key", trace_calls[0].run.outputs["result"])

    def test_settings_load_langsmith_environment(self) -> None:
        with patch.dict(
            os.environ,
            {
                "LANGSMITH_TRACING": "true",
                "LANGSMITH_API_KEY": "test-key",
                "LANGSMITH_ENDPOINT": "https://example.test",
                "RAG_ENVIRONMENT": "staging",
                "LANGSMITH_TRACING_SAMPLING_RATE": "0.5",
            },
        ):
            settings = APISettings.from_environment()
        self.assertTrue(settings.langsmith_tracing)
        self.assertEqual(settings.langsmith_project, "cmo-intelligence-staging")
        self.assertEqual(settings.langsmith_tracing_sampling_rate, 0.5)


if __name__ == "__main__":
    unittest.main()
