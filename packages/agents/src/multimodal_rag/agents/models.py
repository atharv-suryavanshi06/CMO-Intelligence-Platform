"""Reusable request, response, and provenance models for platform agents."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from multimodal_rag.api.config import validate_scope_identifier

AgentStatus = Literal["completed", "partial", "needs_input", "failed", "declined"]
MeetingFollowUpIntent = Literal["modify_meeting", "expand_strategy"]
MeetingFollowUpAgent = Literal["meeting_preparation", "market_strategy"]
ScopeOrigin = Literal["request", "default"]
EvidenceRecency = Literal["recent", "not_recent", "unknown"]
EvidenceKind = Literal["rag", "web", "document"]


class AgentRequest(BaseModel):
    """Common context passed from the manager/UI to an agent."""

    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()), min_length=1, max_length=128)
    objective: str = Field(min_length=1, max_length=10_000)
    user_id: str = Field(min_length=1, max_length=128)
    project_id: str | None = Field(default=None, max_length=128)
    industry: str | None = Field(default=None, max_length=256)
    geography: str | None = Field(default=None, max_length=256)
    time_range: str | None = Field(default=None, max_length=256)
    company_name: str | None = Field(default=None, max_length=256)
    company_url: str | None = Field(default=None, max_length=2048)
    additional_context: dict[str, Any] = Field(default_factory=dict)
    top_k: int = Field(default=8, ge=1, le=50)
    chat_id: str | None = Field(default=None, min_length=1, max_length=128)
    document_context: str | None = Field(default=None, max_length=200_000)

    @field_validator("objective")
    @classmethod
    def objective_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("objective must not be blank")
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


    @field_validator("industry", "geography", "time_range", "company_name", "company_url")
    @classmethod
    def optional_context_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class MarketStrategyRequest(BaseModel):
    """Strict strategy input; personalization is loaded from stored memory."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()), min_length=1, max_length=128)
    objective: str = Field(min_length=1, max_length=10_000)
    user_id: str = Field(min_length=1, max_length=128)
    project_id: str | None = Field(default=None, max_length=128)
    time_range: str | None = Field(default=None, max_length=256)
    top_k: int = Field(default=8, ge=1, le=50)
    chat_id: str | None = Field(default=None, min_length=1, max_length=128)
    document_context: str | None = Field(default=None, max_length=200_000)
    meeting_context: dict[str, Any] | None = None

    @field_validator("objective")
    @classmethod
    def objective_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("objective must not be blank")
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

    @field_validator("time_range")
    @classmethod
    def time_range_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class AgentEvidence(BaseModel):
    """A retrieved chunk and its complete provenance."""

    chunk_id: str
    kind: EvidenceKind = "rag"
    source: str
    document: str
    title: str | None = None
    page: int | None = None
    pages: list[int] = Field(default_factory=list)
    score: float | None = None
    text_excerpt: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    publication_date: str | None = None
    url: str | None = None
    query: str | None = None
    rank: int | None = None
    recency: EvidenceRecency = "unknown"

    @model_validator(mode="after")
    def populate_provenance_fields(self) -> "AgentEvidence":
        if self.publication_date is None:
            for key in ("publication_date", "published_at", "published", "date"):
                value = self.metadata.get(key)
                if value is not None:
                    self.publication_date = str(value)
                    break
        if self.url is None:
            for key in ("url", "source_url", "source_uri"):
                value = self.metadata.get(key)
                if value is not None:
                    self.url = str(value)
                    break
        return self


class ResolvedScopeValue(BaseModel):
    value: str
    origin: ScopeOrigin


class ResolvedScope(BaseModel):
    industry: ResolvedScopeValue
    geography: ResolvedScopeValue
    time_range: ResolvedScopeValue


class AgentFinding(BaseModel):
    """A reusable evidence-backed finding for downstream agents."""

    trend: str
    description: str
    facts: list[str] = Field(default_factory=list)
    inference: str | None = None
    momentum: str = "unclear"
    impact: str = "unclear"
    relevance: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[AgentEvidence] = Field(default_factory=list)


class AgentResponse(BaseModel):
    """Common response envelope used across current and future agents."""

    agent_name: str
    status: AgentStatus
    summary: str
    findings: list[AgentFinding] = Field(default_factory=list)
    sources: list[AgentEvidence] = Field(default_factory=list)
    task_id: str
    trace_id: str
    user_id: str
    project_id: str | None = None
    resolved_scope: ResolvedScope | None = None
    missing_context: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    error_code: str | None = None


class MarketTrendIntelligenceFinding(BaseModel):
    """One evidence-backed market trend."""

    trend: str
    what_is_happening: str
    evidence_facts: list[str] = Field(default_factory=list)
    inference: str | None = None
    importance: str
    significance: str = "unclear"
    momentum: str = "unclear"
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[AgentEvidence] = Field(default_factory=list)


class CompetitorFinding(BaseModel):
    """One source-linked competitor activity finding."""

    competitor: str
    activity_change: str
    evidence_facts: list[str] = Field(default_factory=list)
    inference: str | None = None
    importance: str
    significance: str = "unclear"
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[AgentEvidence] = Field(default_factory=list)


class OpportunityRiskFinding(BaseModel):
    """A research-derived opportunity or risk, not a strategic recommendation."""

    title: str
    description: str
    evidence_facts: list[str] = Field(default_factory=list)
    importance: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[AgentEvidence] = Field(default_factory=list)


class MarketIntelligenceResponse(BaseModel):
    """Structured market and competitor intelligence for downstream agents."""

    agent_name: str
    status: AgentStatus
    executive_summary: str
    market_trends: list[MarketTrendIntelligenceFinding] = Field(default_factory=list)
    competitor_intelligence: list[CompetitorFinding] = Field(default_factory=list)
    market_opportunities: list[OpportunityRiskFinding] = Field(default_factory=list)
    market_risks: list[OpportunityRiskFinding] = Field(default_factory=list)
    key_intelligence_takeaways: list[str] = Field(default_factory=list)
    sources: list[AgentEvidence] = Field(default_factory=list)
    task_id: str
    trace_id: str
    user_id: str
    project_id: str | None = None
    resolved_scope: ResolvedScope | None = None
    missing_context: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    error_code: str | None = None
    chat_id: str | None = None
    reused_from_message_id: str | None = None


StrategyPriority = Literal["high", "medium", "low"]
StrategyHorizon = Literal["immediate", "near_term", "longer_term"]


class StrategyFinding(BaseModel):
    """An evidence-linked strategic opportunity or risk."""

    title: str
    observation: str
    implication: str
    recommendation: str
    priority: StrategyPriority
    evidence: list[AgentEvidence] = Field(default_factory=list)


class StrategicPriorityFinding(StrategyFinding):
    """A ranked strategic priority with an expected planning horizon."""

    horizon: StrategyHorizon
    expected_impact: str


class MarketStrategyResponse(BaseModel):
    """CMO-level strategy grounded in market intelligence and known context."""

    agent_name: str
    status: AgentStatus
    executive_summary: str
    market_intelligence_summary: str | None = None
    market_intelligence_scope: ResolvedScope | None = None
    market_trends: list[MarketTrendIntelligenceFinding] = Field(default_factory=list)
    competitor_intelligence: list[CompetitorFinding] = Field(default_factory=list)
    market_opportunities: list[OpportunityRiskFinding] = Field(default_factory=list)
    market_risks: list[OpportunityRiskFinding] = Field(default_factory=list)
    key_intelligence_takeaways: list[str] = Field(default_factory=list)
    clarification_question: str | None = None
    strategic_situation: str | None = None
    top_opportunities: list[StrategyFinding] = Field(default_factory=list)
    key_risks: list[StrategyFinding] = Field(default_factory=list)
    recommended_priorities: list[StrategicPriorityFinding] = Field(default_factory=list)
    positioning_messaging_direction: list[str] = Field(default_factory=list)
    marketing_channel_direction: list[str] = Field(default_factory=list)
    recommended_next_actions: list[str] = Field(default_factory=list)
    assumptions_uncertainties: list[str] = Field(default_factory=list)
    meeting_questions: list[str] = Field(default_factory=list)
    sources: list[AgentEvidence] = Field(default_factory=list)
    task_id: str
    trace_id: str
    user_id: str
    project_id: str | None = None
    missing_context: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    error_code: str | None = None
    chat_id: str | None = None
    reused_from_message_id: str | None = None


class MeetingPreparationRequest(BaseModel):
    """Input for generating a comprehensive CMO meeting briefing."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()), min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=500)
    objective: str = Field(min_length=1, max_length=10_000)
    attendee_context: str | None = Field(default=None, max_length=10_000)
    product: str | None = Field(default=None, max_length=1000)
    industry: str | None = Field(default=None, max_length=1000)
    geography: str | None = Field(default=None, max_length=1000)
    budget: str | None = Field(default=None, max_length=1000)
    key_competitors: str | None = Field(default=None, max_length=1000)
    timeline: str | None = Field(default=None, max_length=1000)
    company_name: str | None = Field(default=None, max_length=256)
    company_url: str | None = Field(default=None, max_length=2048)
    user_id: str = Field(min_length=1, max_length=128)
    project_id: str | None = Field(default=None, max_length=128)
    chat_id: str | None = Field(default=None, min_length=1, max_length=128)
    document_context: str | None = Field(default=None, max_length=200_000)
    top_k: int = Field(default=8, ge=1, le=50)
    revision_instruction: str | None = Field(default=None, max_length=10_000)
    previous_briefing: dict[str, Any] | None = None

    @field_validator("title", "objective")
    @classmethod
    def required_fields_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Field must not be blank")
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

    @field_validator(
        "attendee_context",
        "company_name",
        "company_url",
        "product",
        "industry",
        "geography",
        "budget",
        "key_competitors",
        "timeline",
    )
    @classmethod
    def optional_context_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class MeetingTalkingPoint(BaseModel):
    """A strategic talking point for the CMO with backing rationale."""

    topic: str
    talking_point: str
    rationale_or_evidence: str


class MeetingQuestion(BaseModel):
    """A strategic question for the CMO to ask specific attendees."""

    target_attendee: str = "General / All"
    question: str
    strategic_intent: str


class MeetingRisk(BaseModel):
    """A risk or pushback to watch out for during the meeting, with countermeasures."""

    risk: str
    countermeasure_or_watchout: str
    severity: StrategyPriority = "medium"


class MeetingAction(BaseModel):
    """Recommended decision, next step, or action to secure in the meeting."""

    action: str
    owner_or_role: str = "CMO / Marketing Team"
    timing: str = "Immediate"


class MeetingPreparationResponse(BaseModel):
    """A comprehensive meeting briefing package for the CMO."""

    agent_name: str = "meeting_preparation"
    status: AgentStatus
    meeting_title: str
    meeting_objective: str
    attendee_context: str | None = None
    product: str | None = None
    industry: str | None = None
    geography: str | None = None
    budget: str | None = None
    key_competitors: str | None = None
    timeline: str | None = None
    executive_brief: str
    key_facts_to_remember: list[str] = Field(default_factory=list)
    strategic_talking_points: list[MeetingTalkingPoint] = Field(default_factory=list)
    questions_to_ask: list[MeetingQuestion] = Field(default_factory=list)
    risks_to_watch: list[MeetingRisk] = Field(default_factory=list)
    recommended_actions: list[MeetingAction] = Field(default_factory=list)
    relevant_market_trends: list[MarketTrendIntelligenceFinding] = Field(default_factory=list)
    relevant_competitor_intelligence: list[CompetitorFinding] = Field(default_factory=list)
    sources: list[AgentEvidence] = Field(default_factory=list)
    task_id: str
    trace_id: str
    user_id: str
    project_id: str | None = None
    chat_id: str | None = None
    limitations: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    error_code: str | None = None
    reused_from_message_id: str | None = None


class MeetingFollowUpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str = Field(min_length=1, max_length=10_000)
    user_id: str = Field(min_length=1, max_length=128)
    project_id: str | None = Field(default=None, max_length=128)
    chat_id: str | None = Field(default=None, min_length=1, max_length=128)
    document_context: str | None = Field(default=None, max_length=200_000)
    top_k: int = Field(default=8, ge=1, le=50)

    @field_validator("objective")
    @classmethod
    def objective_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("objective must not be blank")
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


class MeetingFollowUpResponse(BaseModel):
    agent: MeetingFollowUpAgent
    intent: MeetingFollowUpIntent
    response: MeetingPreparationResponse | MarketStrategyResponse

