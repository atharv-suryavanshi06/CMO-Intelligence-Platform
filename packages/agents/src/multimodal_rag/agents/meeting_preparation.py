"""Context-aware, evidence-grounded meeting preparation briefing agent for CMOs."""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from multimodal_rag.agents.models import (
    AgentEvidence,
    CompetitorFinding,
    MarketIntelligenceResponse,
    MarketStrategyResponse,
    MarketTrendIntelligenceFinding,
    MeetingAction,
    MeetingPreparationRequest,
    MeetingPreparationResponse,
    MeetingFollowUpIntent,
    MeetingQuestion,
    MeetingRisk,
    MeetingTalkingPoint,
    StrategyPriority,
)
from multimodal_rag.rag.observability import observed
from multimodal_rag.rag.generation.answer_generator import (
    AnswerGenerationError,
    AnswerGenerationUnavailableError,
    GenerationResult,
    generate_answer_with_metadata,
)

Generator = Callable[[str], GenerationResult | str]


class MeetingPreparationAgentError(RuntimeError):
    """Raised when a meeting preparation generation response is not safely structured."""


class MeetingTalkingPointDraft(BaseModel):
    topic: str = Field(min_length=1)
    talking_point: str = Field(min_length=1)
    rationale_or_evidence: str = Field(min_length=1)


class MeetingQuestionDraft(BaseModel):
    target_attendee: str = Field(default="General / All", min_length=1)
    question: str = Field(min_length=1)
    strategic_intent: str = Field(min_length=1)


class MeetingRiskDraft(BaseModel):
    risk: str = Field(min_length=1)
    countermeasure_or_watchout: str = Field(min_length=1)
    severity: StrategyPriority = "medium"


class MeetingActionDraft(BaseModel):
    action: str = Field(min_length=1)
    owner_or_role: str = Field(default="CMO / Marketing Team", min_length=1)
    timing: str = Field(default="Immediate", min_length=1)


class MeetingPreparationDraft(BaseModel):
    executive_brief: str = Field(min_length=1)
    key_facts_to_remember: list[str] = Field(default_factory=list, min_length=1, max_length=8)
    strategic_talking_points: list[MeetingTalkingPointDraft] = Field(default_factory=list, min_length=1, max_length=6)
    questions_to_ask: list[MeetingQuestionDraft] = Field(default_factory=list, min_length=1, max_length=6)
    risks_to_watch: list[MeetingRiskDraft] = Field(default_factory=list, min_length=1, max_length=5)
    recommended_actions: list[MeetingActionDraft] = Field(default_factory=list, min_length=1, max_length=5)


class MeetingFollowUpIntentDraft(BaseModel):
    intent: MeetingFollowUpIntent = "expand_strategy"


class MeetingPreparationAgent:
    """Orchestrates market intelligence and strategy into an executive CMO meeting briefing."""

    agent_name = "meeting_preparation"

    def __init__(
        self,
        generator: Generator = generate_answer_with_metadata,
    ) -> None:
        self.generator = generator

    @observed("meeting_preparation.classify_follow_up")
    def classify_follow_up(
        self,
        message: str,
        *,
        meeting_request: MeetingPreparationRequest,
        previous_briefing: dict[str, Any],
    ) -> MeetingFollowUpIntentDraft:
        briefing = json.dumps(previous_briefing, default=str)[:30_000]
        prompt = (
            'Classify this follow-up for a CMO meeting briefing. '
            'Return modify_meeting when the user wants the complete briefing changed, corrected, or updated with a missing point. '
            'Return expand_strategy when the user wants explanation, coaching, more detail, or more things to say without changing the briefing. '
            f'Original meeting: {meeting_request.title} - {meeting_request.objective}. '
            f'Previous briefing: {briefing}. New message: {message}. '
            'Return ONLY JSON: {"intent":"modify_meeting|expand_strategy"}'
        )
        try:
            return self._parse_json(self.generator(prompt), MeetingFollowUpIntentDraft, "meeting follow-up intent")
        except (AnswerGenerationUnavailableError, AnswerGenerationError, MeetingPreparationAgentError, ValueError):
            revision_terms = (
                "add",
                "include",
                "missed",
                "forgot",
                "update",
                "change",
                "correct",
                "remove",
                "replace",
                "revise",
                "regenerate",
            )
            normalized = message.casefold()
            fallback_intent = (
                "modify_meeting"
                if any(term in normalized.split() for term in revision_terms)
                else "expand_strategy"
            )
            return MeetingFollowUpIntentDraft(intent=fallback_intent)

    @observed("agent.meeting_preparation")
    def run(
        self,
        request: MeetingPreparationRequest,
        *,
        intelligence: MarketIntelligenceResponse,
        strategy: MarketStrategyResponse | None = None,
        user_context: dict[str, Any] | None = None,
        trace_id: str | None = None,
        latency: dict[str, float] | None = None,
    ) -> MeetingPreparationResponse:
        trace_id = trace_id or str(uuid.uuid4())
        context = dict(user_context or {})
        prep_start = time.perf_counter()

        # Combine all unique sources
        all_sources: list[AgentEvidence] = []
        seen_chunks: set[str] = set()
        for source in intelligence.sources:
            if source.chunk_id not in seen_chunks:
                seen_chunks.add(source.chunk_id)
                all_sources.append(source)
        if strategy:
            for source in strategy.sources:
                if source.chunk_id not in seen_chunks:
                    seen_chunks.add(source.chunk_id)
                    all_sources.append(source)

        if intelligence.status == "failed":
            return MeetingPreparationResponse(
                agent_name=self.agent_name,
                status="failed",
                meeting_title=request.title,
                meeting_objective=request.objective,
                attendee_context=request.attendee_context,
                product=request.product,
                industry=request.industry,
                geography=request.geography,
                budget=request.budget,
                key_competitors=request.key_competitors,
                timeline=request.timeline,
                executive_brief="Meeting preparation could not be generated because market research failed.",
                sources=all_sources,
                task_id=request.task_id,
                trace_id=trace_id,
                user_id=request.user_id,
                project_id=request.project_id,
                chat_id=request.chat_id,
                limitations=intelligence.limitations,
                errors=intelligence.errors,
                error_code=intelligence.error_code,
            )

        if intelligence.status == "declined":
            return MeetingPreparationResponse(
                agent_name=self.agent_name,
                status="declined",
                meeting_title=request.title,
                meeting_objective=request.objective,
                attendee_context=request.attendee_context,
                product=request.product,
                industry=request.industry,
                geography=request.geography,
                budget=request.budget,
                key_competitors=request.key_competitors,
                timeline=request.timeline,
                executive_brief=intelligence.executive_summary
                or "The meeting topic does not appear to be business or marketing related.",
                task_id=request.task_id,
                trace_id=trace_id,
                user_id=request.user_id,
                project_id=request.project_id,
                chat_id=request.chat_id,
                errors=intelligence.errors,
                error_code="out_of_scope",
            )

        try:
            draft = self._synthesize_meeting_prep(request, context, intelligence, strategy)
        except AnswerGenerationUnavailableError as exc:
            self._add_latency(latency, time.perf_counter() - prep_start)
            return MeetingPreparationResponse(
                agent_name=self.agent_name,
                status="failed",
                meeting_title=request.title,
                meeting_objective=request.objective,
                attendee_context=request.attendee_context,
                product=request.product,
                industry=request.industry,
                geography=request.geography,
                budget=request.budget,
                key_competitors=request.key_competitors,
                timeline=request.timeline,
                executive_brief="Meeting preparation is unavailable because the generation model is not configured.",
                sources=all_sources,
                task_id=request.task_id,
                trace_id=trace_id,
                user_id=request.user_id,
                project_id=request.project_id,
                chat_id=request.chat_id,
                error_code="generation_unavailable",
                errors=[str(exc)],
            )
        except (AnswerGenerationError, MeetingPreparationAgentError, ValueError) as exc:
            self._add_latency(latency, time.perf_counter() - prep_start)
            return MeetingPreparationResponse(
                agent_name=self.agent_name,
                status="failed",
                meeting_title=request.title,
                meeting_objective=request.objective,
                attendee_context=request.attendee_context,
                product=request.product,
                industry=request.industry,
                geography=request.geography,
                budget=request.budget,
                key_competitors=request.key_competitors,
                timeline=request.timeline,
                executive_brief="Meeting preparation briefing generation failed.",
                sources=all_sources,
                task_id=request.task_id,
                trace_id=trace_id,
                user_id=request.user_id,
                project_id=request.project_id,
                chat_id=request.chat_id,
                error_code="generation_error",
                errors=[str(exc)],
            )

        self._add_latency(latency, time.perf_counter() - prep_start)

        # Merge limitations
        limitations = list(intelligence.limitations)
        if strategy:
            limitations.extend(strategy.limitations)
        deduped_limitations = list(dict.fromkeys(limitations))

        return MeetingPreparationResponse(
            agent_name=self.agent_name,
            status="completed",
            meeting_title=request.title,
            meeting_objective=request.objective,
            attendee_context=request.attendee_context,
            product=request.product,
            industry=request.industry,
            geography=request.geography,
            budget=request.budget,
            key_competitors=request.key_competitors,
            timeline=request.timeline,
            executive_brief=draft.executive_brief,
            key_facts_to_remember=draft.key_facts_to_remember,
            strategic_talking_points=[
                MeetingTalkingPoint(
                    topic=tp.topic,
                    talking_point=tp.talking_point,
                    rationale_or_evidence=tp.rationale_or_evidence,
                )
                for tp in draft.strategic_talking_points
            ],
            questions_to_ask=[
                MeetingQuestion(
                    target_attendee=q.target_attendee,
                    question=q.question,
                    strategic_intent=q.strategic_intent,
                )
                for q in draft.questions_to_ask
            ],
            risks_to_watch=[
                MeetingRisk(
                    risk=r.risk,
                    countermeasure_or_watchout=r.countermeasure_or_watchout,
                    severity=r.severity,
                )
                for r in draft.risks_to_watch
            ],
            recommended_actions=[
                MeetingAction(
                    action=a.action,
                    owner_or_role=a.owner_or_role,
                    timing=a.timing,
                )
                for a in draft.recommended_actions
            ],
            relevant_market_trends=intelligence.market_trends,
            relevant_competitor_intelligence=intelligence.competitor_intelligence,
            sources=all_sources,
            task_id=request.task_id,
            trace_id=trace_id,
            user_id=request.user_id,
            project_id=request.project_id,
            chat_id=request.chat_id,
            limitations=deduped_limitations,
            errors=intelligence.errors + (strategy.errors if strategy else []),
        )

    @observed("meeting_preparation.synthesize")
    def _synthesize_meeting_prep(
        self,
        request: MeetingPreparationRequest,
        context: dict[str, Any],
        intelligence: MarketIntelligenceResponse,
        strategy: MarketStrategyResponse | None,
    ) -> MeetingPreparationDraft:
        strategy_block = ""
        if strategy:
            strategy_block = f"""
STRATEGY INSIGHTS:
Executive summary: {strategy.executive_summary}
Strategic situation: {strategy.strategic_situation or 'None'}
Priorities: {[f"{p.title}: {p.recommendation}" for p in strategy.recommended_priorities]}
Opportunities: {[f"{o.title}: {o.recommendation}" for o in strategy.top_opportunities]}
Risks: {[f"{r.title}: {r.recommendation}" for r in strategy.key_risks]}
Positioning: {strategy.positioning_messaging_direction}
Channels: {strategy.marketing_channel_direction}
Next actions: {strategy.recommended_next_actions}
"""

        intelligence_block = f"""
MARKET & COMPETITOR INTELLIGENCE:
Summary: {intelligence.executive_summary}
Trends: {[f"{t.trend}: {t.what_is_happening}" for t in intelligence.market_trends]}
Competitors: {[f"{c.competitor}: {c.activity_change}" for c in intelligence.competitor_intelligence]}
Takeaways: {intelligence.key_intelligence_takeaways}
"""

        prompt = f"""You are an elite CMO advisor preparing an executive CMO for an upcoming high-stakes meeting.
Analyze the meeting parameters, attendee context, company profile, research intelligence, and strategic guidance to produce an actionable meeting briefing kit.

MEETING TITLE: {request.title}
MEETING OBJECTIVE: {request.objective}
ATTENDEE / COUNTERPART CONTEXT: {request.attendee_context or 'Internal / Executive stakeholders'}
PRODUCT / OFFERING: {request.product or 'Not specified'}
INDUSTRY / VERTICAL: {request.industry or context.get('industry') or 'Not specified'}
GEOGRAPHY / TARGET MARKET: {request.geography or context.get('target_geography') or 'Not specified'}
BUDGET / RESOURCES: {request.budget or context.get('budget') or 'Not specified'}
KEY COMPETITORS TO ADDRESS: {request.key_competitors or context.get('competitors') or 'Not specified'}
TIMELINE / HORIZON: {request.timeline or 'Immediate / Ongoing'}
COMPANY FOCUS: {request.company_name or context.get('brand') or 'Current Organization'} {request.company_url or ''}
USER / BUSINESS PROFILE: {json.dumps(context, default=str)}

{intelligence_block}
{strategy_block}

Produce a structured JSON briefing with:
1. "executive_brief": A crisp, high-impact 2-paragraph executive briefing. Paragraph 1 frames the CMO's stance and the meeting's strategic stakes. Paragraph 2 synthesizes how market forces and strategic priorities should drive the desired meeting outcome.
2. "key_facts_to_remember": 3 to 6 critical, memorable facts, metrics, or benchmark data points the CMO can cite effortlessly in conversation.
3. "strategic_talking_points": 3 to 5 persuasive talking points. Each with "topic", "talking_point" (direct, executive language the CMO can say), and "rationale_or_evidence" (why this point matters and how it is backed).
4. "questions_to_ask": 3 to 5 sharp, probing questions for the CMO to ask. Each with "target_attendee" (e.g. "CEO", "CFO", "VP Sales", "Partner", or "General"), "question", and "strategic_intent" (what outcome or admission this question secures).
5. "risks_to_watch": 2 to 4 potential traps, skepticism, or counter-arguments attendees might raise. Each with "risk", "countermeasure_or_watchout" (how the CMO should navigate or pivot), and "severity" ("high", "medium", or "low").
6. "recommended_actions": 2 to 4 concrete decisions or commitments to walk out of the meeting with. Each with "action", "owner_or_role", and "timing".

Return ONLY valid JSON matching this structure:
{{
  "executive_brief": "...",
  "key_facts_to_remember": ["..."],
  "strategic_talking_points": [
    {{"topic": "...", "talking_point": "...", "rationale_or_evidence": "..."}}
  ],
  "questions_to_ask": [
    {{"target_attendee": "...", "question": "...", "strategic_intent": "..."}}
  ],
  "risks_to_watch": [
    {{"risk": "...", "countermeasure_or_watchout": "...", "severity": "high|medium|low"}}
  ],
  "recommended_actions": [
    {{"action": "...", "owner_or_role": "...", "timing": "..."}}
  ]
}}"""
        revision_instruction = context.get("meeting_revision_instruction")
        if revision_instruction:
            previous_briefing = json.dumps(context.get("previous_meeting_briefing", {}), default=str)[:30_000]
            prompt += (
                "\n\nREVISION INSTRUCTION: Update the complete briefing to address this user request: "
                f"{revision_instruction}"
                "\nPrevious briefing to preserve and improve:\n"
                f"{previous_briefing}"
                "\nReturn the full briefing again, including every required section."
            )
        raw = self.generator(prompt)
        return self._parse_json(raw, MeetingPreparationDraft, "meeting preparation briefing")

    def _parse_json(self, raw: GenerationResult | str, model: type[Any], label: str) -> Any:
        content = raw.text if isinstance(raw, GenerationResult) else str(raw)
        stripped = content.strip()
        fenced_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
        candidate = fenced_match.group(1).strip() if fenced_match else stripped
        if not candidate.startswith("{"):
            brace_idx = candidate.find("{")
            rbrace_idx = candidate.rfind("}")
            if brace_idx != -1 and rbrace_idx != -1 and rbrace_idx > brace_idx:
                candidate = candidate[brace_idx : rbrace_idx + 1]
            else:
                raise MeetingPreparationAgentError(f"Generation output did not contain a JSON object for {label}.")
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise MeetingPreparationAgentError(f"Could not parse JSON for {label}: {exc}") from exc

        try:
            return model.model_validate(payload)
        except Exception as exc:
            raise MeetingPreparationAgentError(f"Could not validate {label}: {exc}") from exc

    @staticmethod
    def _add_latency(latency: dict[str, float] | None, elapsed_seconds: float) -> None:
        if latency is not None:
            latency["meeting_prep_ms"] = latency.get("meeting_prep_ms", 0.0) + (elapsed_seconds * 1000.0)
