"""Environment-backed configuration and tenant-scoped artifact paths."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from multimodal_rag.paths import USER_DATA_ROOT_DEFAULT

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

load_dotenv()


def _environment_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def validate_scope_identifier(value: str, field_name: str) -> str:
    """Validate a path-safe tenant identifier before constructing a path."""
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(
            f"{field_name} must be 1-128 characters using letters, numbers, '.', '_' or '-'."
        )
    return value


@dataclass(frozen=True)
class CorpusScope:
    """A tenant boundary; ``project_id`` is reserved for a future API field."""

    user_id: str
    data_root: Path
    project_id: str | None = None

    def __post_init__(self) -> None:
        validate_scope_identifier(self.user_id, "user_id")
        if self.project_id is not None:
            validate_scope_identifier(self.project_id, "project_id")

    @property
    def root(self) -> Path:
        root = self.data_root / self.user_id
        if self.project_id:
            root = root / "projects" / self.project_id
        return root

    @property
    def ingestion_artifacts_dir(self) -> Path:
        return self.root / "artifacts" / "ingestion"

    @property
    def index_dir(self) -> Path:
        return self.root / "artifacts" / "index"


@dataclass(frozen=True)
class APISettings:
    """Settings deliberately limited to the HTTP layer, not RAG tuning."""

    user_data_root: Path
    log_level: str = "INFO"
    rag_environment: str = "development"
    langsmith_tracing: bool = False
    langsmith_api_key: str | None = None
    langsmith_endpoint: str | None = None
    langsmith_project: str = "cmo-intelligence-development"
    langsmith_tracing_sampling_rate: float = 1.0
    api_auth_token: str | None = None
    api_username: str | None = None
    api_password: str | None = None
    clamav_enabled: bool = False
    clamav_host: str = "127.0.0.1"
    clamav_port: int = 3310
    clamav_timeout_seconds: float = 30.0
    clamav_fail_closed: bool = True
    deepgram_api_key: str | None = None
    deepgram_model: str = "nova-3"
    deepgram_timeout_seconds: float = 120.0
    memory_database_url: str | None = None
    database_url: str | None = None
    web_search_security_enabled: bool = True
    virustotal_api_key: str | None = None
    virustotal_max_malicious: int = 0
    virustotal_max_suspicious: int = 1
    lakera_guard_api_key: str | None = None
    lakera_project_id: str | None = None
    web_search_security_timeout_seconds: float = 10.0
    web_search_security_max_workers: int = 8
    web_search_security_url_max_workers: int = 4
    web_search_security_lookup_granularity: str = "domain"
    web_search_security_cache_ttl_seconds: float = 86400.0
    web_search_security_cache_negative_ttl_seconds: float = 60.0
    web_search_security_cache_max_entries: int = 4096
    virustotal_rate_limit_per_minute: int = 4
    tavily_timeout_seconds: float = 30.0
    presenton_base_url: str | None = None
    presenton_api_key: str | None = None
    presenton_timeout_seconds: float = 120.0

    @classmethod
    def from_environment(cls) -> "APISettings":
        configured_root = os.getenv("RAG_USER_DATA_ROOT")
        sampling_rate = float(os.getenv("LANGSMITH_TRACING_SAMPLING_RATE", "1.0"))
        if not 0.0 <= sampling_rate <= 1.0:
            raise ValueError("LANGSMITH_TRACING_SAMPLING_RATE must be between 0 and 1.")
        environment = os.getenv("RAG_ENVIRONMENT", "development").strip() or "development"
        return cls(
            user_data_root=Path(configured_root) if configured_root else USER_DATA_ROOT_DEFAULT,
            log_level=os.getenv("RAG_API_LOG_LEVEL", "INFO").upper(),
            rag_environment=environment,
            langsmith_tracing=_environment_flag("LANGSMITH_TRACING", False),
            langsmith_api_key=os.getenv("LANGSMITH_API_KEY"),
            langsmith_endpoint=os.getenv("LANGSMITH_ENDPOINT"),
            langsmith_project=os.getenv("LANGSMITH_PROJECT") or f"cmo-intelligence-{environment}",
            langsmith_tracing_sampling_rate=sampling_rate,
            api_auth_token=os.getenv("RAG_API_AUTH_TOKEN"),
            api_username=os.getenv("RAG_API_USERNAME"),
            api_password=os.getenv("RAG_API_PASSWORD"),
            clamav_enabled=_environment_flag("RAG_CLAMAV_ENABLED", False),
            clamav_host=os.getenv("RAG_CLAMAV_HOST", "127.0.0.1"),
            clamav_port=int(os.getenv("RAG_CLAMAV_PORT", "3310")),
            clamav_timeout_seconds=float(os.getenv("RAG_CLAMAV_TIMEOUT_SECONDS", "30")),
            clamav_fail_closed=_environment_flag("RAG_CLAMAV_FAIL_CLOSED", True),
            deepgram_api_key=os.getenv("DEEPGRAM_API_KEY"),
            deepgram_model=os.getenv("RAG_DEEPGRAM_MODEL", "nova-3"),
            deepgram_timeout_seconds=float(os.getenv("RAG_DEEPGRAM_TIMEOUT_SECONDS", "120")),
            memory_database_url=os.getenv("RAG_MEMORY_DATABASE_URL"),
            database_url=os.getenv("RAG_DATABASE_URL") or os.getenv("RAG_MEMORY_DATABASE_URL"),
            web_search_security_enabled=_environment_flag("WEB_SEARCH_SECURITY_ENABLED", True),
            virustotal_api_key=os.getenv("VIRUSTOTAL_API_KEY"),
            virustotal_max_malicious=int(os.getenv("VIRUSTOTAL_MAX_MALICIOUS", "0")),
            virustotal_max_suspicious=int(os.getenv("VIRUSTOTAL_MAX_SUSPICIOUS", "1")),
            lakera_guard_api_key=os.getenv("LAKERA_GUARD_API_KEY"),
            lakera_project_id=os.getenv("LAKERA_PROJECT_ID"),
            web_search_security_timeout_seconds=float(os.getenv("WEB_SEARCH_SECURITY_TIMEOUT_SECONDS", "10")),
            web_search_security_max_workers=int(os.getenv("WEB_SEARCH_SECURITY_MAX_WORKERS", "8")),
            web_search_security_url_max_workers=int(os.getenv("WEB_SEARCH_SECURITY_URL_MAX_WORKERS", "4")),
            web_search_security_lookup_granularity=(
                os.getenv("WEB_SEARCH_SECURITY_LOOKUP_GRANULARITY", "domain")
                if os.getenv("WEB_SEARCH_SECURITY_LOOKUP_GRANULARITY", "domain") in {"domain", "url"}
                else "domain"
            ),
            web_search_security_cache_ttl_seconds=float(os.getenv("WEB_SEARCH_SECURITY_CACHE_TTL_SECONDS", "86400")),
            web_search_security_cache_negative_ttl_seconds=float(os.getenv("WEB_SEARCH_SECURITY_CACHE_NEGATIVE_TTL_SECONDS", "60")),
            web_search_security_cache_max_entries=int(os.getenv("WEB_SEARCH_SECURITY_CACHE_MAX_ENTRIES", "4096")),
            virustotal_rate_limit_per_minute=int(os.getenv("VIRUSTOTAL_RATE_LIMIT_PER_MINUTE", "4")),
            tavily_timeout_seconds=float(os.getenv("TAVILY_TIMEOUT_SECONDS", "30")),
            presenton_base_url=os.getenv("PRESENTON_BASE_URL"),
            presenton_api_key=os.getenv("PRESENTON_API_KEY"),
            presenton_timeout_seconds=float(os.getenv("PRESENTON_TIMEOUT_SECONDS", "120")),
        )

    def scope_for(self, user_id: str, project_id: str | None = None) -> CorpusScope:
        return CorpusScope(user_id=user_id, project_id=project_id, data_root=self.user_data_root)
