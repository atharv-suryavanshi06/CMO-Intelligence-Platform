"""Public API contracts. Keep these independent of RAG implementation types."""

from __future__ import annotations

from typing import Any

from pydantic import AnyHttpUrl, BaseModel, Field, field_validator

from multimodal_rag.api.config import validate_scope_identifier


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)

    @field_validator("username")
    @classmethod
    def username_must_be_safe(cls, value: str) -> str:
        return validate_scope_identifier(value.strip(), "username")


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str | None = None


class ChatSummaryResponse(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int


class ChatMessageResponse(BaseModel):
    id: str
    role: str
    payload: dict[str, Any]
    created_at: str


class ChatResponse(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    messages: list[ChatMessageResponse] = Field(default_factory=list)


class QuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=10_000)
    user_id: str = Field(min_length=1, max_length=128)
    project_id: str | None = Field(default=None, max_length=128)
    top_k: int = Field(default=8, ge=1, le=50)
    chat_id: str | None = Field(default=None, min_length=1, max_length=128)
    document_context: str | None = Field(default=None, max_length=200_000)

    @field_validator("question")
    @classmethod
    def question_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be blank")
        return value.strip()

    @field_validator("user_id")
    @classmethod
    def user_id_must_be_safe(cls, value: str) -> str:
        return validate_scope_identifier(value, "user_id")

    @field_validator("project_id")
    @classmethod
    def project_id_must_be_safe(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_scope_identifier(value, "project_id")


class ChunkResponse(BaseModel):
    text: str
    source: str
    document: str
    page: int | None = None
    score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrieveResponse(BaseModel):
    chunks: list[ChunkResponse]


class SourceResponse(BaseModel):
    source: str
    document: str
    pages: list[int] = Field(default_factory=list)
    chunk_id: str
    section_title: str | None = None


class AnswerResponse(BaseModel):
    answer: str
    chunks: list[ChunkResponse]
    sources: list[SourceResponse]
    trace_id: str
    rag_trace: dict[str, Any]
    chat_id: str | None = None
    reused_from_message_id: str | None = None


class CompanyResearchCompany(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    url: AnyHttpUrl

    @field_validator("name")
    @classmethod
    def company_name_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("company name must not be blank")
        return value.strip()


class CompanyResearchRequest(BaseModel):
    question: str = Field(min_length=1, max_length=10_000)
    companies: list[CompanyResearchCompany] = Field(default_factory=list, max_length=10)

    @field_validator("question")
    @classmethod
    def research_question_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be blank")
        return value.strip()


class CompanyResearchSource(BaseModel):
    title: str = ""
    url: AnyHttpUrl


class CompanyResearchResponse(BaseModel):
    report: str
    sources: list[CompanyResearchSource] = Field(default_factory=list)
    request_id: str | None = None


class IngestionJobResponse(BaseModel):
    job_id: str
    filename: str
    user_id: str
    project_id: str | None = None
    status: str
    stage: str
    document_id: str | None = None
    chunk_count: int = 0
    embedded_count: int = 0
    extraction_completed: int = 0
    extraction_total: int = 0
    extraction_percent: int = 0
    error: str | None = None


class DocumentExtractResponse(BaseModel):
    filename: str
    page_count: int
    extracted_page: int
    text: str
    chunks: list[str] = Field(default_factory=list)


class PresentationTemplateResponse(BaseModel):
    id: str
    name: str
    total_layouts: int | None = None
    preview_url: str | None = None


class PresentationTaskResponse(BaseModel):
    id: str
    status: str
    message: str


class PresentationGenerateRequest(BaseModel):
    chat_id: str = Field(min_length=1, max_length=128)
    template_id: str = Field(min_length=1, max_length=256)
    slide_count: int | None = Field(default=None, ge=3, le=20)

    @field_validator("chat_id")
    @classmethod
    def chat_id_must_be_safe(cls, value: str) -> str:
        return validate_scope_identifier(value, "chat_id")
