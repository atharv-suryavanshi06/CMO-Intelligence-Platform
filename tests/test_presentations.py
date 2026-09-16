from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient

from multimodal_rag.api.config import APISettings
from multimodal_rag.api.main import create_app
from multimodal_rag.api.presentations import (
    GeneratedPresentation,
    InvalidPresentationTemplateError,
    NoMeetingPreparationError,
    PresentationArtifactError,
    PPTContextBuilder,
    PresentationGenerationService,
    PresentationProviderError,
    PresentationTemplate,
    PresentationTimeoutError,
    PresentonClient,
    PresentonGeneration,
    PresentonTask,
)


def meeting_payload(title: str = "Board Strategy") -> dict:
    return {
        "agent": "meeting_preparation",
        "status": "completed",
        "meeting_title": title,
        "meeting_objective": "Secure approval for the enterprise launch.",
        "executive_brief": "Lead with the commercial case and an execution path.",
        "key_facts_to_remember": ["Retention is the first proof point."],
        "strategic_talking_points": [{
            "topic": "Customer value",
            "talking_point": "Protect expansion revenue before accelerating acquisition.",
            "rationale_or_evidence": "Retention is already a stated meeting priority.",
        }],
        "questions_to_ask": [],
        "risks_to_watch": [],
        "recommended_actions": [],
        "sources": [{"chunk_id": "secret-source", "text": "Do not send this metadata."}],
        "trace_id": "trace-meeting",
        "user_id": "user-1",
        "chat_id": "chat-1",
    }


def strategy_payload(summary: str = "Focus on enterprise expansion.") -> dict:
    return {
        "agent": "market_strategy",
        "status": "completed",
        "executive_summary": summary,
        "strategic_situation": "The enterprise segment needs a focused motion.",
        "recommended_next_actions": ["Confirm the enterprise pilot owner."],
        "sources": [{"chunk_id": "secret-strategy-source"}],
        "trace_id": "trace-strategy",
        "user_id": "user-1",
        "chat_id": "chat-1",
    }


def chat_with(*messages: tuple[str, str, dict]) -> dict:
    return {
        "id": "chat-1",
        "title": "Board Strategy",
        "messages": [
            {"id": message_id, "role": role, "payload": payload}
            for message_id, role, payload in messages
        ],
    }


class PPTContextBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.builder = PPTContextBuilder()

    def test_meeting_only_context_contains_public_briefing_without_sources(self) -> None:
        context = self.builder.build(chat_with(("m1", "assistant", meeting_payload())))

        self.assertIn("Secure approval for the enterprise launch.", context.markdown)
        self.assertIn("Retention is the first proof point.", context.markdown)
        self.assertNotIn("secret-source", context.markdown)
        self.assertEqual(context.source_message_ids, ("m1",))

    def test_strategy_follow_up_after_meeting_is_included_and_other_agents_are_excluded(self) -> None:
        context = self.builder.build(chat_with(
            ("m1", "assistant", meeting_payload()),
            ("u1", "user", {"question": "What else?"}),
            ("s1", "assistant", strategy_payload()),
            ("i1", "assistant", {"agent": "market_intelligence", "status": "completed", "executive_summary": "Unrelated research."}),
        ))

        self.assertIn("Focus on enterprise expansion.", context.markdown)
        self.assertNotIn("Unrelated research.", context.markdown)
        self.assertNotIn("secret-strategy-source", context.markdown)
        self.assertEqual(context.source_message_ids, ("m1", "s1"))

    def test_latest_revision_replaces_old_brief_and_old_strategy(self) -> None:
        context = self.builder.build(chat_with(
            ("m1", "assistant", meeting_payload("Old Brief")),
            ("s1", "assistant", strategy_payload("Old strategy.")),
            ("m2", "assistant", meeting_payload("Current Brief")),
            ("s2", "assistant", strategy_payload("Current strategy.")),
            ("s3", "assistant", strategy_payload("Current strategy.")),
        ))

        self.assertIn("Current Brief", context.markdown)
        self.assertIn("Current strategy.", context.markdown)
        self.assertNotIn("Old Brief", context.markdown)
        self.assertNotIn("Old strategy.", context.markdown)
        self.assertEqual(context.source_message_ids, ("m2", "s2"))

    def test_missing_completed_meeting_is_rejected(self) -> None:
        with self.assertRaises(NoMeetingPreparationError):
            self.builder.build(chat_with(("s1", "assistant", strategy_payload())))


class FakePresentonProvider:
    def __init__(self) -> None:
        self.generate_calls: list[dict] = []

    def list_templates(self) -> list[PresentationTemplate]:
        return [PresentationTemplate(id="general", name="General")]

    def generate(self, **kwargs) -> PresentonGeneration:
        self.generate_calls.append(kwargs)
        return PresentonGeneration("presentation-1", "/app_data/presentation-1.pptx")

    def download(self, path: str) -> bytes:
        return b"PK\x03\x04pptx"


class PresentationServiceTests(unittest.TestCase):
    def test_template_is_validated_and_generation_uses_chat_context(self) -> None:
        provider = FakePresentonProvider()
        service = PresentationGenerationService(provider)

        generated = service.generate_from_chat(
            chat_with(("m1", "assistant", meeting_payload())),
            template_id="general",
            slide_count=7,
        )

        self.assertEqual(generated.content[:2], b"PK")
        self.assertEqual(generated.filename, "Board_Strategy.pptx")
        self.assertIn("Secure approval for the enterprise launch.", provider.generate_calls[0]["content"])
        self.assertEqual(provider.generate_calls[0]["slide_count"], 7)
        self.assertIn("web search", provider.generate_calls[0]["instructions"].lower())

    def test_invalid_template_is_rejected_before_generation(self) -> None:
        provider = FakePresentonProvider()
        service = PresentationGenerationService(provider)

        with self.assertRaises(InvalidPresentationTemplateError):
            service.generate_from_chat(
                chat_with(("m1", "assistant", meeting_payload())),
                template_id="unknown",
                slide_count=None,
            )
        self.assertEqual(provider.generate_calls, [])


class PresentonClientTests(unittest.TestCase):
    def test_template_envelope_and_layout_count_are_supported(self) -> None:
        response = Mock()
        response.is_success = True
        response.json.return_value = {
            "items": [
                {"id": "executive", "name": "Executive", "layout_count": 32},
            ],
            "total": 1,
        }
        client = PresentonClient("http://presenton.test", "presenton-key")

        with patch("multimodal_rag.api.presentations.httpx.get", return_value=response):
            templates = client.list_templates()

        self.assertEqual(templates, [PresentationTemplate(id="executive", name="Executive", total_layouts=32)])

    def test_generation_disables_web_search_and_forwards_template_and_slide_count(self) -> None:
        response = Mock()
        response.is_success = True
        response.json.return_value = {"presentation_id": "p1", "path": "/p1.pptx"}
        client = PresentonClient("http://presenton.test", "presenton-key")

        with patch("multimodal_rag.api.presentations.httpx.post", return_value=response) as post:
            client.generate(content="# Brief", template_id="general", slide_count=5, instructions="Only use context.")

        payload = post.call_args.kwargs["json"]
        self.assertFalse(payload["web_search"])
        self.assertEqual(payload["content_generation"], "preserve")
        self.assertEqual(payload["template"], "general")
        self.assertEqual(payload["n_slides"], 5)

    def test_provider_error_detail_is_preserved_for_actionable_api_errors(self) -> None:
        response = Mock()
        response.is_success = False
        response.status_code = 500
        response.json.return_value = {"detail": "Gemini quota exhausted"}
        client = PresentonClient("http://presenton.test", "presenton-key")

        with patch("multimodal_rag.api.presentations.httpx.post", return_value=response):
            with self.assertRaisesRegex(PresentationProviderError, "Gemini quota exhausted"):
                client.generate(content="# Brief", template_id="general", slide_count=None, instructions="Only use context.")

    def test_invalid_artifact_and_provider_timeout_are_rejected(self) -> None:
        invalid_response = Mock()
        invalid_response.is_success = True
        invalid_response.content = b"not-a-pptx"
        invalid_response.headers = {"content-type": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}
        client = PresentonClient("http://presenton.test", "presenton-key")
        with patch("multimodal_rag.api.presentations.httpx.get", return_value=invalid_response):
            with self.assertRaises(PresentationArtifactError):
                client.download("/bad.pptx")

        with patch("multimodal_rag.api.presentations.httpx.get", side_effect=httpx.TimeoutException("timed out")):
            with self.assertRaises(PresentationTimeoutError):
                client.list_templates()

    def test_safe_artifact_url_allows_public_https_artifacts_and_normalizes_internal_paths(self) -> None:
        client = PresentonClient("https://api.presenton.ai", "presenton-key")

        self.assertEqual(
            client._safe_artifact_url("https://api.presenton.ai:443/static/presentation.pptx"),
            "https://api.presenton.ai:443/static/presentation.pptx",
        )
        self.assertEqual(
            client._safe_artifact_url("https://downloads.example-cdn.test/presentation.pptx?signature=abc"),
            "https://downloads.example-cdn.test/presentation.pptx?signature=abc",
        )
        self_hosted_client = PresentonClient("http://127.0.0.1:5001", "presenton-key")
        self.assertEqual(
            self_hosted_client._safe_artifact_url("http://presenton:5001/static/presentation.pptx"),
            "http://127.0.0.1:5001/static/presentation.pptx",
        )
        with self.assertRaises(PresentationArtifactError):
            client._safe_artifact_url("file:///app_data/presentation.pptx")
        with self.assertRaises(PresentationArtifactError):
            client._safe_artifact_url("http://downloads.example-cdn.test/presentation.pptx")
        with self.assertRaises(PresentationArtifactError):
            client._safe_artifact_url("https://localhost/presentation.pptx")

    def test_external_artifact_download_does_not_receive_presenton_credentials(self) -> None:
        response = Mock()
        response.is_success = True
        response.content = b"PK\x03\x04presentation"
        response.headers = {"content-type": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}
        client = PresentonClient("https://api.presenton.ai", "presenton-key")

        with patch("multimodal_rag.api.presentations.httpx.get", return_value=response) as get:
            client.download("https://downloads.example-cdn.test/presentation.pptx?signature=abc")

        self.assertEqual(get.call_args.kwargs["headers"], {})


class PresentationAPITest(unittest.TestCase):
    class Store:
        def __init__(self) -> None:
            self.chat = None

        def initialize(self) -> None:
            pass

        def user_for_token(self, token: str) -> str | None:
            return "user-1" if token == "token-1" else None

        def get_chat(self, user_id: str, chat_id: str) -> dict | None:
            if user_id != "user-1" or chat_id != "chat-1":
                return None
            return self.chat

    class Memory:
        def initialize(self) -> None:
            pass

    class Service:
        def list_templates(self) -> list[PresentationTemplate]:
            return [PresentationTemplate(id="general", name="General")]

        def start_from_chat(self, chat: dict, *, template_id: str, slide_count: int | None) -> PresentonTask:
            PPTContextBuilder().build(chat)
            return PresentonTask("task-1", "pending", "Presentation generation task created")

        def task_status(self, task_id: str) -> PresentonTask:
            return PresentonTask("task-1", "completed", "Presentation generated", PresentonGeneration("p1", "/p1.pptx"))

        def download_task(self, task_id: str) -> GeneratedPresentation:
            return GeneratedPresentation(b"PK\x03\x04presentation", "Board_Strategy.pptx")

        def preview_task(self, task_id: str) -> bytes:
            return b"%PDF-1.7 preview"

    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = self.Store()
        self.store.chat = chat_with(("m1", "assistant", meeting_payload()))
        self.app = create_app(
            settings=APISettings(user_data_root=Path(self.temp_dir.name)),
            user_store=self.store,
            memory_service=self.Memory(),
            presentation_service=self.Service(),
        )
        self.client = TestClient(self.app, headers={"Authorization": "Bearer token-1"})

    def test_templates_and_task_generation_are_authenticated_and_scoped(self) -> None:
        templates = self.client.get("/presentations/templates")
        self.assertEqual(templates.status_code, 200)
        self.assertEqual(templates.json()[0]["id"], "general")

        task = self.client.post(
            "/presentations/generate",
            json={"chat_id": "chat-1", "template_id": "general", "slide_count": 7},
        )
        self.assertEqual(task.status_code, 200)
        self.assertEqual(task.json()["id"], "task-1")
        status_response = self.client.get("/presentations/generate/task-1")
        self.assertEqual(status_response.json()["status"], "completed")
        generated = self.client.get("/presentations/generate/task-1/download")
        self.assertEqual(generated.content[:2], b"PK")
        self.assertIn("Board_Strategy.pptx", generated.headers["content-disposition"])
        preview = self.client.get("/presentations/generate/task-1/preview")
        self.assertEqual(preview.content[:4], b"%PDF")

    def test_other_chat_is_not_accessible(self) -> None:
        response = self.client.post(
            "/presentations/generate",
            json={"chat_id": "other-chat", "template_id": "general"},
        )
        self.assertEqual(response.status_code, 404)

    def test_no_completed_meeting_is_rejected(self) -> None:
        self.store.chat = {"id": "chat-1", "title": "Empty", "messages": []}
        response = self.client.post(
            "/presentations/generate",
            json={"chat_id": "chat-1", "template_id": "general"},
        )
        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
