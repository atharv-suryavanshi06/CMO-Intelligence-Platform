"""Application factory for the API used by CMO-platform agents."""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from multimodal_rag.agents.market_intelligence import MarketIntelligenceAgent
from multimodal_rag.agents.market_strategy import MarketStrategyAgent
from multimodal_rag.agents.meeting_preparation import MeetingPreparationAgent
from multimodal_rag.agents.rag_client import InProcessRAGClient
from multimodal_rag.api.config import APISettings
from multimodal_rag.api.accounts import PostgresUserStore
from multimodal_rag.api.conversation_reuse import ConversationReuseClassifier
from multimodal_rag.api.ingestion import IngestionJobManager
from multimodal_rag.api.presentations import PresentationGenerationService, PresentonClient
from multimodal_rag.api.router import auth_router, router
from multimodal_rag.api.service import RAGService
from multimodal_rag.security.clamav import ClamAVScanner
from multimodal_rag.ingestion.media.deepgram import DeepgramTranscriber
from multimodal_rag.memory import MemoryExtractor, MemoryService, PostgresMemoryRepository
from multimodal_rag.rag.observability import LangSmithObservability, active_request_context
from multimodal_rag.web_search import CompanyResearchClient, SourceGuardService, TavilySearchProvider, WebSearchClient


def create_app(
    settings: APISettings | None = None,
    rag_service: RAGService | None = None,
    market_intelligence_agent: MarketIntelligenceAgent | None = None,
    market_strategy_agent: MarketStrategyAgent | None = None,
    meeting_preparation_agent: MeetingPreparationAgent | None = None,
    ingestion_manager: IngestionJobManager | None = None,
    company_research_client: CompanyResearchClient | None = None,
    memory_service: MemoryService | None = None,
    user_store: PostgresUserStore | None = None,
    reuse_classifier: ConversationReuseClassifier | None = None,
    presentation_service: PresentationGenerationService | None = None,
) -> FastAPI:
    settings = settings or APISettings.from_environment()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    app = FastAPI(title="Multimodal RAG API", version="0.1.0")
    app.state.api_settings = settings
    app.state.api_auth_token = settings.api_auth_token
    app.state.api_username = settings.api_username
    app.state.api_password = settings.api_password
    app.state.langsmith_observability = LangSmithObservability.create(
        enabled=settings.langsmith_tracing,
        api_key=settings.langsmith_api_key,
        endpoint=settings.langsmith_endpoint,
        project_name=settings.langsmith_project,
        environment=settings.rag_environment,
        sampling_rate=settings.langsmith_tracing_sampling_rate,
    )

    @app.middleware("http")
    async def langsmith_context(request, call_next):
        with active_request_context(
            app.state.langsmith_observability,
            path=request.url.path,
            method=request.method,
        ):
            return await call_next(request)
    app.state.user_store = user_store or (PostgresUserStore(settings.database_url) if settings.database_url else None)
    cors_origins = [
        origin.strip()
        for origin in os.getenv(
            "RAG_API_CORS_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173",
        ).split(",")
        if origin.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )
    app.state.rag_service = rag_service or RAGService(settings)
    source_guard = SourceGuardService(
        enabled=settings.web_search_security_enabled,
        virustotal_api_key=settings.virustotal_api_key,
        lakera_guard_api_key=settings.lakera_guard_api_key,
        lakera_project_id=settings.lakera_project_id,
        timeout_seconds=settings.web_search_security_timeout_seconds,
        max_malicious=settings.virustotal_max_malicious,
        max_suspicious=settings.virustotal_max_suspicious,
        max_workers=settings.web_search_security_max_workers,
        url_max_workers=settings.web_search_security_url_max_workers,
        lookup_granularity=settings.web_search_security_lookup_granularity,
        cache_ttl_seconds=settings.web_search_security_cache_ttl_seconds,
        cache_negative_ttl_seconds=settings.web_search_security_cache_negative_ttl_seconds,
        cache_max_entries=settings.web_search_security_cache_max_entries,
        rate_limit_per_minute=settings.virustotal_rate_limit_per_minute,
    )
    app.state.market_intelligence_agent = market_intelligence_agent or MarketIntelligenceAgent(
        InProcessRAGClient(app.state.rag_service),
        WebSearchClient(TavilySearchProvider(timeout_seconds=settings.tavily_timeout_seconds)),
        source_guard=source_guard,
    )
    app.state.market_strategy_agent = market_strategy_agent or MarketStrategyAgent()
    app.state.meeting_preparation_agent = meeting_preparation_agent or MeetingPreparationAgent()
    app.state.conversation_reuse_classifier = reuse_classifier or ConversationReuseClassifier()
    app.state.presentation_service = presentation_service or PresentationGenerationService(
        PresentonClient(
            base_url=settings.presenton_base_url,
            api_key=settings.presenton_api_key,
            timeout_seconds=settings.presenton_timeout_seconds,
        )
    )
    app.state.company_research_client = company_research_client or CompanyResearchClient(
        TavilySearchProvider(timeout_seconds=settings.tavily_timeout_seconds), source_guard=source_guard
    )
    app.state.memory_service = memory_service or (
        MemoryService(MemoryExtractor(), PostgresMemoryRepository(settings.database_url))
        if settings.database_url
        else MemoryService()
    )
    app.state.memory_service.initialize()
    if app.state.user_store is not None:
        app.state.user_store.initialize()
    app.state.ingestion_manager = ingestion_manager or IngestionJobManager(
        scanner=ClamAVScanner(
            enabled=settings.clamav_enabled,
            host=settings.clamav_host,
            port=settings.clamav_port,
            timeout_seconds=settings.clamav_timeout_seconds,
            fail_closed=settings.clamav_fail_closed,
        ),
        transcriber=DeepgramTranscriber(
            api_key=settings.deepgram_api_key,
            model=settings.deepgram_model,
            timeout_seconds=settings.deepgram_timeout_seconds,
        ),
    )
    app.add_event_handler("shutdown", app.state.ingestion_manager.shutdown)
    app.include_router(auth_router)
    app.include_router(router)
    return app


app = create_app()
