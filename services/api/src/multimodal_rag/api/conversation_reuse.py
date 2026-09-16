"""Conversation-aware reuse: detect repeat/refinement/follow-up questions in a
chat so repeats can replay verbatim and near-repeats research only the delta.

Lives in the API layer (not rag-core or agents) because it is pure
orchestration over already-stored chat messages: window selection, the cheap
hash pre-check, prompt-block formatting, and adapting stored exchanges into
`ConversationTurn`s for the RAG prompt builder. Putting it in rag-core would
drag ingestion (and its heavier deps) into rag/generation/*; putting it in
agents would create an agents -> ingestion edge. router.py already imports
both `deduplicator` and the agents package, so this adds no new edges.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from multimodal_rag.ingestion.output.deduplicator import normalized_hash
from multimodal_rag.rag.generation.answer_generator import (
    AnswerGenerationError,
    AnswerGenerationUnavailableError,
    GenerationResult,
    generate_answer_with_metadata,
)
from multimodal_rag.rag.generation.prompt_builder import ConversationTurn

logger = logging.getLogger(__name__)

_MAX_ANSWER_CHARS_IN_PROMPT = 800
_TERMINAL_STATUSES = {"declined", "needs_input", "failed"}


@dataclass(frozen=True)
class PriorExchange:
    """One prior (question, assistant-payload) pair eligible for reuse."""

    message_id: str
    question: str
    payload: dict[str, Any]


def answer_text(payload: dict[str, Any]) -> str:
    """Extract the human-readable answer from a stored assistant payload.

    `/answer` stores it under "answer"; both agents store it under
    "executive_summary". This is the one place that needs to know that.
    """
    return str(payload.get("answer") or payload.get("executive_summary") or "")


def eligible_exchanges(records: list[dict[str, Any]], *, agent: str | None) -> list[PriorExchange]:
    """Filter raw (question, payload) records down to safe reuse candidates.

    `agent=None` selects RAG turns (no "agent" key on the payload); otherwise
    selects turns from that specific agent. This is mandatory, not an
    optimization - the user can switch modes mid-chat, and replaying a
    Market Strategy payload as an AnswerResponse (or vice versa) would fail
    validation. Declines, greetings, clarifications, and errored turns are
    dropped: they carry nothing worth replaying or citing as "prior".
    """
    eligible: list[PriorExchange] = []
    for record in records:
        payload = record.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        if payload.get("agent") != agent:
            continue
        if payload.get("status") in _TERMINAL_STATUSES or payload.get("error_code"):
            continue
        question = str(record.get("question") or "").strip()
        answer = answer_text(payload).strip()
        if not question or not answer:
            continue
        eligible.append(PriorExchange(message_id=str(record.get("message_id")), question=question, payload=payload))
    return eligible


def exact_repeat(question: str, exchanges: list[PriorExchange]) -> PriorExchange | None:
    """Return the newest exchange whose question normalizes identically, if any."""
    target = normalized_hash(question)
    for exchange in reversed(exchanges):
        if normalized_hash(exchange.question) == target:
            return exchange
    return None


def format_exchanges(exchanges: list[PriorExchange]) -> str:
    """Render a numbered Q/A block for use inside a classification/generation prompt."""
    lines = []
    for index, exchange in enumerate(exchanges, start=1):
        answer = answer_text(exchange.payload)[:_MAX_ANSWER_CHARS_IN_PROMPT]
        lines.append(f"{index}. Q: {exchange.question}\n   A: {answer}")
    return "\n".join(lines)


def to_conversation_turns(exchanges: list[PriorExchange]) -> list[ConversationTurn]:
    return [
        ConversationTurn(user_query=exchange.question, assistant_answer=answer_text(exchange.payload))
        for exchange in exchanges
    ]


REUSE_INSTRUCTIONS = (
    "Also decide how this question relates to the PRIOR EXCHANGES above: "
    '"repeat" if it is essentially the same question already answered (even '
    'if reworded); "refinement" if it narrows or changes one facet of a '
    "prior question (e.g. same topic but a different geography, competitor, "
    'product, or timeframe); "follow_up" if it builds on a prior answer\'s '
    'topic without repeating it (e.g. "explain that further", "what about '
    'pricing"); or "new" if it is unrelated to every prior exchange. Set '
    "prior_turn to the 1-based number of the most relevant prior exchange "
    '(or null for "new"). Set shared_context to what this question has in '
    "common with that prior exchange, and delta to specifically what is "
    'newly being asked. When relation is "refinement", research_queries '
    "must cover ONLY the delta, not the whole prior topic again."
)


class ReuseDecision(BaseModel):
    """Structured reuse classification. Every field defaulted to the safe
    'treat as a brand new question' value so older/partial LLM output and
    any fixture that predates this feature still validates."""

    relation: str = "new"
    prior_turn: int | None = None
    shared_context: str = ""
    delta: str = ""
    research_queries: list[str] = Field(default_factory=list, max_length=2)


Generator = Any


class ConversationReuseClassifier:
    """Decide whether a new RAG question repeats, refines, or follows up on
    a prior exchange in the same chat. Used only by the `/answer` path -
    the two agents fold the same decision into their own existing LLM calls
    (`_plan` / `classify_message`) instead of paying for a second call."""

    def __init__(self, generator: Generator = generate_answer_with_metadata) -> None:
        self.generator = generator

    def classify(self, question: str, exchanges: list[PriorExchange]) -> ReuseDecision:
        prompt = f"""Decide how a new question in an ongoing chat relates to its recent history.

New question: {question}

--- PRIOR EXCHANGES (most recent last) ---
{format_exchanges(exchanges)}

{REUSE_INSTRUCTIONS}
Return ONLY JSON: {{"relation":"new","prior_turn":null,"shared_context":"","delta":"","research_queries":[]}}."""
        try:
            raw = self.generator(prompt)
            text = raw.text if isinstance(raw, GenerationResult) else str(raw)
            return self._parse(text)
        except (AnswerGenerationUnavailableError, AnswerGenerationError, ValueError):
            logger.warning("Conversation reuse classification failed; treating question as new.")
            return ReuseDecision()

    @staticmethod
    def _parse(text: str) -> ReuseDecision:
        import json
        import re

        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I | re.S).strip()
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("reuse classification did not return a JSON object")
        payload = json.loads(cleaned[start : end + 1])
        return ReuseDecision.model_validate(payload)
