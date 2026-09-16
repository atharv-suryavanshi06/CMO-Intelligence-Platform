"""FastAPI routes; RAG work stays in :mod:`multimodal_rag.api.service`."""

from __future__ import annotations

import logging
import hashlib
import secrets
import time
import uuid
from pathlib import Path

import fitz
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from multimodal_rag.agents.models import (
    AgentRequest,
    MarketIntelligenceResponse,
    MarketStrategyRequest,
    MarketStrategyResponse,
    MeetingFollowUpRequest,
    MeetingFollowUpResponse,
    MeetingPreparationRequest,
    MeetingPreparationResponse,
)
from multimodal_rag.agents.rag_client import RAGIndexNotFoundError
from multimodal_rag.api.schemas import (
    DocumentExtractResponse,
    AnswerResponse,
    ChatResponse,
    ChatSummaryResponse,
    CompanyResearchRequest,
    CompanyResearchResponse,
    CompanyResearchSource,
    IngestionJobResponse,
    LoginRequest,
    QuestionRequest,
    RetrieveResponse,
    PresentationGenerateRequest,
    PresentationTaskResponse,
    PresentationTemplateResponse,
    TokenResponse,
)
from multimodal_rag.api.accounts import PostgresUserStore
from multimodal_rag.api.presentations import (
    InvalidPresentationTemplateError,
    NoMeetingPreparationError,
    PresentationArtifactError,
    PresentationConfigurationError,
    PresentationGenerationService,
    PresentationProviderError,
    PresentationTimeoutError,
)
from multimodal_rag.api.conversation_reuse import (
    PriorExchange,
    eligible_exchanges,
    exact_repeat,
    format_exchanges,
    to_conversation_turns,
)
from multimodal_rag.api.service import RAGService, UserIndexNotFoundError
from multimodal_rag.ingestion.formats import (
    ACTIVE_INGESTION_EXTENSIONS,
    SUPPORTED_INGESTION_EXTENSIONS,
    SUPPORTED_INGESTION_FORMAT_LABEL,
    file_extension,
)
from multimodal_rag.ingestion.loaders.pdf_loader import MAX_PAGE_COUNT, normalized_pdf_text_hash
from multimodal_rag.ingestion.output.deduplicator import find_content_duplicate, find_file_duplicate
from multimodal_rag.ingestion.pipeline.orchestrator import IngestionError
from multimodal_rag.memory import MemoryCandidate, MemoryService
from multimodal_rag.rag.generation.answer_generator import (
    AnswerGenerationError,
    AnswerGenerationUnavailableError,
)
from multimodal_rag.web_search import CompanyResearchTarget, WebSearchConfigurationError, WebSearchProviderError
from multimodal_rag.rag.observability import annotate_current_span, observed

logger = logging.getLogger(__name__)
bearer_scheme = HTTPBearer(auto_error=False)
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def require_api_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> None:
    """Authenticate a persistent session, retaining a legacy deployment fallback."""
    store: PostgresUserStore | None = request.app.state.user_store
    if store is not None:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.", headers={"WWW-Authenticate": "Bearer"})
        user_id = store.user_for_token(credentials.credentials)
        if user_id is None:
            logger.warning("Rejected invalid user session")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.", headers={"WWW-Authenticate": "Bearer"})
        request.state.authenticated_user_id = user_id
        return

    expected_token = request.app.state.api_auth_token
    if not expected_token:
        logger.error("RAG API authentication is not configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API authentication is not configured.",
        )

    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not secrets.compare_digest(credentials.credentials, expected_token)
    ):
        logger.warning("Rejected unauthenticated API request")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )


auth_router = APIRouter()
router = APIRouter(dependencies=[Depends(require_api_token)])


@auth_router.post("/auth/login", response_model=TokenResponse, response_model_exclude_none=True)
def login(payload: LoginRequest, request: Request) -> TokenResponse:
    """Authenticate a persistent account and issue a user-bound session token."""
    store: PostgresUserStore | None = request.app.state.user_store
    if store is not None:
        user_id = store.authenticate(payload.username, payload.password)
        if user_id is None:
            logger.warning("Rejected API login attempt")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password.")
        return TokenResponse(access_token=store.create_session(user_id), user_id=user_id)

    configured_username = request.app.state.api_username
    configured_password = request.app.state.api_password
    configured_token = request.app.state.api_auth_token
    if not configured_username or not configured_password or not configured_token:
        logger.error("RAG API login is not configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API login is not configured.",
        )

    valid_username = secrets.compare_digest(payload.username, configured_username)
    valid_password = secrets.compare_digest(payload.password, configured_password)
    if not (valid_username and valid_password):
        logger.warning("Rejected API login attempt")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )

    return TokenResponse(access_token=configured_token)


@auth_router.post("/auth/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED, response_model_exclude_none=True)
def signup(payload: LoginRequest, request: Request) -> TokenResponse:
    """Create a persistent user account and sign it in immediately."""
    store: PostgresUserStore | None = request.app.state.user_store
    if store is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Persistent account storage is not configured.")
    user_id = store.create_user(payload.username, payload.password)
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="That username is already in use.")
    return TokenResponse(access_token=store.create_session(user_id), user_id=user_id)


def _authenticated_user_id(request: Request, requested_user_id: str) -> str:
    """Reject client-supplied tenant IDs that do not match the signed-in account."""
    authenticated = getattr(request.state, "authenticated_user_id", None)
    if authenticated is not None and not secrets.compare_digest(authenticated, requested_user_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This request is outside the signed-in user's workspace.")
    return requested_user_id


def _chat_store(request: Request) -> PostgresUserStore | None:
    return request.app.state.user_store


def _record_chat_message(request: Request, user_id: str, chat_id: str | None, role: str, payload: dict) -> None:
    if chat_id is None or _chat_store(request) is None:
        return
    _chat_store(request).append_message(user_id, chat_id, role, payload)


@auth_router.get("/chats", response_model=list[ChatSummaryResponse], dependencies=[Depends(require_api_token)])
def list_chats(request: Request) -> list[ChatSummaryResponse]:
    user_id = getattr(request.state, "authenticated_user_id", None)
    store = _chat_store(request)
    if user_id is None or store is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Persistent chat storage is not configured.")
    return [ChatSummaryResponse(**{**chat, "created_at": chat["created_at"].isoformat(), "updated_at": chat["updated_at"].isoformat()}) for chat in store.list_chats(user_id)]


@auth_router.get("/chats/{chat_id}", response_model=ChatResponse, dependencies=[Depends(require_api_token)])
def get_chat(chat_id: str, request: Request) -> ChatResponse:
    user_id = getattr(request.state, "authenticated_user_id", None)
    store = _chat_store(request)
    if user_id is None or store is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Persistent chat storage is not configured.")
    chat = store.get_chat(user_id, chat_id)
    if chat is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
    return ChatResponse(**{
        **chat,
        "created_at": chat["created_at"].isoformat(),
        "updated_at": chat["updated_at"].isoformat(),
        "messages": [{**message, "created_at": message["created_at"].isoformat()} for message in chat["messages"]],
    })


@auth_router.delete("/chats/{chat_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_api_token)])
def delete_chat(chat_id: str, request: Request) -> Response:
    """Permanently delete a chat and all of its messages and strategy state."""
    user_id = getattr(request.state, "authenticated_user_id", None)
    store = _chat_store(request)
    if user_id is None or store is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Persistent chat storage is not configured.")
    if not store.delete_chat(user_id, chat_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@auth_router.post("/chats/{chat_id}/fork", response_model=ChatSummaryResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_api_token)])
def fork_chat(chat_id: str, request: Request) -> ChatSummaryResponse:
    """Create a new chat seeded with the context and title of an existing chat (Option 1 branching)."""
    user_id = getattr(request.state, "authenticated_user_id", None)
    store = _chat_store(request)
    if user_id is None or store is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Persistent chat storage is not configured.")
    source_chat = store.get_chat(user_id, chat_id)
    if source_chat is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
    new_chat_id = str(uuid.uuid4())
    new_title = f"{source_chat['title']} (Branch)"[:120]
    if hasattr(store, "create_chat"):
        created = store.create_chat(user_id, new_chat_id, new_title)
    else:
        store.append_message(user_id, new_chat_id, "user", {"question": new_title})
        created = store.get_chat(user_id, new_chat_id) or {"created_at": datetime.now(UTC), "updated_at": datetime.now(UTC), "messages": []}
    mem_service = _memory_service(request)
    if mem_service and hasattr(mem_service, "clone_context"):
        mem_service.clone_context(user_id, chat_id, new_chat_id)
    created_at = created["created_at"]
    updated_at = created["updated_at"]
    return ChatSummaryResponse(
        id=new_chat_id,
        title=new_title,
        created_at=created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
        updated_at=updated_at.isoformat() if hasattr(updated_at, "isoformat") else str(updated_at),
        message_count=created.get("message_count", len(created.get("messages", []))),
    )


def _presentation_failure(exc: Exception) -> HTTPException:
    if isinstance(exc, NoMeetingPreparationError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, InvalidPresentationTemplateError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    if isinstance(exc, PresentationConfigurationError):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    if isinstance(exc, PresentationTimeoutError):
        return HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc))
    if isinstance(exc, PresentationProviderError | PresentationArtifactError):
        return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Presentation generation failed.")


@router.get("/presentations/templates", response_model=list[PresentationTemplateResponse])
def list_presentation_templates(request: Request) -> list[PresentationTemplateResponse]:
    """Return the stable template IDs exposed by the configured Presenton server."""
    try:
        return [
            PresentationTemplateResponse(
                id=template.id,
                name=template.name,
                total_layouts=template.total_layouts,
                preview_url=template.preview_url,
            )
            for template in _presentation_service(request).list_templates()
        ]
    except Exception as exc:
        raise _presentation_failure(exc) from exc


@router.post("/presentations/generate", response_model=PresentationTaskResponse)
def generate_presentation(payload: PresentationGenerateRequest, request: Request) -> PresentationTaskResponse:
    """Start a PPTX task exclusively from the authenticated user's current chat."""
    user_id = getattr(request.state, "authenticated_user_id", None)
    store = _chat_store(request)
    if user_id is None or store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Persistent chat storage is required to create a presentation.",
        )
    chat = store.get_chat(user_id, payload.chat_id)
    if chat is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
    try:
        task = _presentation_service(request).start_from_chat(
            chat,
            template_id=payload.template_id,
            slide_count=payload.slide_count,
        )
    except Exception as exc:
        raise _presentation_failure(exc) from exc
    return PresentationTaskResponse(id=task.id, status=task.status, message=task.message)


@router.get("/presentations/generate/{task_id}", response_model=PresentationTaskResponse)
def presentation_status(task_id: str, request: Request) -> PresentationTaskResponse:
    try:
        task = _presentation_service(request).task_status(task_id)
        return PresentationTaskResponse(id=task.id, status=task.status, message=task.message)
    except Exception as exc:
        raise _presentation_failure(exc) from exc


@router.get("/presentations/generate/{task_id}/download")
def download_presentation(task_id: str, request: Request) -> Response:
    try:
        generated = _presentation_service(request).download_task(task_id)
    except Exception as exc:
        raise _presentation_failure(exc) from exc
    return Response(
        content=generated.content,
        media_type=generated.media_type,
        headers={"Content-Disposition": f'attachment; filename="{generated.filename}"'},
    )


@router.get("/presentations/generate/{task_id}/preview")
def preview_presentation(task_id: str, request: Request) -> Response:
    try:
        content = _presentation_service(request).preview_task(task_id)
    except Exception as exc:
        raise _presentation_failure(exc) from exc
    return Response(content=content, media_type="application/pdf", headers={"Content-Disposition": 'inline; filename="presentation-preview.pdf"'})


def _service(request: Request) -> RAGService:
    return request.app.state.rag_service


def _market_intelligence_agent(request: Request):
    return request.app.state.market_intelligence_agent


def _market_strategy_agent(request: Request):
    return request.app.state.market_strategy_agent


def _meeting_preparation_agent(request: Request):
    return request.app.state.meeting_preparation_agent


def _presentation_service(request: Request) -> PresentationGenerationService:
    return request.app.state.presentation_service


def _company_research_client(request: Request):
    return request.app.state.company_research_client


def _log_latency_report(trace_id: str, agent_name: str, user_id: str, latency: dict[str, float], total_ms: float) -> None:
    """Print the full per-question latency breakdown once the agent pipeline finishes.

    Web search and Source Guard checks now run concurrently (see
    MarketIntelligenceAgent._search_all / SourceGuardService.filter_results),
    so a plain sum across N parallel calls overstates the time actually added
    to the request. Both the summed total (useful for spotting one very slow
    call) and the measured wall-clock (what the user actually waited for) are
    reported side by side. ``Unaccounted`` surfaces whatever isn't covered by
    any measured phase (Postgres/chat-store I/O, the embedding call, etc.).
    """
    web_search_wall_ms = latency.get("web_search_wall_ms", 0.0)
    virustotal_wall_ms = latency.get("virustotal_wall_ms", 0.0)
    lakera_guard_wall_ms = latency.get("lakera_guard_wall_ms", 0.0)
    rag_ms = latency.get("rag_ms", 0.0)
    intelligence_generation_ms = latency.get("intelligence_generation_ms", 0.0)
    strategy_generation_ms = latency.get("strategy_generation_ms", 0.0)
    meeting_prep_ms = latency.get("meeting_prep_ms", 0.0)
    measured_wall_ms = rag_ms + web_search_wall_ms + virustotal_wall_ms + lakera_guard_wall_ms + intelligence_generation_ms + strategy_generation_ms + meeting_prep_ms
    unaccounted_ms = max(0.0, total_ms - measured_wall_ms)

    logger.info(
        "\n"
        "===== Latency Report trace_id=%s agent=%s user_id=%s =====\n"
        "  RAG retrieval           : %8.1f ms\n"
        "  Web search (Tavily)     : %8.1f ms summed / %8.1f ms wall\n"
        "  VirusTotal              : %8.1f ms summed / %8.1f ms wall  (lookups=%d cache_hits=%d rate_limited=%d)\n"
        "  Lakera Guard            : %8.1f ms summed / %8.1f ms wall\n"
        "  Intelligence generation : %8.1f ms\n"
        "  Strategy generation     : %8.1f ms\n"
        "  Meeting prep generation : %8.1f ms\n"
        "  -----------------------------------------------------------\n"
        "  Unaccounted             : %8.1f ms\n"
        "  Total request time      : %8.1f ms\n"
        "=============================================================",
        trace_id,
        agent_name,
        user_id,
        rag_ms,
        latency.get("web_search_ms", 0.0),
        web_search_wall_ms,
        latency.get("virustotal_ms", 0.0),
        virustotal_wall_ms,
        int(latency.get("virustotal_lookups", 0.0)),
        int(latency.get("virustotal_cache_hits", 0.0)),
        int(latency.get("virustotal_rate_limited", 0.0)),
        latency.get("lakera_guard_ms", 0.0),
        lakera_guard_wall_ms,
        intelligence_generation_ms,
        strategy_generation_ms,
        meeting_prep_ms,
        unaccounted_ms,
        total_ms,
    )
    annotate_current_span(metadata={
        "trace_id": trace_id,
        "agent": agent_name,
        "total_latency_ms": total_ms,
        "latency": latency,
    }, tags=[f"agent:{agent_name}"])


def _memory_service(request: Request) -> MemoryService:
    return request.app.state.memory_service


def _remember_message(request: Request, user_id: str, chat_id: str | None, message: str) -> None:
    """Persist useful context without allowing memory failures to fail the answer route."""
    try:
        _memory_service(request).process_message(user_id, chat_id, message)
    except Exception:
        logger.exception("Chat memory processing failed for user_id=%s chat_id=%s", user_id, chat_id)


def _recent_exchanges(request: Request, user_id: str, chat_id: str | None, limit: int = 10) -> list[dict]:
    """Fetch the last `limit` (question, assistant-payload) pairs for a chat.

    Degrades to an empty list (no reuse detection) when there is no chat_id
    or the configured store predates this capability - the same duck-typed
    pattern as `_load_strategy_state` below.
    """
    reader = getattr(_chat_store(request), "get_recent_exchanges", None)
    if chat_id is None or reader is None:
        return []
    return reader(user_id, chat_id, limit)


def _meeting_briefing_context(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep the prior briefing focused and bounded when routing a follow-up."""
    keys = (
        "meeting_title",
        "meeting_objective",
        "attendee_context",
        "product",
        "industry",
        "geography",
        "budget",
        "key_competitors",
        "timeline",
        "executive_brief",
        "key_facts_to_remember",
        "strategic_talking_points",
        "questions_to_ask",
        "risks_to_watch",
        "recommended_actions",
        "relevant_market_trends",
        "relevant_competitor_intelligence",
    )
    return {key: payload[key] for key in keys if key in payload}


def _reuse_classifier(request: Request):
    return getattr(request.app.state, "conversation_reuse_classifier", None)


def _load_strategy_state(request: Request, user_id: str, chat_id: str | None) -> dict | None:
    store = _chat_store(request)
    loader = getattr(store, "get_strategy_state", None)
    if chat_id is None or loader is None:
        return None
    return loader(user_id, chat_id)


def _save_strategy_state(request: Request, user_id: str, chat_id: str, state: dict) -> None:
    saver = getattr(_chat_store(request), "set_strategy_state", None)
    if saver is None:
        raise RuntimeError("Persistent strategy continuation storage is not configured.")
    saver(user_id, chat_id, state)


def _clear_strategy_state(request: Request, user_id: str, chat_id: str | None) -> None:
    clearer = getattr(_chat_store(request), "clear_strategy_state", None)
    if chat_id is not None and clearer is not None:
        clearer(user_id, chat_id)


def _strategy_research_request(
    request: MarketStrategyRequest,
    user_context: dict,
) -> AgentRequest:
    """Build the MI request without accepting temporary personalization fields."""
    additional_context = {"stored_user_context": user_context}
    if request.meeting_context:
        additional_context["meeting_preparation_context"] = request.meeting_context
    return AgentRequest(
        task_id=request.task_id,
        objective=request.objective,
        user_id=request.user_id,
        project_id=request.project_id,
        time_range=request.time_range,
        top_k=request.top_k,
        chat_id=request.chat_id,
        document_context=request.document_context,
        additional_context=additional_context,
    )


@router.post("/ingest", response_model=IngestionJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def ingest_pdf(
    request: Request,
    file: UploadFile = File(...),
    user_id: str = Form(...),
    project_id: str | None = Form(None),
) -> IngestionJobResponse:
    """Validate an active PDF, media, or Word upload and queue its ingestion job."""
    filename = Path(file.filename or "").name
    extension = file_extension(filename)
    if not filename or extension not in SUPPORTED_INGESTION_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file format. Allowed formats: {SUPPORTED_INGESTION_FORMAT_LABEL}.",
        )
    if extension not in ACTIVE_INGESTION_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=f"{extension.upper().lstrip('.')} is an allowed format, but its ingestion pipeline is not available yet.",
        )
    allowed_content_types = {
        ".pdf": {"application/pdf", "application/octet-stream"},
        ".mp3": {"audio/mpeg", "audio/mp3", "application/octet-stream"},
        ".mp4": {"video/mp4", "application/octet-stream"},
        ".doc": {"application/msword", "application/octet-stream"},
        ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/octet-stream"},
        ".ppt": {"application/vnd.ms-powerpoint", "application/octet-stream"},
        ".pptx": {"application/vnd.openxmlformats-officedocument.presentationml.presentation", "application/octet-stream"},
    }
    if file.content_type and file.content_type not in allowed_content_types.get(extension, set()):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"The uploaded file does not match its {extension.upper().lstrip('.')} format.")

    normalized_user_id = _authenticated_user_id(request, user_id.strip())
    normalized_project_id = project_id.strip() if project_id and project_id.strip() else None
    try:
        scope = request.app.state.api_settings.scope_for(normalized_user_id, normalized_project_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    job_id = uuid.uuid4().hex
    upload_dir = scope.root / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    upload_path = upload_dir / f"{job_id}{extension}"
    size = 0
    first_bytes = b""
    file_digest = hashlib.sha256()
    try:
        with upload_path.open("wb") as destination:
            while True:
                block = await file.read(1024 * 1024)
                if not block:
                    break
                if not first_bytes:
                    first_bytes = block[:5]
                size += len(block)
                file_digest.update(block)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Uploads must be smaller than 50 MB.")
                destination.write(block)
        if size == 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty files are not accepted.")
        if extension == ".pdf":
            if first_bytes != b"%PDF-":
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The uploaded file is not a valid PDF.")
            try:
                with fitz.open(str(upload_path)) as pdf:
                    page_count = pdf.page_count
            except Exception as exc:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The uploaded file is not a readable PDF.") from exc
            if page_count < 1:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="PDFs must contain at least one page.")
            if page_count > MAX_PAGE_COUNT:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"PDFs must contain {MAX_PAGE_COUNT} pages or fewer; this file has {page_count} pages.",
                )
        source_sha256 = file_digest.hexdigest()
        existing_file = find_file_duplicate(scope.ingestion_artifacts_dir, source_sha256)
        if existing_file:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Duplicate document already ingested as {existing_file.get('document_id', 'an existing document')}; upload skipped.",
            )
        content_sha256 = normalized_pdf_text_hash(upload_path) if extension == ".pdf" else None
        if content_sha256:
            existing_content = find_content_duplicate(scope.ingestion_artifacts_dir, content_sha256)
            if existing_content:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Duplicate document content already ingested as {existing_content.get('document_id', 'an existing document')}; upload skipped.",
                )
        record = request.app.state.ingestion_manager.submit(
            pdf_path=upload_path,
            scope=scope,
            filename=filename,
            job_id=job_id,
            source_sha256=source_sha256,
            content_sha256=content_sha256,
        )
        return IngestionJobResponse(**record)
    except HTTPException:
        upload_path.unlink(missing_ok=True)
        raise
    except IngestionError as exc:
        upload_path.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except OSError as exc:
        upload_path.unlink(missing_ok=True)
        logger.exception("Could not save upload for user_id=%s", normalized_user_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Could not save the uploaded file.") from exc
    finally:
        await file.close()


@router.get("/ingest/{job_id}", response_model=IngestionJobResponse)
def ingestion_status(job_id: str, request: Request) -> IngestionJobResponse:
    """Return the current stage and result for an upload job."""
    record = request.app.state.ingestion_manager.get(job_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ingestion job not found.")
    return IngestionJobResponse(**record)


@router.post("/retrieve", response_model=RetrieveResponse)
@observed("api.retrieve")
def retrieve_documents(
    payload: QuestionRequest,
    request: Request,
    response: Response,
) -> RetrieveResponse:
    _authenticated_user_id(request, payload.user_id)
    trace_id = str(uuid.uuid4())
    annotate_current_span(metadata={"trace_id": trace_id})
    response.headers["X-Trace-ID"] = trace_id
    logger.info(
        "Retrieval request trace_id=%s user_id=%s top_k=%s",
        trace_id,
        payload.user_id,
        payload.top_k,
    )
    try:
        result = _service(request).retrieve(**payload.model_dump(exclude_none=True))
        logger.info(
            "Retrieval completed trace_id=%s user_id=%s chunks=%s",
            trace_id,
            payload.user_id,
            len(result.chunks),
        )
        return RetrieveResponse(chunks=result.chunks)
    except UserIndexNotFoundError as exc:
        logger.warning("Retrieval index missing trace_id=%s user_id=%s", trace_id, payload.user_id)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        logger.warning("Invalid retrieval request trace_id=%s user_id=%s", trace_id, payload.user_id)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Retrieval failed trace_id=%s user_id=%s", trace_id, payload.user_id)
        raise HTTPException(status_code=500, detail="Retrieval failed.") from exc


@router.post("/agents/market-intelligence", response_model=MarketIntelligenceResponse)
@observed("api.market_intelligence")
def analyze_market_intelligence(
    payload: AgentRequest,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
) -> MarketIntelligenceResponse:
    """Run combined RAG and fresh-web market intelligence analysis."""
    trace_id = str(uuid.uuid4())
    annotate_current_span(metadata={"trace_id": trace_id})
    response.headers["X-Trace-ID"] = trace_id
    logger.info(
        "Market intelligence request trace_id=%s task_id=%s user_id=%s project_id=%s",
        trace_id,
        payload.task_id,
        payload.user_id,
        payload.project_id,
    )
    _authenticated_user_id(request, payload.user_id)
    try:
        # Extraction is an LLM call; deferring it past the response means a
        # fact stated in this message is usable from the next turn rather
        # than this one, in exchange for not paying its latency here.
        background_tasks.add_task(_remember_message, request, payload.user_id, payload.chat_id, payload.objective)
        _record_chat_message(request, payload.user_id, payload.chat_id, "user", {"question": payload.objective})

        exchanges = eligible_exchanges(
            _recent_exchanges(request, payload.user_id, payload.chat_id), agent="market_intelligence"
        )
        hit = exact_repeat(payload.objective, exchanges) if exchanges else None
        if hit is not None:
            logger.info("Market intelligence replayed from message_id=%s trace_id=%s user_id=%s", hit.message_id, trace_id, payload.user_id)
            replay = MarketIntelligenceResponse.model_validate({k: v for k, v in hit.payload.items() if k != "agent"})
            replay = replay.model_copy(update={
                "trace_id": trace_id,
                "chat_id": payload.chat_id,
                "reused_from_message_id": hit.message_id,
            })
            _record_chat_message(request, payload.user_id, payload.chat_id, "assistant", {"agent": "market_intelligence", **replay.model_dump(mode="json")})
            return replay

        if payload.document_context:
            payload = payload.model_copy(update={"additional_context": {
                **payload.additional_context,
                "document_context": payload.document_context,
            }})
        if exchanges:
            payload = payload.model_copy(update={"additional_context": {
                **payload.additional_context,
                "prior_exchanges": format_exchanges(exchanges),
                "prior_sources": (exchanges[-1].payload.get("sources") or [])[:20],
            }})

        request_start = time.perf_counter()
        latency: dict[str, float] = {}
        result = _market_intelligence_agent(request).run(payload, trace_id=trace_id, latency=latency)
        _log_latency_report(trace_id, "market_intelligence", payload.user_id, latency, (time.perf_counter() - request_start) * 1000)
        if result.error_code == "index_not_found":
            response.status_code = status.HTTP_404_NOT_FOUND
        elif result.error_code == "generation_unavailable":
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        elif result.error_code in {"retrieval_error", "generation_error"}:
            response.status_code = status.HTTP_502_BAD_GATEWAY
        result = result.model_copy(update={"chat_id": payload.chat_id})
        _record_chat_message(request, payload.user_id, payload.chat_id, "assistant", {"agent": "market_intelligence", **result.model_dump(mode="json")})
        return result
    except RAGIndexNotFoundError as exc:
        logger.warning("Market intelligence index missing trace_id=%s user_id=%s", trace_id, payload.user_id)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Market intelligence request failed trace_id=%s task_id=%s", trace_id, payload.task_id)
        raise HTTPException(status_code=500, detail="Market intelligence analysis failed.") from exc


@router.post("/agents/market-strategy", response_model=MarketStrategyResponse)
@observed("api.market_strategy")
def create_market_strategy(
    payload: MarketStrategyRequest,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
) -> MarketStrategyResponse:
    """Return one critical clarification or an evidence-linked CMO strategy."""
    trace_id = str(uuid.uuid4())
    annotate_current_span(metadata={"trace_id": trace_id})
    response.headers["X-Trace-ID"] = trace_id
    _authenticated_user_id(request, payload.user_id)
    request_start = time.perf_counter()
    latency: dict[str, float] = {}
    try:
        pending_state = _load_strategy_state(request, payload.user_id, payload.chat_id)
        if pending_state:
            missing_items = pending_state.get("missing_context") or []
            # When answering a clarification question, process the memory
            # synchronously so that the answered detail is immediately present in
            # user_context and PostgreSQL before readiness assessment runs.
            _remember_message(request, payload.user_id, payload.chat_id, payload.objective)

            mem_service = _memory_service(request)
            clean_val = payload.objective.strip()
            for missing_key in missing_items:
                clean_key = missing_key.lower().replace(" ", "_")
                mem_type = "market" if any(w in clean_key for w in ("geograph", "market", "region", "countr")) else "target_audience" if "audience" in clean_key else "company_context"
                if mem_service and getattr(mem_service, "enabled", False):
                    repo = getattr(mem_service, "_repository", None)
                    if repo and hasattr(repo, "upsert_memory") and payload.chat_id:
                        try:
                            repo.upsert_memory(
                                payload.user_id,
                                payload.chat_id,
                                MemoryCandidate(memory_type=mem_type, key=clean_key, value=clean_val, confidence=1.0),
                            )
                        except Exception:
                            logger.exception("Direct clarification memory upsert failed for key=%s", clean_key)
        else:
            # Extraction is an LLM call; deferring it past the response means a
            # fact stated in this message is usable from the next turn rather
            # than this one, in exchange for not paying its latency here.
            background_tasks.add_task(_remember_message, request, payload.user_id, payload.chat_id, payload.objective)

        user_context = _memory_service(request).get_user_context(payload.user_id, payload.chat_id)
        if pending_state:
            missing_items = pending_state.get("missing_context") or []
            clean_val = payload.objective.strip()
            profile = user_context.setdefault("profile", {})
            for missing_key in missing_items:
                clean_key = missing_key.lower().replace(" ", "_")
                profile[clean_key] = clean_val
                profile[missing_key] = clean_val
                if any(w in clean_key for w in ("geograph", "market", "region", "countr")):
                    profile["target_geography"] = clean_val
                    profile["target_market"] = clean_val
                    profile["geography"] = clean_val

        _record_chat_message(request, payload.user_id, payload.chat_id, "user", {"question": payload.objective})

        # Reuse detection is skipped entirely while a clarification is
        # pending: a clarification answer ("India") is not a reuse
        # candidate, and scoring it against the window would mis-bucket it.
        exchanges: list[PriorExchange] = []
        if not pending_state:
            exchanges = eligible_exchanges(
                _recent_exchanges(request, payload.user_id, payload.chat_id), agent="market_strategy"
            )
            hit = exact_repeat(payload.objective, exchanges) if exchanges else None
            if hit is not None:
                logger.info("Market strategy replayed from message_id=%s trace_id=%s user_id=%s", hit.message_id, trace_id, payload.user_id)
                replay = MarketStrategyResponse.model_validate({k: v for k, v in hit.payload.items() if k != "agent"})
                replay = replay.model_copy(update={
                    "trace_id": trace_id,
                    "chat_id": payload.chat_id,
                    "reused_from_message_id": hit.message_id,
                })
                _record_chat_message(request, payload.user_id, payload.chat_id, "assistant", {"agent": "market_strategy", **replay.model_dump(mode="json")})
                return replay

        classify_message = getattr(_market_strategy_agent(request), "classify_message", None)
        if pending_state:
            clarif_q = pending_state.get("clarification_question") or (pending_state.get("missing_context") or ["Strategy clarification"])[0]
            prior_exchanges_text = f"Clarification requested: {clarif_q}"
        else:
            prior_exchanges_text = format_exchanges(exchanges) if exchanges else None
        relevance = classify_message(payload.objective, prior_exchanges=prior_exchanges_text) if classify_message is not None else None
        if relevance is not None and not relevance.on_topic:
            # Check the LITERAL new message before touching any pending
            # clarification state. A pending state (if any) is left exactly
            # as it was - an off-topic aside should not be consumed as if it
            # answered the clarification, and should not discard a strategy
            # request the user may still want to finish.
            if relevance.is_greeting:
                executive_summary = "Hello! I'm your Market Strategy assistant. I can help turn market research into a concrete marketing strategy - how can I help you today?"
                error_code = None
            else:
                executive_summary = "This doesn't look like a market or business-strategy question, so I didn't generate a strategy for it. Ask me about your market, competitors, or marketing strategy whenever you're ready."
                error_code = "out_of_scope"
            result = MarketStrategyResponse(
                agent_name="market_strategy",
                status="declined",
                executive_summary=executive_summary,
                task_id=payload.task_id,
                trace_id=trace_id,
                user_id=payload.user_id,
                project_id=payload.project_id,
                error_code=error_code,
                chat_id=payload.chat_id,
            )
            _record_chat_message(request, payload.user_id, payload.chat_id, "assistant", {"agent": "market_strategy", **result.model_dump(mode="json")})
            return result

        strategy_request = payload
        intelligence: MarketIntelligenceResponse | None = None
        if pending_state:
            try:
                strategy_request = MarketStrategyRequest.model_validate(pending_state["request"])
                intelligence = MarketIntelligenceResponse.model_validate(pending_state["intelligence"])
                clean_ans = payload.objective.strip()
                if clean_ans and clean_ans.lower() not in strategy_request.objective.lower():
                    strategy_request = strategy_request.model_copy(update={
                        "objective": f"{strategy_request.objective} (Target/Clarification: {clean_ans})"
                    })
            except (KeyError, TypeError, ValueError):
                logger.warning(
                    "Discarding invalid pending strategy state user_id=%s chat_id=%s",
                    payload.user_id,
                    payload.chat_id,
                )
                _clear_strategy_state(request, payload.user_id, payload.chat_id)

        if intelligence is None:
            if payload.document_context:
                user_context["uploaded_document_page_1"] = payload.document_context
            research_request = _strategy_research_request(strategy_request, user_context)
            if payload.document_context:
                research_request = research_request.model_copy(update={"additional_context": {
                    **research_request.additional_context,
                    "document_context": payload.document_context,
                }})
            if exchanges:
                research_request = research_request.model_copy(update={"additional_context": {
                    **research_request.additional_context,
                    "prior_exchanges": prior_exchanges_text,
                    "prior_sources": (exchanges[-1].payload.get("sources") or [])[:20],
                }})
            intelligence = _market_intelligence_agent(request).run(
                research_request,
                trace_id=trace_id,
                latency=latency,
            )

        result = _market_strategy_agent(request).run(
            strategy_request,
            intelligence=intelligence,
            user_context=user_context,
            trace_id=trace_id,
            latency=latency,
        )

        if result.status == "needs_input":
            memory_ready = bool(getattr(_memory_service(request), "enabled", False))
            if payload.chat_id is None or _chat_store(request) is None or not memory_ready:
                result = MarketStrategyResponse(
                    agent_name="market_strategy",
                    status="failed",
                    executive_summary="A clarification is required, but persistent profile and chat storage are not configured.",
                    task_id=strategy_request.task_id,
                    trace_id=trace_id,
                    user_id=strategy_request.user_id,
                    project_id=strategy_request.project_id,
                    sources=intelligence.sources,
                    limitations=[
                        *result.limitations,
                        "Configure DATABASE_URL so clarification answers can be saved and the strategy can safely resume.",
                    ],
                    error_code="persistent_context_unavailable",
                )
                _clear_strategy_state(request, payload.user_id, payload.chat_id)
            else:
                _save_strategy_state(request, payload.user_id, payload.chat_id, {
                    "request": strategy_request.model_dump(mode="json"),
                    "intelligence": intelligence.model_dump(mode="json"),
                    "missing_context": result.missing_context,
                    "task_id": strategy_request.task_id,
                    "clarification_question": result.clarification_question,
                })
        else:
            _clear_strategy_state(request, payload.user_id, payload.chat_id)

        if result.error_code == "index_not_found":
            response.status_code = status.HTTP_404_NOT_FOUND
        elif result.error_code in {"generation_unavailable", "persistent_context_unavailable"}:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        elif result.error_code in {"retrieval_error", "generation_error"}:
            response.status_code = status.HTTP_502_BAD_GATEWAY
        result = result.model_copy(update={"chat_id": payload.chat_id})
        _record_chat_message(request, payload.user_id, payload.chat_id, "assistant", {"agent": "market_strategy", **result.model_dump(mode="json")})
        _log_latency_report(trace_id, "market_strategy", payload.user_id, latency, (time.perf_counter() - request_start) * 1000)
        return result
    except RAGIndexNotFoundError as exc:
        logger.warning("Market strategy index missing trace_id=%s user_id=%s", trace_id, payload.user_id)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Market strategy request failed trace_id=%s task_id=%s", trace_id, payload.task_id)
        raise HTTPException(status_code=500, detail="Market strategy request failed.") from exc


@router.post("/agents/meeting-preparation", response_model=MeetingPreparationResponse)
@observed("api.meeting_preparation")
def prepare_meeting(
    payload: MeetingPreparationRequest,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
) -> MeetingPreparationResponse:
    """Orchestrate Market Intelligence and Strategy to produce a tailored CMO meeting briefing."""
    trace_id = str(uuid.uuid4())
    annotate_current_span(metadata={"trace_id": trace_id})
    response.headers["X-Trace-ID"] = trace_id
    _authenticated_user_id(request, payload.user_id)
    request_start = time.perf_counter()
    latency: dict[str, float] = {}
    try:
        user_context = _memory_service(request).get_user_context(payload.user_id, payload.chat_id)
        if not payload.revision_instruction:
            background_tasks.add_task(
                _remember_message,
                request,
                payload.user_id,
                payload.chat_id,
                f"Meeting: {payload.title}. Objective: {payload.objective}",
            )

            _record_chat_message(
                request,
                payload.user_id,
                payload.chat_id,
                "user",
                {"question": f"Meeting Prep: {payload.title} — {payload.objective}"},
            )

        research_objective = f"Meeting: {payload.title}. Objective: {payload.objective}."
        if payload.product:
            research_objective += f" Product: {payload.product}."
        if payload.industry:
            research_objective += f" Industry: {payload.industry}."
        if payload.geography:
            research_objective += f" Geography: {payload.geography}."
        if payload.budget:
            research_objective += f" Budget: {payload.budget}."
        if payload.key_competitors:
            research_objective += f" Competitors: {payload.key_competitors}."
        if payload.timeline:
            research_objective += f" Timeline: {payload.timeline}."
        if payload.attendee_context:
            research_objective += f" Attendee Context: {payload.attendee_context}."

        additional_context: dict[str, Any] = {
            "stored_user_context": user_context,
            "meeting_title": payload.title,
            "attendee_context": payload.attendee_context,
            "product": payload.product,
            "industry": payload.industry,
            "geography": payload.geography,
            "budget": payload.budget,
            "key_competitors": payload.key_competitors,
            "timeline": payload.timeline,
        }
        if payload.previous_briefing:
            additional_context["previous_meeting_briefing"] = payload.previous_briefing
        if payload.revision_instruction:
            additional_context["meeting_revision_instruction"] = payload.revision_instruction
        if payload.document_context:
            additional_context["document_context"] = payload.document_context

        research_request = AgentRequest(
            task_id=str(uuid.uuid4()),
            objective=research_objective,
            user_id=payload.user_id,
            project_id=payload.project_id,
            company_name=payload.company_name,
            company_url=payload.company_url,
            top_k=payload.top_k,
            chat_id=payload.chat_id,
            document_context=payload.document_context,
            additional_context=additional_context,
        )

        intelligence = _market_intelligence_agent(request).run(
            research_request,
            trace_id=trace_id,
            latency=latency,
        )

        strategy_req = MarketStrategyRequest(
            task_id=str(uuid.uuid4()),
            objective=payload.objective,
            user_id=payload.user_id,
            project_id=payload.project_id,
            chat_id=payload.chat_id,
            document_context=payload.document_context,
            top_k=payload.top_k,
        )

        strategy = _market_strategy_agent(request).run(
            strategy_req,
            intelligence=intelligence,
            user_context=user_context,
            trace_id=trace_id,
            latency=latency,
        )

        meeting_user_context = dict(user_context)
        if payload.revision_instruction:
            meeting_user_context["meeting_revision_instruction"] = payload.revision_instruction
            meeting_user_context["previous_meeting_briefing"] = payload.previous_briefing or {}

        result = _meeting_preparation_agent(request).run(
            payload,
            intelligence=intelligence,
            strategy=strategy,
            user_context=meeting_user_context,
            trace_id=trace_id,
            latency=latency,
        )

        if result.error_code == "index_not_found":
            response.status_code = status.HTTP_404_NOT_FOUND
        elif result.error_code == "generation_unavailable":
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        elif result.error_code in {"retrieval_error", "generation_error"}:
            response.status_code = status.HTTP_502_BAD_GATEWAY

        result = result.model_copy(update={"chat_id": payload.chat_id})
        _record_chat_message(
            request,
            payload.user_id,
            payload.chat_id,
            "assistant",
            {"agent": "meeting_preparation", **result.model_dump(mode="json")},
        )
        _log_latency_report(trace_id, "meeting_preparation", payload.user_id, latency, (time.perf_counter() - request_start) * 1000)
        return result
    except RAGIndexNotFoundError as exc:
        logger.warning("Meeting preparation RAG index missing trace_id=%s user_id=%s", trace_id, payload.user_id)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Meeting preparation request failed trace_id=%s task_id=%s", trace_id, payload.task_id)
        raise HTTPException(status_code=500, detail="Meeting preparation request failed.") from exc

@router.post("/agents/meeting-follow-up", response_model=MeetingFollowUpResponse)
@observed("api.meeting_follow_up")
def meeting_follow_up(
    payload: MeetingFollowUpRequest,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
) -> MeetingFollowUpResponse:
    """Route a meeting follow-up to briefing revision or strategy coaching."""
    trace_id = str(uuid.uuid4())
    annotate_current_span(metadata={"trace_id": trace_id})
    response.headers["X-Trace-ID"] = trace_id
    _authenticated_user_id(request, payload.user_id)
    request_start = time.perf_counter()
    latency: dict[str, float] = {}

    try:
        exchanges = _recent_exchanges(request, payload.user_id, payload.chat_id)
        previous_payload = next(
            (
                exchange["payload"]
                for exchange in reversed(exchanges)
                if exchange.get("payload", {}).get("agent") == "meeting_preparation"
                and exchange.get("payload", {}).get("status") in {"completed", "partial"}
            ),
            None,
        )
        if previous_payload is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A completed meeting briefing is required before asking a meeting follow-up.",
            )

        previous_briefing = _meeting_briefing_context(previous_payload)
        meeting_request = MeetingPreparationRequest(
            title=previous_payload.get("meeting_title") or "Meeting preparation",
            objective=previous_payload.get("meeting_objective") or "Prepare for the meeting",
            attendee_context=previous_payload.get("attendee_context"),
            product=previous_payload.get("product"),
            industry=previous_payload.get("industry"),
            geography=previous_payload.get("geography"),
            budget=previous_payload.get("budget"),
            key_competitors=previous_payload.get("key_competitors"),
            timeline=previous_payload.get("timeline"),
            user_id=payload.user_id,
            project_id=payload.project_id,
            chat_id=payload.chat_id,
            document_context=payload.document_context,
            top_k=payload.top_k,
            previous_briefing=previous_briefing,
        )
        intent = _meeting_preparation_agent(request).classify_follow_up(
            payload.objective,
            meeting_request=meeting_request,
            previous_briefing=previous_briefing,
        ).intent

        background_tasks.add_task(
            _remember_message,
            request,
            payload.user_id,
            payload.chat_id,
            payload.objective,
        )
        _record_chat_message(
            request,
            payload.user_id,
            payload.chat_id,
            "user",
            {"question": payload.objective},
        )

        if intent == "modify_meeting":
            revised_request = meeting_request.model_copy(
                update={"revision_instruction": payload.objective}
            )
            revised = prepare_meeting(
                revised_request,
                request,
                Response(),
                BackgroundTasks(),
            )
            if revised.error_code == "index_not_found":
                response.status_code = status.HTTP_404_NOT_FOUND
            elif revised.error_code == "generation_unavailable":
                response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            elif revised.error_code in {"retrieval_error", "generation_error"}:
                response.status_code = status.HTTP_502_BAD_GATEWAY
            _log_latency_report(
                trace_id,
                "meeting_preparation",
                payload.user_id,
                latency,
                (time.perf_counter() - request_start) * 1000,
            )
            return MeetingFollowUpResponse(
                agent="meeting_preparation",
                intent=intent,
                response=revised,
            )

        user_context = _memory_service(request).get_user_context(payload.user_id, payload.chat_id)
        meeting_context = {
            "original_meeting": previous_briefing,
            "previous_briefing": previous_briefing,
            "follow_up_question": payload.objective,
        }
        strategy_request = MarketStrategyRequest(
            task_id=str(uuid.uuid4()),
            objective=payload.objective,
            user_id=payload.user_id,
            project_id=payload.project_id,
            chat_id=payload.chat_id,
            document_context=payload.document_context,
            top_k=payload.top_k,
            meeting_context=meeting_context,
        )
        research_request = _strategy_research_request(strategy_request, user_context)
        intelligence = _market_intelligence_agent(request).run(
            research_request,
            trace_id=trace_id,
            latency=latency,
        )
        strategy = _market_strategy_agent(request).run(
            strategy_request,
            intelligence=intelligence,
            user_context=user_context,
            trace_id=trace_id,
            latency=latency,
        )
        strategy = strategy.model_copy(update={"chat_id": payload.chat_id})
        if strategy.error_code == "index_not_found":
            response.status_code = status.HTTP_404_NOT_FOUND
        elif strategy.error_code in {"generation_unavailable", "persistent_context_unavailable"}:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        elif strategy.error_code in {"retrieval_error", "generation_error"}:
            response.status_code = status.HTTP_502_BAD_GATEWAY
        _record_chat_message(
            request,
            payload.user_id,
            payload.chat_id,
            "assistant",
            {"agent": "market_strategy", **strategy.model_dump(mode="json")},
        )
        _log_latency_report(
            trace_id,
            "market_strategy",
            payload.user_id,
            latency,
            (time.perf_counter() - request_start) * 1000,
        )
        return MeetingFollowUpResponse(
            agent="market_strategy",
            intent=intent,
            response=strategy,
        )
    except HTTPException:
        raise
    except RAGIndexNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(
            "Meeting follow-up request failed trace_id=%s user_id=%s",
            trace_id,
            payload.user_id,
        )
        raise HTTPException(status_code=500, detail="Meeting follow-up request failed.") from exc


@router.post("/web-search/research", response_model=CompanyResearchResponse)
@observed("api.company_research")
def research_companies(
    payload: CompanyResearchRequest,
    request: Request,
) -> CompanyResearchResponse:
    """Run citation-backed Tavily company research outside the RAG path."""
    try:
        result = _company_research_client(request).research(
            payload.question,
            [CompanyResearchTarget(name=item.name, url=str(item.url)) for item in payload.companies],
        )
        return CompanyResearchResponse(
            report=result.report,
            sources=[CompanyResearchSource(title=item.title, url=item.url) for item in result.sources],
            request_id=result.request_id,
        )
    except WebSearchConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except WebSearchProviderError as exc:
        logger.warning("Company research provider failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc



@router.post("/chat/extract-document", response_model=DocumentExtractResponse)
async def extract_chat_document(
    request: Request,
    file: UploadFile = File(...),
) -> DocumentExtractResponse:
    """Extract and chunk only Page 1 of an uploaded document (.pdf, .docx, .txt, .md)."""
    filename = Path(file.filename or "uploaded_document").name
    extension = filename.split(".")[-1].lower() if "." in filename else ""
    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty.")

    page_count = 1
    extracted_text = ""

    if extension == "pdf":
        try:
            doc = fitz.open(stream=raw_bytes, filetype="pdf")
            page_count = len(doc)
            if page_count > 0:
                extracted_text = doc[0].get_text("text") or ""
            doc.close()
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid PDF file: {exc}") from exc
    elif extension == "docx":
        try:
            from docx import Document
            import io
            doc = Document(io.BytesIO(raw_bytes))
            # Take paragraphs for first page estimation (~500 words or first 10 paragraphs)
            paras = [p.text for p in doc.paragraphs if p.text.strip()]
            extracted_text = "\n".join(paras[:15])
            page_count = 1
        except Exception as exc:
            # Fallback to UTF-8 decoding if docx parser fails
            extracted_text = raw_bytes.decode("utf-8", errors="ignore")[:3000]
    else:
        # Plain text / markdown
        extracted_text = raw_bytes.decode("utf-8", errors="ignore")[:4000]

    cleaned_text = "\n".join(line.strip() for line in extracted_text.splitlines() if line.strip())
    if not cleaned_text:
        cleaned_text = "No readable text found on Page 1 of the document."

    # Simple windowed chunking for page 1 content
    words = cleaned_text.split()
    chunk_size = 120
    overlap = 30
    chunks = []
    i = 0
    while i < len(words):
        chunk = " ".join(words[i : i + chunk_size])
        chunks.append(chunk)
        i += (chunk_size - overlap)

    if not chunks:
        chunks = [cleaned_text]

    return DocumentExtractResponse(
        filename=filename,
        page_count=page_count,
        extracted_page=1,
        text=cleaned_text,
        chunks=chunks,
    )


@router.post("/answer", response_model=AnswerResponse)
@observed("api.answer")
def answer_question(
    payload: QuestionRequest,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
) -> AnswerResponse:
    _authenticated_user_id(request, payload.user_id)
    trace_id = str(uuid.uuid4())
    annotate_current_span(metadata={"trace_id": trace_id})
    response.headers["X-Trace-ID"] = trace_id
    logger.info(
        "Answer request trace_id=%s user_id=%s top_k=%s",
        trace_id,
        payload.user_id,
        payload.top_k,
    )
    try:
        # Extraction is an LLM call; deferring it past the response means a
        # fact stated in this message is usable from the next turn rather
        # than this one, in exchange for not paying its latency here.
        background_tasks.add_task(_remember_message, request, payload.user_id, payload.chat_id, payload.question)
        _record_chat_message(request, payload.user_id, payload.chat_id, "user", {"question": payload.question})

        exchanges = eligible_exchanges(_recent_exchanges(request, payload.user_id, payload.chat_id), agent=None)
        hit: PriorExchange | None = exact_repeat(payload.question, exchanges) if exchanges else None
        conversation_history = None
        retrieval_question = None
        if hit is None and exchanges:
            classifier = _reuse_classifier(request)
            if classifier is not None:
                decision = classifier.classify(payload.question, exchanges)
                matched = (
                    exchanges[decision.prior_turn - 1]
                    if decision.prior_turn and 1 <= decision.prior_turn <= len(exchanges)
                    else None
                )
                if decision.relation == "repeat" and matched is not None:
                    hit = matched
                elif decision.relation == "refinement" and matched is not None:
                    retrieval_question = decision.research_queries[0] if decision.research_queries else None
                    conversation_history = to_conversation_turns([matched])
                elif decision.relation == "follow_up":
                    retrieval_question = decision.research_queries[0] if decision.research_queries else None
                    conversation_history = to_conversation_turns(exchanges[-3:])

        if hit is not None:
            logger.info("Answer replayed from message_id=%s trace_id=%s user_id=%s", hit.message_id, trace_id, payload.user_id)
            answer = AnswerResponse.model_validate({
                **hit.payload,
                "trace_id": trace_id,
                "chat_id": payload.chat_id,
                "reused_from_message_id": hit.message_id,
            })
            answer = answer.model_copy(update={"rag_trace": {**answer.rag_trace, "reused": True}})
            _record_chat_message(request, payload.user_id, payload.chat_id, "assistant", answer.model_dump(mode="json"))
            return answer

        extra: dict = {}
        if conversation_history:
            extra["conversation_history"] = conversation_history
        if retrieval_question and retrieval_question != payload.question:
            extra["retrieval_question"] = retrieval_question
        effective_question = payload.question
        if payload.document_context:
            effective_question = f"Question: {payload.question}\n\n[Uploaded Document (Page 1)]:\n{payload.document_context}"

        dump = payload.model_dump(exclude_none=True, exclude={"chat_id", "document_context"}); dump["question"] = effective_question; result = _service(request).answer(**dump, **extra)
        logger.info(
            "Answer completed trace_id=%s user_id=%s chunks=%s",
            trace_id,
            payload.user_id,
            len(result.chunks),
        )
        answer = AnswerResponse(
            answer=result.answer,
            chunks=result.chunks,
            sources=result.sources,
            trace_id=trace_id,
            rag_trace=RAGService._trace_payload(result.trace),
            chat_id=payload.chat_id,
        )
        _record_chat_message(request, payload.user_id, payload.chat_id, "assistant", answer.model_dump(mode="json"))
        return answer
    except UserIndexNotFoundError as exc:
        logger.warning("Answer index missing trace_id=%s user_id=%s", trace_id, payload.user_id)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except AnswerGenerationUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except AnswerGenerationError as exc:
        logger.warning("Generation failed trace_id=%s user_id=%s: %s", trace_id, payload.user_id, exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Answer generation failed.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Answer request failed trace_id=%s user_id=%s", trace_id, payload.user_id)
        raise HTTPException(status_code=500, detail="Answer request failed.") from exc
