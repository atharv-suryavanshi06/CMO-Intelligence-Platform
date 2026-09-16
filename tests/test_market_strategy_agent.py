from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

# These tests never write to disk via APISettings.user_data_root - routed
# through the OS temp dir (never a repo-relative path) purely as a defensive
# placeholder in case that ever changes.
_TEST_TENANT_DATA_ROOT = Path(tempfile.gettempdir()) / "cmo-rag-tests-tenant-data"

from fastapi.testclient import TestClient

from multimodal_rag.agents.market_strategy import MarketStrategyAgent
from multimodal_rag.agents.models import (
    AgentEvidence,
    CompetitorFinding,
    MarketIntelligenceResponse,
    MarketStrategyRequest,
    MarketStrategyResponse,
    OpportunityRiskFinding,
    ResolvedScope,
    ResolvedScopeValue,
)
from multimodal_rag.api.config import APISettings
from multimodal_rag.api.main import create_app


def intelligence(
    *,
    status: str = "completed",
    with_findings: bool = True,
    with_sources: bool = True,
) -> MarketIntelligenceResponse:
    evidence = AgentEvidence(
        chunk_id="evidence-1",
        source="market-report.pdf",
        document="market-report",
        title="Market report",
        text_excerpt="Documented demand for better fit guidance is increasing.",
    )
    competitors = [
        CompetitorFinding(
            competitor="Example Rival",
            activity_change="Introduced digital fit guidance.",
            evidence_facts=["The launch is documented."],
            importance="It addresses a visible customer need.",
            confidence=0.8,
            evidence=[evidence],
        )
    ] if with_findings else []
    opportunities = [
        OpportunityRiskFinding(
            title="Fit guidance",
            description="Demand for better fit guidance is documented.",
            evidence_facts=["Demand is increasing."],
            importance="It can improve purchase confidence.",
            confidence=0.8,
            evidence=[evidence],
        )
    ] if with_findings else []
    return MarketIntelligenceResponse(
        agent_name="market_intelligence",
        status=status,
        executive_summary="Evidence identifies a customer need and a relevant competitor.",
        competitor_intelligence=competitors,
        market_opportunities=opportunities,
        sources=[evidence] if with_sources else [],
        resolved_scope=ResolvedScope(
            industry=ResolvedScopeValue(value="footwear", origin="default"),
            geography=ResolvedScopeValue(value="global", origin="default"),
            time_range=ResolvedScopeValue(value="last 30 days", origin="default"),
        ),
        task_id="task",
        trace_id="trace",
        user_id="alice",
    )


def strategy_payload(evidence_id: str = "evidence-1") -> str:
    return json.dumps({
        "executive_summary": "Prioritize fit guidance for the known audience.",
        "strategic_situation": "Evidence indicates a relevant customer need.",
        "top_opportunities": [{
            "title": "Fit guidance",
            "observation": "Demand is documented.",
            "implication": "The company can address a customer need.",
            "recommendation": "Test fit guidance messaging.",
            "priority": "high",
            "evidence_ids": [evidence_id],
        }],
        "key_risks": [],
        "recommended_priorities": [{
            "title": "Validate fit guidance",
            "observation": "Demand is documented.",
            "implication": "The opportunity fits the stated objective.",
            "recommendation": "Run a focused test.",
            "priority": "high",
            "horizon": "immediate",
            "expected_impact": "Clarifies positioning relevance.",
            "evidence_ids": [evidence_id],
        }],
        "positioning_messaging_direction": ["Emphasize confidence in product fit."],
        "marketing_channel_direction": [],
        "recommended_next_actions": ["Define a focused test."],
        "assumptions_uncertainties": ["Budget was not provided."],
    })


class MarketStrategyAgentTests(unittest.TestCase):
    def test_readiness_uses_market_intelligence_before_asking_one_question(self):
        prompts: list[str] = []

        def generate(prompt: str) -> str:
            prompts.append(prompt)
            return json.dumps({
                "ready": False,
                "missing_context": ["target geography"],
                "clarifying_question": "Which geography or market are you targeting?",
            })

        result = MarketStrategyAgent(generator=generate).run(
            MarketStrategyRequest(objective="Create a market-entry strategy for us.", user_id="alice"),
            intelligence=intelligence(),
            user_context={"profile": {"company": "Northstar"}},
        )

        self.assertEqual(result.status, "needs_input")
        self.assertEqual(result.clarification_question, "Which geography or market are you targeting?")
        self.assertIn("Example Rival", prompts[0])
        self.assertIn('"origin": "default"', prompts[0])
        # No research/evidence content should reach the user until they answer
        # the clarifying question - only the question itself.
        self.assertEqual(result.sources, [])
        self.assertEqual(result.market_trends, [])
        self.assertEqual(result.competitor_intelligence, [])
        self.assertEqual(result.market_opportunities, [])
        self.assertEqual(result.market_risks, [])
        self.assertEqual(result.key_intelligence_takeaways, [])
        self.assertIsNone(result.market_intelligence_summary)
        self.assertFalse(hasattr(MarketStrategyAgent(), "market_intelligence_agent"))

    def test_generates_only_source_linked_priorities_when_context_is_ready(self):
        responses = iter([
            json.dumps({"ready": True, "missing_context": [], "clarifying_question": None}),
            strategy_payload(),
        ])
        result = MarketStrategyAgent(generator=lambda _prompt: next(responses)).run(
            MarketStrategyRequest(objective="Improve customer acquisition.", user_id="alice"),
            intelligence=intelligence(),
            user_context={"profile": {"primary_industry": "footwear"}, "business_context": {"target_audience": ["runners"]}},
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.recommended_priorities[0].evidence[0].chunk_id, "evidence-1")
        self.assertIn("Budget was not provided.", result.assumptions_uncertainties)
        self.assertEqual(result.competitor_intelligence[0].competitor, "Example Rival")
        self.assertEqual(result.market_opportunities[0].title, "Fit guidance")
        self.assertEqual(result.market_intelligence_summary, intelligence().executive_summary)

    def test_discards_recommendations_with_unknown_evidence(self):
        responses = iter([json.dumps({"ready": True}), strategy_payload("not-real")])
        result = MarketStrategyAgent(generator=lambda _prompt: next(responses)).run(
            MarketStrategyRequest(objective="Improve acquisition.", user_id="alice"),
            intelligence=intelligence(),
        )

        self.assertEqual(result.status, "partial")
        self.assertEqual(result.recommended_priorities, [])

    def test_discards_string_risks_without_failing_valid_strategy(self):
        malformed = json.loads(strategy_payload())
        malformed["key_risks"] = [
            "High consumer price sensitivity.",
            "Limited budget restricts competitive reach.",
        ]
        responses = iter([json.dumps({"ready": True}), json.dumps(malformed)])

        result = MarketStrategyAgent(generator=lambda _prompt: next(responses)).run(
            MarketStrategyRequest(objective="Improve acquisition.", user_id="alice"),
            intelligence=intelligence(),
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.key_risks, [])
        self.assertEqual(result.recommended_priorities[0].title, "Validate fit guidance")

    def test_does_not_generate_without_supported_intelligence(self):
        called = False

        def generate(_prompt: str) -> str:
            nonlocal called
            called = True
            return "{}"

        result = MarketStrategyAgent(generator=generate).run(
            MarketStrategyRequest(objective="Improve acquisition.", user_id="alice"),
            intelligence=intelligence(status="partial", with_findings=False, with_sources=False),
        )

        self.assertEqual(result.status, "partial")
        self.assertFalse(called)

    def test_declined_intelligence_declines_strategy_without_generating(self):
        called = False

        def generate(_prompt: str) -> str:
            nonlocal called
            called = True
            return "{}"

        result = MarketStrategyAgent(generator=generate).run(
            MarketStrategyRequest(objective="Improve acquisition.", user_id="alice"),
            intelligence=intelligence(status="declined", with_findings=False, with_sources=False),
        )

        self.assertEqual(result.status, "declined")
        self.assertEqual(result.error_code, "out_of_scope")
        self.assertFalse(called)
        self.assertEqual(result.sources, [])

    def test_generates_from_mi_sources_when_structured_findings_are_empty(self):
        responses = iter([
            json.dumps({"ready": True}),
            strategy_payload(),
        ])

        result = MarketStrategyAgent(generator=lambda _prompt: next(responses)).run(
            MarketStrategyRequest(objective="Improve acquisition.", user_id="alice"),
            intelligence=intelligence(status="partial", with_findings=False, with_sources=True),
            user_context={"profile": {"company": "Northstar"}},
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.recommended_priorities[0].evidence[0].chunk_id, "evidence-1")


class FakeIntelligenceAgent:
    def __init__(self, events: list[str] | None = None) -> None:
        self.calls = []
        self.events = events

    def run(self, request, *, trace_id, latency=None):
        if self.events is not None:
            self.events.append("market_intelligence")
        self.calls.append((request, trace_id))
        return intelligence().model_copy(update={"task_id": request.task_id, "trace_id": trace_id, "user_id": request.user_id})


class FakeMemoryService:
    enabled = True

    def __init__(self) -> None:
        self.messages: list[str] = []

    def initialize(self) -> None:
        pass

    def process_message(self, _user_id: str, _chat_id: str | None, message: str) -> list[str]:
        self.messages.append(message)
        return ["inserted"]

    def get_user_context(self, _user_id: str, _chat_id: str | None = None) -> dict:
        market = "India" if "India" in self.messages else None
        return {"profile": {"company": "Northstar", **({"target_market": market} if market else {})}, "business_context": {}}

    def clone_context(self, _user_id: str, _source_chat_id: str, _target_chat_id: str) -> None:
        pass


class FakeUserStore:
    def __init__(self) -> None:
        self.states: dict[tuple[str, str], dict] = {}
        self.messages: list[tuple[str, str, str, dict]] = []

    def initialize(self) -> None:
        pass

    def user_for_token(self, _token: str) -> str:
        return "alice"

    def append_message(self, user_id: str, chat_id: str, role: str, payload: dict) -> None:
        self.messages.append((user_id, chat_id, role, payload))

    def get_strategy_state(self, user_id: str, chat_id: str) -> dict | None:
        return self.states.get((user_id, chat_id))

    def set_strategy_state(self, user_id: str, chat_id: str, state: dict) -> None:
        self.states[(user_id, chat_id)] = state

    def clear_strategy_state(self, user_id: str, chat_id: str) -> None:
        self.states.pop((user_id, chat_id), None)

    def get_recent_exchanges(self, user_id: str, chat_id: str, limit: int = 10) -> list[dict]:
        exchanges: list[dict] = []
        question = None
        for uid, cid, role, payload in self.messages:
            if uid != user_id or cid != chat_id:
                continue
            if role == "user":
                question = payload.get("question")
            else:
                exchanges.append({"message_id": str(len(exchanges)), "question": question, "payload": payload})
                question = None
        return exchanges[-limit:]


class MarketStrategyAPITests(unittest.TestCase):
    def test_exact_repeat_replays_without_running_market_intelligence(self):
        responses = iter([
            json.dumps({"on_topic": True}),  # classify_message gate for the first request
            json.dumps({"ready": True, "missing_context": [], "clarifying_question": None}),
            strategy_payload(),
        ])

        def generate(prompt: str) -> str:
            return next(responses)

        market_intelligence = FakeIntelligenceAgent()
        memory = FakeMemoryService()
        store = FakeUserStore()
        app = create_app(
            settings=APISettings(user_data_root=_TEST_TENANT_DATA_ROOT, api_auth_token="test-token"),
            market_intelligence_agent=market_intelligence,
            market_strategy_agent=MarketStrategyAgent(generator=generate),
            memory_service=memory,
            user_store=store,
        )
        client = TestClient(app, headers={"Authorization": "Bearer test-token"})

        first = client.post("/agents/market-strategy", json={"objective": "Improve customer acquisition.", "user_id": "alice", "chat_id": "chat-1"})
        second = client.post("/agents/market-strategy", json={"objective": "  Improve customer acquisition.  ", "user_id": "alice", "chat_id": "chat-1"})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["status"], "completed")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["status"], "completed")
        self.assertEqual(len(market_intelligence.calls), 1)  # not re-run on the repeat
        self.assertIsNotNone(second.json()["reused_from_message_id"])
        self.assertNotEqual(second.json()["trace_id"], first.json()["trace_id"])
        self.assertEqual(second.json()["executive_summary"], first.json()["executive_summary"])

    def test_clarification_restores_original_objective_and_reuses_intelligence(self):
        prompts: list[str] = []
        events: list[str] = []
        responses = iter([
            json.dumps({"on_topic": True}),  # classify_message gate for the first request
            json.dumps({"ready": False, "missing_context": ["target geography"], "clarifying_question": "Which geography or market are you targeting?"}),
            json.dumps({"on_topic": True}),  # classify_message gate for the second (clarification-answer) request
            json.dumps({"ready": True, "missing_context": [], "clarifying_question": None}),
            strategy_payload(),
        ])

        def generate(prompt: str) -> str:
            events.append("strategy_generation")
            prompts.append(prompt)
            return next(responses)

        market_intelligence = FakeIntelligenceAgent(events)
        memory = FakeMemoryService()
        store = FakeUserStore()
        app = create_app(
            settings=APISettings(user_data_root=_TEST_TENANT_DATA_ROOT, api_auth_token="test-token"),
            market_intelligence_agent=market_intelligence,
            market_strategy_agent=MarketStrategyAgent(generator=generate),
            memory_service=memory,
            user_store=store,
        )
        client = TestClient(app, headers={"Authorization": "Bearer test-token"})

        first = client.post("/agents/market-strategy", json={"objective": "Create a market-entry strategy for us.", "user_id": "alice", "chat_id": "chat-1"})
        second = client.post("/agents/market-strategy", json={"objective": "India", "user_id": "alice", "chat_id": "chat-1"})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["status"], "needs_input")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["status"], "completed")
        # events[0] is now the classify_message gate (a generator call on the
        # strategy agent) that runs before Market Intelligence is invoked.
        self.assertEqual(events.index("market_intelligence"), 1)
        self.assertEqual(len(market_intelligence.calls), 1)
        self.assertEqual(market_intelligence.calls[0][0].additional_context["stored_user_context"]["profile"]["company"], "Northstar")
        self.assertIn("Create a market-entry strategy for us.", prompts[-1])
        # Memory extraction now runs as a FastAPI background task (see
        # router.py) so it happens *after* this response is built, trading
        # same-turn availability for not paying its LLM-call latency here:
        # "India" is recorded for the next turn's context rather than this
        # one's, but the extraction call itself still fires.
        self.assertIn("India", memory.messages)
        self.assertNotIn(("alice", "chat-1"), store.states)

    def test_off_topic_reply_does_not_consume_pending_clarification(self):
        responses = iter([
            json.dumps({"on_topic": True}),  # classify_message gate for the first request
            json.dumps({"ready": False, "missing_context": ["target geography"], "clarifying_question": "Which geography or market are you targeting?"}),
            json.dumps({"on_topic": False}),  # classify_message gate for the off-topic follow-up ("hi")
        ])

        def generate(prompt: str) -> str:
            return next(responses)

        market_intelligence = FakeIntelligenceAgent()
        memory = FakeMemoryService()
        store = FakeUserStore()
        app = create_app(
            settings=APISettings(user_data_root=_TEST_TENANT_DATA_ROOT, api_auth_token="test-token"),
            market_intelligence_agent=market_intelligence,
            market_strategy_agent=MarketStrategyAgent(generator=generate),
            memory_service=memory,
            user_store=store,
        )
        client = TestClient(app, headers={"Authorization": "Bearer test-token"})

        first = client.post("/agents/market-strategy", json={"objective": "Create a market-entry strategy for us.", "user_id": "alice", "chat_id": "chat-1"})
        second = client.post("/agents/market-strategy", json={"objective": "hi", "user_id": "alice", "chat_id": "chat-1"})

        self.assertEqual(first.json()["status"], "needs_input")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["status"], "declined")
        self.assertEqual(second.json()["error_code"], "out_of_scope")
        # "hi" must not be treated as an answer re-triggering the old
        # clarifying question, and the pending state must survive so the
        # user can still answer it afterward.
        self.assertIsNone(second.json()["clarification_question"])
        self.assertIn(("alice", "chat-1"), store.states)
        self.assertEqual(len(market_intelligence.calls), 1)

    def test_bare_greeting_gets_a_welcome_and_preserves_pending_clarification(self):
        responses = iter([
            json.dumps({"on_topic": True}),  # classify_message gate for the first request
            json.dumps({"ready": False, "missing_context": ["target geography"], "clarifying_question": "Which geography or market are you targeting?"}),
            json.dumps({"on_topic": False, "is_greeting": True}),  # classify_message gate for "hi"
        ])

        def generate(prompt: str) -> str:
            return next(responses)

        market_intelligence = FakeIntelligenceAgent()
        memory = FakeMemoryService()
        store = FakeUserStore()
        app = create_app(
            settings=APISettings(user_data_root=_TEST_TENANT_DATA_ROOT, api_auth_token="test-token"),
            market_intelligence_agent=market_intelligence,
            market_strategy_agent=MarketStrategyAgent(generator=generate),
            memory_service=memory,
            user_store=store,
        )
        client = TestClient(app, headers={"Authorization": "Bearer test-token"})

        first = client.post("/agents/market-strategy", json={"objective": "Create a market-entry strategy for us.", "user_id": "alice", "chat_id": "chat-1"})
        second = client.post("/agents/market-strategy", json={"objective": "hi", "user_id": "alice", "chat_id": "chat-1"})

        self.assertEqual(first.json()["status"], "needs_input")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["status"], "declined")
        self.assertIsNone(second.json()["error_code"])
        self.assertIn("Hello", second.json()["executive_summary"])
        self.assertIn(("alice", "chat-1"), store.states)

    def test_strategy_endpoint_rejects_temporary_company_fields(self):
        app = create_app(
            settings=APISettings(user_data_root=_TEST_TENANT_DATA_ROOT, api_auth_token="test-token"),
            market_intelligence_agent=FakeIntelligenceAgent(),
            market_strategy_agent=MarketStrategyAgent(generator=lambda _prompt: "{}"),
        )
        response = TestClient(app, headers={"Authorization": "Bearer test-token"}).post(
            "/agents/market-strategy",
            json={"objective": "Improve strategy", "user_id": "alice", "company_name": "Temporary Co"},
        )

        self.assertEqual(response.status_code, 422)

    def test_injected_completed_strategy_agent_uses_intelligence_handoff(self):
        class FakeStrategyAgent:
            def run(self, request, *, intelligence, user_context, trace_id, latency=None):
                self.intelligence = intelligence
                return MarketStrategyResponse(
                    agent_name="market_strategy", status="completed", executive_summary="done",
                    task_id=request.task_id, trace_id=trace_id, user_id=request.user_id, project_id=request.project_id,
                )

        strategy = FakeStrategyAgent()
        app = create_app(
            settings=APISettings(user_data_root=_TEST_TENANT_DATA_ROOT, api_auth_token="test-token"),
            market_intelligence_agent=FakeIntelligenceAgent(),
            market_strategy_agent=strategy,
        )
        response = TestClient(app, headers={"Authorization": "Bearer test-token"}).post(
            "/agents/market-strategy",
            json={"objective": "Improve our marketing strategy", "user_id": "alice"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(strategy.intelligence.agent_name, "market_intelligence")

    def test_attached_document_weighting_policy_in_strategy(self):
        captured_prompts: list[str] = []

        def recording_generator(prompt: str):
            captured_prompts.append(prompt)
            if "you decide whether a cmo request has enough known context" in prompt.lower():
                return json.dumps({"ready": True, "missing_context": [], "clarifying_question": None})
            return json.dumps({
                "executive_summary": "P1: Core shift.\n\nP2: Priority focus.\n\nP3: Immediate next steps.",
                "strategic_situation": "Our company document details current market position.",
                "top_opportunities": [{
                    "title": "Document Expansion",
                    "observation": "Document shows strong market fit.",
                    "implication": "Can accelerate go-to-market.",
                    "recommendation": "Launch targeted digital pilots.",
                    "priority": "high",
                    "evidence_ids": ["doc:attached_page_1"],
                }],
                "key_risks": [],
                "recommended_priorities": [{
                    "title": "Focus on Document Priorities",
                    "observation": "Core customer segment is ready.",
                    "implication": "Immediate revenue opportunity.",
                    "recommendation": "Deploy direct outreach.",
                    "priority": "high",
                    "horizon": "immediate",
                    "expected_impact": "High return.",
                    "evidence_ids": ["doc:attached_page_1"],
                }],
                "positioning_messaging_direction": ["Value-led messaging"],
                "marketing_channel_direction": ["Direct B2B"],
                "recommended_next_actions": ["Deploy outreach"],
                "assumptions_uncertainties": [],
            })

        agent = MarketStrategyAgent(generator=recording_generator)
        request = MarketStrategyRequest(
            objective="Develop market strategy for our upcoming launch",
            user_id="alice",
            document_context="Confidential Brief: Launching B2B SaaS platform in North America with $50k budget.",
        )
        mi = intelligence(with_findings=True, with_sources=True)
        response = agent.run(request, intelligence=mi, user_context={})

        self.assertEqual(response.status, "completed")
        self.assertTrue(any(s.kind == "document" for s in response.sources))

        strategy_prompts = [p for p in captured_prompts if "SOURCE WEIGHTING POLICY" in p]
        self.assertTrue(len(strategy_prompts) > 0)
        self.assertIn("~60% ATTACHED DOCUMENT", strategy_prompts[0])
        self.assertIn("~25% EXTERNAL WEB INTELLIGENCE", strategy_prompts[0])
        self.assertIn("~15% RAG KNOWLEDGE BASE", strategy_prompts[0])

        readiness_prompts = [p for p in captured_prompts if "Attached user document context:" in p]
        self.assertTrue(len(readiness_prompts) > 0)
        self.assertIn("Confidential Brief: Launching B2B SaaS platform", readiness_prompts[0])

    def test_strategy_prompt_and_response_include_market_trends_and_competitor_insights(self):
        captured_prompts: list[str] = []

        def recording_generator(prompt: str):
            captured_prompts.append(prompt)
            if "you decide whether a cmo request has enough known context" in prompt.lower():
                return json.dumps({"ready": True, "missing_context": [], "clarifying_question": None})
            return strategy_payload()

        agent = MarketStrategyAgent(generator=recording_generator)
        request = MarketStrategyRequest(objective="Develop market strategy", user_id="alice")
        mi = intelligence(with_findings=True, with_sources=True)
        response = agent.run(request, intelligence=mi, user_context={})

        self.assertEqual(response.status, "completed")
        self.assertEqual(len(response.competitor_intelligence), 1)
        self.assertEqual(response.competitor_intelligence[0].competitor, "Example Rival")

        self.assertTrue(any("What's Trending in the Market" in p for p in captured_prompts))
        self.assertTrue(any("Competitor Insights" in p for p in captured_prompts))

    def test_meeting_question_follow_up_returns_questions(self):
        captured_prompts = []

        def recording_generator(prompt):
            captured_prompts.append(prompt)
            return json.dumps({
                "executive_summary": "Here are additional questions tailored to the meeting.",
                "strategic_situation": "The meeting needs sharper discovery questions.",
                "top_opportunities": [],
                "key_risks": [],
                "recommended_priorities": [],
                "positioning_messaging_direction": [],
                "marketing_channel_direction": [],
                "recommended_next_actions": [],
                "assumptions_uncertainties": [],
                "meeting_questions": [
                    "Which customer segment should we prioritize first, and why?",
                    "What evidence would make us change the proposed investment?",
                ],
            })

        request = MarketStrategyRequest(
            objective="Give me some more questions the CMO should ask in the meeting.",
            user_id="alice",
            meeting_context={
                "previous_briefing": {"questions_to_ask": ["What is the launch date?"]},
                "follow_up_question": "Give me some more questions the CMO should ask in the meeting.",
            },
        )
        response = MarketStrategyAgent(generator=recording_generator).run(
            request,
            intelligence=intelligence(with_findings=True, with_sources=True),
            user_context={},
        )

        self.assertEqual(response.status, "completed")
        self.assertEqual(len(response.meeting_questions), 2)
        self.assertEqual(response.market_trends, [])
        self.assertEqual(response.competitor_intelligence, [])
        strategy_prompts = [p for p in captured_prompts if "MEETING FOLLOW-UP RESPONSE" in p]
        self.assertEqual(len(strategy_prompts), 1)
        self.assertIn("populate meeting_questions", strategy_prompts[0])
        self.assertIn("Do not include a What's Trending in the Market section", strategy_prompts[0])


if __name__ == "__main__":
    unittest.main()
