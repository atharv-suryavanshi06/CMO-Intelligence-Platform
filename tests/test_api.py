from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import fitz
from fastapi.testclient import TestClient

from multimodal_rag.api.conversation_reuse import ReuseDecision
from multimodal_rag.api.main import create_app
from multimodal_rag.api.config import APISettings
from multimodal_rag.api.schemas import ChunkResponse, SourceResponse
from multimodal_rag.api.service import AnswerResult, RetrievalResult, UserIndexNotFoundError


class FakeRAGService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.chunk = ChunkResponse(
            text="Marketing evidence",
            source="report.pdf",
            document="report-1",
            page=3,
            score=0.92,
            metadata={"chunk_id": "chunk-1", "page_numbers": [3]},
        )

    def retrieve(self, **kwargs):
        self.calls.append(("retrieve", kwargs))
        if kwargs["user_id"] == "missing":
            raise UserIndexNotFoundError("No index is available for user_id 'missing'.")
        return RetrievalResult([self.chunk])

    def answer(self, **kwargs):
        self.calls.append(("answer", kwargs))
        return AnswerResult(
            answer="Grounded marketing answer.",
            chunks=[self.chunk],
            sources=[SourceResponse(source="report.pdf", document="report-1", pages=[3], chunk_id="chunk-1")],
            trace=SimpleNamespace(
                original_question="Summarize",
                configured_top_k=8,
                actual_retrieved_count=1,
                retrieved_items=[],
                citations=[],
                uncited_sources=[],
            ),
        )


class FakeIngestionManager:
    def __init__(self) -> None:
        self.submitted = None
        self.jobs = {}

    def submit(self, **kwargs):
        self.submitted = kwargs
        kwargs["pdf_path"].unlink(missing_ok=True)
        record = {
            "job_id": kwargs["job_id"],
            "filename": kwargs["filename"],
            "user_id": kwargs["scope"].user_id,
            "project_id": kwargs["scope"].project_id,
            "status": "queued",
            "stage": "upload",
            "document_id": None,
            "chunk_count": 0,
            "embedded_count": 0,
            "error": None,
        }
        self.jobs[record["job_id"]] = record
        return record

    def get(self, job_id):
        return self.jobs.get(job_id)

    def shutdown(self):
        return None


class FakeReuseClassifier:
    def __init__(self, decision: ReuseDecision) -> None:
        self.decision = decision
        self.calls: list[tuple[str, list]] = []

    def classify(self, question: str, exchanges: list) -> ReuseDecision:
        self.calls.append((question, exchanges))
        return self.decision


class FakeMemoryService:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str | None, str]] = []

    def initialize(self) -> None:
        return None

    def process_message(self, user_id: str, chat_id: str | None, message: str) -> list[str]:
        self.messages.append((user_id, chat_id, message))
        return []

    def get_user_context(self, user_id: str, chat_id: str | None = None) -> dict:
        return {"profile": {}, "business_context": {}}

    def clone_context(self, user_id: str, source_chat_id: str, target_chat_id: str) -> None:
        pass


class FakeUserStore:
    def __init__(self) -> None:
        self.users: dict[str, tuple[str, str]] = {}
        self.tokens: dict[str, str] = {}
        self.chats: dict[str, dict] = {}

    def initialize(self) -> None:
        return None

    def create_user(self, username: str, password: str) -> str | None:
        if username in self.users:
            return None
        user_id = f"user-{len(self.users) + 1}"
        self.users[username] = (user_id, password)
        return user_id

    def authenticate(self, username: str, password: str) -> str | None:
        record = self.users.get(username)
        return record[0] if record and record[1] == password else None

    def create_session(self, user_id: str) -> str:
        token = f"token-{user_id}"
        self.tokens[token] = user_id
        return token

    def user_for_token(self, token: str) -> str | None:
        return self.tokens.get(token)

    def create_chat(self, user_id: str, chat_id: str, title: str) -> dict:
        chat = {"id": chat_id, "user_id": user_id, "title": title, "created_at": datetime.now(UTC), "updated_at": datetime.now(UTC), "messages": []}
        self.chats[chat_id] = chat
        return {key: value for key, value in chat.items() if key != "messages"} | {"message_count": 0}

    def append_message(self, user_id: str, chat_id: str, role: str, payload: dict) -> None:
        chat = self.chats.setdefault(chat_id, {"id": chat_id, "user_id": user_id, "title": payload.get("question", "New conversation"), "created_at": datetime.now(UTC), "updated_at": datetime.now(UTC), "messages": []})
        if chat["user_id"] != user_id:
            raise ValueError("Conversation does not belong to this user.")
        chat["updated_at"] = datetime.now(UTC)
        chat["messages"].append({"id": str(len(chat["messages"]) + 1), "role": role, "payload": payload, "created_at": datetime.now(UTC)})

    def list_chats(self, user_id: str) -> list[dict]:
        return [{key: value for key, value in chat.items() if key != "messages"} | {"message_count": len(chat["messages"])} for chat in self.chats.values() if chat["user_id"] == user_id]

    def get_chat(self, user_id: str, chat_id: str) -> dict | None:
        chat = self.chats.get(chat_id)
        return chat if chat and chat["user_id"] == user_id else None

    def delete_chat(self, user_id: str, chat_id: str) -> bool:
        chat = self.chats.get(chat_id)
        if not chat or chat["user_id"] != user_id:
            return False
        del self.chats[chat_id]
        return True

    def get_recent_exchanges(self, user_id: str, chat_id: str, limit: int = 10) -> list[dict]:
        chat = self.chats.get(chat_id)
        if not chat or chat["user_id"] != user_id:
            return []
        exchanges: list[dict] = []
        question = None
        for message in chat["messages"]:
            if message["role"] == "user":
                question = message["payload"].get("question")
            else:
                exchanges.append({"message_id": message["id"], "question": question, "payload": message["payload"]})
                question = None
        return exchanges[-limit:]


class APITest(unittest.TestCase):
    def setUp(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.tenant_data_root = Path(temp_dir.name)
        self.service = FakeRAGService()
        self.ingestion_manager = FakeIngestionManager()
        self.memory_service = FakeMemoryService()
        self.app = create_app(
            settings=APISettings(
                user_data_root=self.tenant_data_root,
                api_auth_token="test-token",
                api_username="admin",
                api_password="secret",
            ),
            rag_service=self.service,
            ingestion_manager=self.ingestion_manager,
            memory_service=self.memory_service,
        )
        self.client = TestClient(self.app)
        self.client.headers.update({"Authorization": "Bearer test-token"})

    def test_login_returns_bearer_token_for_valid_credentials(self) -> None:
        client = TestClient(self.app)

        response = client.post(
            "/auth/login",
            json={"username": "admin", "password": "secret"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"access_token": "test-token", "token_type": "bearer"},
        )

    def test_login_rejects_invalid_credentials(self) -> None:
        client = TestClient(self.app)

        response = client.post(
            "/auth/login",
            json={"username": "admin", "password": "wrong"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.service.calls, [])

    def test_login_fails_closed_when_credentials_are_unconfigured(self) -> None:
        app = create_app(
            settings=APISettings(
                user_data_root=self.tenant_data_root,
                api_auth_token="test-token",
            ),
            rag_service=self.service,
        )

        response = TestClient(app).post(
            "/auth/login",
            json={"username": "admin", "password": "secret"},
        )

        self.assertEqual(response.status_code, 503)

    def test_signup_signin_and_user_scoped_chat_history(self) -> None:
        store = FakeUserStore()
        app = create_app(
            settings=APISettings(user_data_root=self.tenant_data_root),
            rag_service=self.service,
            ingestion_manager=self.ingestion_manager,
            memory_service=self.memory_service,
            user_store=store,
        )
        client = TestClient(app)
        signup = client.post("/auth/signup", json={"username": "alice", "password": "password-1"})
        self.assertEqual(signup.status_code, 201)
        credentials = {"Authorization": f"Bearer {signup.json()['access_token']}"}
        user_id = signup.json()["user_id"]
        answer = client.post("/answer", headers=credentials, json={"question": "Summarize", "user_id": user_id, "chat_id": "chat-1"})
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(client.get("/chats", headers=credentials).json()[0]["title"], "Summarize")
        self.assertEqual(client.get("/chats/chat-1", headers=credentials).json()["messages"][1]["role"], "assistant")
        self.assertEqual(client.post("/answer", headers=credentials, json={"question": "x", "user_id": "another-user"}).status_code, 403)
        self.assertEqual(client.post("/auth/login", json={"username": "alice", "password": "password-1"}).status_code, 200)

        signup_2 = client.post("/auth/signup", json={"username": "bob", "password": "password-2"})
        other_credentials = {"Authorization": f"Bearer {signup_2.json()['access_token']}"}
        self.assertEqual(client.request("DELETE", "/chats/chat-1", headers=other_credentials).status_code, 404)
        self.assertIn("chat-1", store.chats)

        self.assertEqual(client.request("DELETE", "/chats/chat-1", headers=credentials).status_code, 204)
        self.assertNotIn("chat-1", store.chats)
        self.assertEqual(client.get("/chats", headers=credentials).json(), [])
        self.assertEqual(client.get("/chats/chat-1", headers=credentials).status_code, 404)
        self.assertEqual(client.request("DELETE", "/chats/chat-1", headers=credentials).status_code, 404)

    def test_missing_bearer_token_is_rejected_before_service(self) -> None:
        client = TestClient(self.app)

        response = client.post("/retrieve", json={"question": "x", "user_id": "alice"})

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["WWW-Authenticate"], "Bearer")
        self.assertEqual(self.service.calls, [])

    def test_invalid_bearer_token_is_rejected_before_service(self) -> None:
        client = TestClient(self.app, headers={"Authorization": "Bearer wrong-token"})

        response = client.post("/answer", json={"question": "x", "user_id": "alice"})

        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.service.calls, [])

    def test_unconfigured_auth_fails_closed(self) -> None:
        app = create_app(
            settings=APISettings(user_data_root=self.tenant_data_root),
            rag_service=self.service,
        )
        client = TestClient(app, headers={"Authorization": "Bearer test-token"})

        response = client.post("/retrieve", json={"question": "x", "user_id": "alice"})

        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.service.calls, [])

    def test_cors_allows_authorization_header(self) -> None:
        response = self.client.options(
            "/retrieve",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Authorization, Content-Type",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Authorization", response.headers["access-control-allow-headers"])

    def test_pdf_upload_creates_scoped_ingestion_job(self) -> None:
        pdf = fitz.open()
        pdf.new_page()
        pdf_bytes = pdf.tobytes()
        pdf.close()
        response = self.client.post(
            "/ingest",
            data={"user_id": "alice", "project_id": "launch-2026"},
            files={"file": ("brief.pdf", pdf_bytes, "application/pdf")},
        )

        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(body["filename"], "brief.pdf")
        self.assertEqual(body["user_id"], "alice")
        self.assertEqual(body["project_id"], "launch-2026")
        self.assertEqual(body["stage"], "upload")
        self.assertEqual(
            self.ingestion_manager.submitted["scope"].ingestion_artifacts_dir,
            self.tenant_data_root / "alice" / "projects" / "launch-2026" / "artifacts" / "ingestion",
        )

    def test_pdf_upload_rejects_empty_file(self) -> None:
        response = self.client.post(
            "/ingest",
            data={"user_id": "alice"},
            files={"file": ("empty.pdf", b"", "application/pdf")},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Empty files", response.json()["detail"])
        self.assertIsNone(self.ingestion_manager.submitted)

    def test_pdf_upload_rejects_more_than_fifty_pages(self) -> None:
        pdf = fitz.open()
        for _ in range(51):
            pdf.new_page()
        pdf_bytes = pdf.tobytes()
        pdf.close()

        response = self.client.post(
            "/ingest",
            data={"user_id": "alice"},
            files={"file": ("large.pdf", pdf_bytes, "application/pdf")},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("50 pages or fewer", response.json()["detail"])
        self.assertIsNone(self.ingestion_manager.submitted)

    def test_pdf_upload_rejects_non_pdf_content(self) -> None:
        response = self.client.post(
            "/ingest",
            data={"user_id": "alice"},
            files={"file": ("notes.txt", b"not a pdf", "text/plain")},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIsNone(self.ingestion_manager.submitted)

    def test_upload_rejects_format_outside_allowlist(self) -> None:
        response = self.client.post(
            "/ingest",
            data={"user_id": "alice"},
            files={"file": ("sheet.xlsx", b"not supported", "application/octet-stream")},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("PDF, MP4, MP3, DOC, DOCX, PPT, PPTX", response.json()["detail"])
        self.assertIsNone(self.ingestion_manager.submitted)

    def test_docx_upload_creates_scoped_ingestion_job(self) -> None:
        response = self.client.post(
            "/ingest",
            data={"user_id": "alice"},
            files={"file": ("brief.docx", b"word bytes", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.ingestion_manager.submitted["pdf_path"].suffix, ".docx")

    def test_mp3_upload_creates_scoped_ingestion_job(self) -> None:
        response = self.client.post(
            "/ingest",
            data={"user_id": "alice"},
            files={"file": ("brief.mp3", b"audio bytes", "audio/mpeg")},
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.ingestion_manager.submitted["pdf_path"].suffix, ".mp3")

    def test_pptx_upload_creates_scoped_ingestion_job(self) -> None:
        response = self.client.post(
            "/ingest",
            data={"user_id": "alice"},
            files={"file": ("strategy.pptx", b"presentation bytes", "application/vnd.openxmlformats-officedocument.presentationml.presentation")},
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.ingestion_manager.submitted["pdf_path"].suffix, ".pptx")

    def test_pdf_upload_rejects_exact_file_duplicate_before_queueing(self) -> None:
        pdf = fitz.open()
        pdf.new_page()
        pdf_bytes = pdf.tobytes()
        pdf.close()
        file_hash = hashlib.sha256(pdf_bytes).hexdigest()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.app.state.api_settings = APISettings(
                user_data_root=root,
                api_auth_token="test-token",
                api_username="admin",
                api_password="secret",
            )
            artifacts = root / "alice" / "artifacts" / "ingestion"
            artifacts.mkdir(parents=True)
            (artifacts / "chunk_registry.json").write_text(
                json.dumps({
                    "chunks": {},
                    "documents": {},
                    "files": {file_hash: {"document_id": "doc-existing", "source_file": "brief.pdf"}},
                }),
                encoding="utf-8",
            )

            response = self.client.post(
                "/ingest",
                data={"user_id": "alice"},
                files={"file": ("brief-copy.pdf", pdf_bytes, "application/pdf")},
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn("Duplicate document already ingested", response.json()["detail"])
        self.assertIsNone(self.ingestion_manager.submitted)

    def test_pdf_upload_rejects_normalized_content_duplicate_before_queueing(self) -> None:
        pdf = fitz.open()
        page = pdf.new_page()
        page.insert_text((72, 72), "Identical report content with stable wording.")
        pdf_bytes = pdf.tobytes()
        pdf.close()

        from multimodal_rag.ingestion.loaders.pdf_loader import normalized_pdf_text_hash

        # The helper is path-based, so create the same bytes in a temporary
        # file solely to calculate the registry fingerprint used by preflight.
        with tempfile.TemporaryDirectory() as source_directory:
            source_path = Path(source_directory) / "content.pdf"
            source_path.write_bytes(pdf_bytes)
            content_hash = normalized_pdf_text_hash(source_path)

            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.app.state.api_settings = APISettings(
                    user_data_root=root,
                    api_auth_token="test-token",
                    api_username="admin",
                    api_password="secret",
                )
                artifacts = root / "alice" / "artifacts" / "ingestion"
                artifacts.mkdir(parents=True)
                (artifacts / "chunk_registry.json").write_text(
                    json.dumps({
                        "chunks": {},
                        "documents": {},
                        "files": {},
                        "content": {content_hash: {"document_id": "doc-existing", "source_file": "original.pdf"}},
                    }),
                    encoding="utf-8",
                )

                response = self.client.post(
                    "/ingest",
                    data={"user_id": "alice"},
                    files={"file": ("renamed-copy.pdf", pdf_bytes, "application/pdf")},
                )

        self.assertEqual(response.status_code, 409)
        self.assertIn("Duplicate document content already ingested", response.json()["detail"])
        self.assertIsNone(self.ingestion_manager.submitted)

    def test_ingestion_status_returns_job_snapshot(self) -> None:
        self.ingestion_manager.jobs["job-1"] = {
            "job_id": "job-1", "filename": "brief.pdf", "user_id": "alice", "project_id": None,
            "status": "completed", "stage": "complete", "document_id": "doc-1",
            "chunk_count": 12, "embedded_count": 12, "error": None,
        }

        response = self.client.get("/ingest/job-1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["embedded_count"], 12)

    def test_retrieve_returns_metadata_and_forwards_scope(self) -> None:
        response = self.client.post("/retrieve", json={"question": "What changed?", "user_id": "alice", "top_k": 4})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["chunks"][0]["metadata"]["chunk_id"], "chunk-1")
        self.assertTrue(response.headers["X-Trace-ID"])
        self.assertEqual(self.service.calls, [("retrieve", {"question": "What changed?", "user_id": "alice", "top_k": 4})])

    def test_project_id_is_forwarded_to_service(self) -> None:
        response = self.client.post(
            "/retrieve",
            json={
                "question": "What changed?",
                "user_id": "alice",
                "project_id": "launch-2026",
                "top_k": 4,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.service.calls,
            [
                (
                    "retrieve",
                    {
                        "question": "What changed?",
                        "user_id": "alice",
                        "project_id": "launch-2026",
                        "top_k": 4,
                    },
                )
            ],
        )

    def test_project_id_is_forwarded_for_answer(self) -> None:
        response = self.client.post(
            "/answer",
            json={
                "question": "Summarize",
                "user_id": "alice",
                "project_id": "launch-2026",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.service.calls,
            [
                (
                    "answer",
                    {
                        "question": "Summarize",
                        "user_id": "alice",
                        "project_id": "launch-2026",
                        "top_k": 8,
                    },
                )
            ],
        )

    def test_answer_returns_trace_id_and_sources(self) -> None:
        response = self.client.post("/answer", json={"question": "Summarize", "user_id": "alice"})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["answer"], "Grounded marketing answer.")
        self.assertEqual(body["sources"][0]["chunk_id"], "chunk-1")
        self.assertTrue(body["trace_id"])
        self.assertEqual(response.headers["X-Trace-ID"], body["trace_id"])
        self.assertEqual(body["rag_trace"]["question"], "Summarize")
        self.assertEqual(body["rag_trace"]["actual_retrieved_count"], 1)
        self.assertEqual(self.memory_service.messages, [("alice", None, "Summarize")])

    def test_exact_repeat_replays_stored_answer_without_calling_the_service(self) -> None:
        store = FakeUserStore()
        app = create_app(
            settings=APISettings(user_data_root=self.tenant_data_root),
            rag_service=self.service,
            memory_service=self.memory_service,
            user_store=store,
        )
        client = TestClient(app)
        signup = client.post("/auth/signup", json={"username": "alice2", "password": "password-1"})
        credentials = {"Authorization": f"Bearer {signup.json()['access_token']}"}
        user_id = signup.json()["user_id"]

        first = client.post("/answer", headers=credentials, json={"question": "Summarize", "user_id": user_id, "chat_id": "chat-1"})
        second = client.post("/answer", headers=credentials, json={"question": "  summarize  ", "user_id": user_id, "chat_id": "chat-1"})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(self.service.calls), 1)  # the service was never called a second time
        self.assertEqual(second.json()["answer"], first.json()["answer"])
        self.assertNotEqual(second.json()["trace_id"], first.json()["trace_id"])
        self.assertIsNone(first.json()["reused_from_message_id"])
        self.assertIsNotNone(second.json()["reused_from_message_id"])
        self.assertTrue(second.json()["rag_trace"].get("reused"))

    def test_follow_up_passes_conversation_history_to_the_service(self) -> None:
        store = FakeUserStore()
        classifier = FakeReuseClassifier(ReuseDecision(relation="follow_up", prior_turn=1, research_queries=["marketing details"]))
        app = create_app(
            settings=APISettings(user_data_root=self.tenant_data_root),
            rag_service=self.service,
            memory_service=self.memory_service,
            user_store=store,
            reuse_classifier=classifier,
        )
        client = TestClient(app)
        signup = client.post("/auth/signup", json={"username": "alice3", "password": "password-1"})
        credentials = {"Authorization": f"Bearer {signup.json()['access_token']}"}
        user_id = signup.json()["user_id"]

        client.post("/answer", headers=credentials, json={"question": "Summarize", "user_id": user_id, "chat_id": "chat-1"})
        client.post("/answer", headers=credentials, json={"question": "Tell me more about that", "user_id": user_id, "chat_id": "chat-1"})

        self.assertEqual(len(self.service.calls), 2)
        second_call_kwargs = self.service.calls[1][1]
        self.assertIn("conversation_history", second_call_kwargs)
        self.assertEqual(second_call_kwargs["conversation_history"][0].user_query, "Summarize")
        self.assertEqual(second_call_kwargs["retrieval_question"], "marketing details")
        self.assertEqual(len(classifier.calls), 1)

    def test_missing_get_recent_exchanges_degrades_to_a_fresh_answer(self) -> None:
        class MinimalUserStore:
            def initialize(self) -> None:
                pass

            def create_user(self, username, password):
                return "user-minimal"

            def authenticate(self, username, password):
                return "user-minimal"

            def create_session(self, user_id):
                return "token-minimal"

            def user_for_token(self, token):
                return "user-minimal"

            def append_message(self, user_id, chat_id, role, payload) -> None:
                return None
            # Deliberately no get_recent_exchanges - exercises the getattr fallback.

        app = create_app(
            settings=APISettings(user_data_root=self.tenant_data_root),
            rag_service=self.service,
            memory_service=self.memory_service,
            user_store=MinimalUserStore(),
        )
        client = TestClient(app)
        signup = client.post("/auth/signup", json={"username": "alice4", "password": "password-1"})
        credentials = {"Authorization": f"Bearer {signup.json()['access_token']}"}

        response = client.post("/answer", headers=credentials, json={"question": "Summarize", "user_id": "user-minimal", "chat_id": "chat-1"})

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["reused_from_message_id"])

    def test_invalid_user_id_is_rejected_before_service(self) -> None:
        response = self.client.post("/retrieve", json={"question": "x", "user_id": "../other"})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.service.calls, [])

    def test_invalid_project_id_is_rejected_before_service(self) -> None:
        response = self.client.post(
            "/answer",
            json={"question": "x", "user_id": "alice", "project_id": "../other"},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.service.calls, [])

    def test_invalid_question_and_top_k_are_rejected(self) -> None:
        blank_question = self.client.post("/retrieve", json={"question": "  ", "user_id": "alice"})
        invalid_top_k = self.client.post(
            "/answer",
            json={"question": "x", "user_id": "alice", "top_k": 0},
        )

        self.assertEqual(blank_question.status_code, 422)
        self.assertEqual(invalid_top_k.status_code, 422)
        self.assertEqual(self.service.calls, [])

    def test_missing_user_index_is_not_found(self) -> None:
        response = self.client.post("/retrieve", json={"question": "x", "user_id": "missing"})

        self.assertEqual(response.status_code, 404)

    def test_scope_paths_are_isolated_and_project_ready(self) -> None:
        settings = APISettings(user_data_root=self.tenant_data_root)

        scope = settings.scope_for("alice", project_id="launch-2026")
        self.assertEqual(
            scope.index_dir,
            self.tenant_data_root / "alice" / "projects" / "launch-2026" / "artifacts" / "index",
        )
        self.assertEqual(
            settings.scope_for("bob").ingestion_artifacts_dir,
            self.tenant_data_root / "bob" / "artifacts" / "ingestion",
        )

    def test_fork_chat_endpoint_creates_branch(self) -> None:
        store = FakeUserStore()
        app = create_app(
            settings=APISettings(user_data_root=self.tenant_data_root),
            rag_service=self.service,
            memory_service=self.memory_service,
            user_store=store,
        )
        client = TestClient(app)
        signup = client.post("/auth/signup", json={"username": "alice-branch", "password": "password-1"})
        token = signup.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        user_id = signup.json()["user_id"]

        # Add initial message to create parent chat
        store.append_message(user_id, "chat-parent", "user", {"question": "Footwear market analysis"})
        
        # Fork chat
        fork_response = client.post("/chats/chat-parent/fork", headers=headers)
        self.assertEqual(fork_response.status_code, 201)
        branched = fork_response.json()
        self.assertTrue(branched["id"])
        self.assertNotEqual(branched["id"], "chat-parent")
        self.assertIn("Branch", branched["title"])


if __name__ == "__main__":
    unittest.main()

