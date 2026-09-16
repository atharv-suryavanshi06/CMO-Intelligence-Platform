from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

_TEST_TENANT_DATA_ROOT = Path(tempfile.gettempdir()) / "cmo-rag-tests-meeting-prep"

from fastapi.testclient import TestClient

from multimodal_rag.agents.meeting_preparation import MeetingPreparationAgent
from multimodal_rag.agents.market_intelligence import MarketIntelligenceAgent
from multimodal_rag.agents.market_strategy import MarketStrategyAgent
from multimodal_rag.agents.models import (
    AgentEvidence,
    AgentRequest,
    CompetitorFinding,
    MarketIntelligenceResponse,
    MarketStrategyRequest,
    MarketStrategyResponse,
    MarketTrendIntelligenceFinding,
    MeetingPreparationRequest,
    MeetingPreparationResponse,
    OpportunityRiskFinding,
    ResolvedScope,
    ResolvedScopeValue,
)
from multimodal_rag.api.config import APISettings
from multimodal_rag.api.main import create_app


def dummy_evidence() -> AgentEvidence:
    return AgentEvidence(
        chunk_id="chunk-1",
        source="q4-trends.pdf",
        document="q4-trends",
        title="Q4 Trends Report",
        text_excerpt="Enterprise adoption of conversational marketing increased by 45% in 2025.",
    )


def sample_intelligence() -> MarketIntelligenceResponse:
    ev = dummy_evidence()
    return MarketIntelligenceResponse(
        agent_name="market_intelligence",
        status="completed",
        executive_summary="Enterprise marketing adoption is shifting rapidly toward AI agents.",
        market_trends=[
            MarketTrendIntelligenceFinding(
                trend="Conversational AI Adoption",
                what_is_happening="Brands are shifting from static campaigns to conversational journeys.",
                evidence_facts=["45% adoption growth reported."],
                importance="Critical for customer acquisition efficiency.",
                confidence=0.88,
                evidence=[ev],
            )
        ],
        competitor_intelligence=[
            CompetitorFinding(
                competitor="CompetitorX",
                activity_change="Launched real-time personalized shopping bot.",
                evidence_facts=["Launched in Q3."],
                importance="Forces faster time-to-market.",
                confidence=0.85,
                evidence=[ev],
            )
        ],
        market_opportunities=[
            OpportunityRiskFinding(
                title="Omnichannel conversational layer",
                description="Unify messaging across WhatsApp and web.",
                evidence_facts=["Higher conversion rates."],
                importance="Immediate revenue impact.",
                confidence=0.9,
                evidence=[ev],
            )
        ],
        sources=[ev],
        resolved_scope=ResolvedScope(
            industry=ResolvedScopeValue(value="Retail", origin="default"),
            geography=ResolvedScopeValue(value="Global", origin="default"),
            time_range=ResolvedScopeValue(value="Past 30 days", origin="default"),
        ),
        task_id="task-1",
        trace_id="trace-1",
        user_id="alice",
    )


def sample_strategy(intel: MarketIntelligenceResponse) -> MarketStrategyResponse:
    return MarketStrategyResponse(
        agent_name="market_strategy",
        status="completed",
        executive_summary="Focus investment on conversational commerce and omnichannel personalization.",
        strategic_situation="The category is consolidating around automated engagement.",
        top_opportunities=[],
        key_risks=[],
        recommended_priorities=[],
        positioning_messaging_direction=["Position as agile innovator"],
        marketing_channel_direction=["Scale WhatsApp and TikTok shop"],
        recommended_next_actions=["Authorize Q4 pilot budget"],
        assumptions_uncertainties=["Assumes engineering support available"],
        sources=intel.sources,
        task_id="task-strat",
        trace_id="trace-strat",
        user_id="alice",
    )


def meeting_prep_json() -> str:
    return json.dumps({
        "executive_brief": "The primary objective of this board review is to align executive leadership on our AI-driven omnichannel growth strategy. With competitors like CompetitorX actively launching automated engagement tools, delaying our rollout risks losing market share in high-intent channels.",
        "key_facts_to_remember": [
            "Enterprise adoption of conversational marketing increased by 45% in 2025.",
            "CompetitorX launched their bot in Q3, capturing early segment buzz.",
            "Our proposed pilot requires a $150k initial spend with an expected 3x ROI."
        ],
        "strategic_talking_points": [
            {
                "topic": "Competitive Urgency",
                "talking_point": "If we wait until next year to pilot conversational commerce, we will be conceding the high-intent customer acquisition funnel to CompetitorX.",
                "rationale_or_evidence": "CompetitorX launched in Q3 and conversational channels have seen 45% growth."
            },
            {
                "topic": "Capital Discipline",
                "talking_point": "This is a phased, ROI-gated initiative where subsequent tranches depend strictly on conversion milestones.",
                "rationale_or_evidence": "Directly addresses CFO concerns regarding unmeasured marketing tech spend."
            }
        ],
        "questions_to_ask": [
            {
                "target_attendee": "CFO",
                "question": "What specific hurdle rate does finance need to see in the first 60 days to greenlight Phase 2?",
                "strategic_intent": "Locks finance into predefined success criteria before launch."
            },
            {
                "target_attendee": "VP Sales",
                "question": "How quickly can the field team absorb inbound leads routed through the new conversational channel?",
                "strategic_intent": "Secures sales SLA alignment for prompt lead follow-up."
            }
        ],
        "risks_to_watch": [
            {
                "risk": "CFO skepticism regarding tech spend ROI and runaway costs.",
                "countermeasure_or_watchout": "Emphasize our phased funding gate and cite the 3x expected return backed by category benchmarks.",
                "severity": "high"
            }
        ],
        "recommended_actions": [
            {
                "action": "Approve $150k budget for the 60-day conversational pilot.",
                "owner_or_role": "CFO & CMO",
                "timing": "End of meeting"
            },
            {
                "action": "Establish weekly marketing-sales lead routing review.",
                "owner_or_role": "CMO & VP Sales",
                "timing": "Next Monday"
            }
        ]
    })


class MeetingPreparationAgentTests(unittest.TestCase):

    def test_run_generates_complete_meeting_preparation_response(self) -> None:
        agent = MeetingPreparationAgent(generator=lambda _p: meeting_prep_json())
        intel = sample_intelligence()
        strat = sample_strategy(intel)

        req = MeetingPreparationRequest(
            title="Executive Strategy Review with Board",
            objective="Secure approval for Q4 conversational commerce pilot budget",
            attendee_context="Attendees: CEO, CFO, VP Sales. CFO is highly sensitive to tech spend ROI.",
            user_id="alice",
        )

        response = agent.run(req, intelligence=intel, strategy=strat)

        self.assertEqual(response.status, "completed")
        self.assertEqual(response.agent_name, "meeting_preparation")
        self.assertEqual(response.meeting_title, req.title)
        self.assertEqual(response.meeting_objective, req.objective)
        self.assertEqual(response.attendee_context, req.attendee_context)
        self.assertIn("The primary objective of this board review", response.executive_brief)
        self.assertEqual(len(response.key_facts_to_remember), 3)
        self.assertEqual(len(response.strategic_talking_points), 2)
        self.assertEqual(response.strategic_talking_points[0].topic, "Competitive Urgency")
        self.assertEqual(len(response.questions_to_ask), 2)
        self.assertEqual(response.questions_to_ask[0].target_attendee, "CFO")
        self.assertEqual(len(response.risks_to_watch), 1)
        self.assertEqual(response.risks_to_watch[0].severity, "high")
        self.assertEqual(len(response.recommended_actions), 2)
        self.assertEqual(len(response.relevant_market_trends), 1)
        self.assertEqual(len(response.relevant_competitor_intelligence), 1)
        self.assertEqual(len(response.sources), 1)

    def test_declined_when_intelligence_declined(self) -> None:
        agent = MeetingPreparationAgent(generator=lambda _p: meeting_prep_json())
        intel = MarketIntelligenceResponse(
            agent_name="market_intelligence",
            status="declined",
            executive_summary="Off topic request.",
            task_id="t",
            trace_id="tr",
            user_id="alice",
        )
        req = MeetingPreparationRequest(
            title="Coffee catchup",
            objective="Chat about vacation",
            user_id="alice",
        )
        response = agent.run(req, intelligence=intel)
        self.assertEqual(response.status, "declined")
        self.assertEqual(response.error_code, "out_of_scope")

    def test_classifies_meeting_follow_up_intent(self) -> None:
        responses = iter([
            '{"intent":"modify_meeting"}',
            '{"intent":"expand_strategy"}',
        ])
        agent = MeetingPreparationAgent(generator=lambda _p: next(responses))
        request = MeetingPreparationRequest(
            title="Board Review",
            objective="Align on the AI strategy",
            user_id="alice",
        )
        previous_briefing = {"executive_brief": "Align on the AI strategy."}

        self.assertEqual(
            agent.classify_follow_up(
                "Add a point about pricing objections.",
                meeting_request=request,
                previous_briefing=previous_briefing,
            ).intent,
            "modify_meeting",
        )
        self.assertEqual(
            agent.classify_follow_up(
                "Explain in more detail what I should say.",
                meeting_request=request,
                previous_briefing=previous_briefing,
            ).intent,
            "expand_strategy",
        )

    def test_api_endpoint_returns_200(self) -> None:
        intel = sample_intelligence()
        strat = sample_strategy(intel)

        class FakeMI:
            def run(self, request, trace_id=None, latency=None):
                return intel

        class FakeMS:
            def run(self, request, intelligence=None, user_context=None, trace_id=None, latency=None):
                return strat

        prep_agent = MeetingPreparationAgent(generator=lambda _p: meeting_prep_json())

        app = create_app(
            settings=APISettings(
                api_auth_token="secret",
                user_data_root=_TEST_TENANT_DATA_ROOT,
            ),
            market_intelligence_agent=FakeMI(),
            market_strategy_agent=FakeMS(),
            meeting_preparation_agent=prep_agent,
        )

        client = TestClient(app)
        res = client.post(
            "/agents/meeting-preparation",
            headers={"Authorization": "Bearer secret"},
            json={
                "title": "Board Review",
                "objective": "Align on 2025 AI strategy",
                "attendee_context": "CEO, CFO",
                "product": "Enterprise AI Suite",
                "industry": "FinTech",
                "geography": "North America",
                "budget": "$1.5M",
                "key_competitors": "CompetitorA",
                "timeline": "Q4 2025",
                "user_id": "alice",
            },
        )

        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["agent_name"], "meeting_preparation")
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["meeting_title"], "Board Review")
        self.assertEqual(data["product"], "Enterprise AI Suite")
        self.assertEqual(data["industry"], "FinTech")
        self.assertEqual(data["geography"], "North America")
        self.assertEqual(data["budget"], "$1.5M")
        self.assertEqual(data["key_competitors"], "CompetitorA")
        self.assertEqual(data["timeline"], "Q4 2025")
        self.assertIn("key_facts_to_remember", data)
        self.assertIn("strategic_talking_points", data)
        self.assertIn("questions_to_ask", data)
        self.assertIn("risks_to_watch", data)
        self.assertIn("recommended_actions", data)
        self.assertIn("relevant_market_trends", data)
        self.assertIn("relevant_competitor_intelligence", data)

    def test_meeting_follow_up_routes_revision_and_strategy(self) -> None:
        intel = sample_intelligence()
        strat = sample_strategy(intel)

        class FakeMI:
            def run(self, request, trace_id=None, latency=None):
                return intel

        class FakeMS:
            def run(self, request, intelligence=None, user_context=None, trace_id=None, latency=None):
                return strat

        class FakeMemory:
            def initialize(self):
                return None

            def process_message(self, user_id, chat_id, message):
                return []

            def get_user_context(self, user_id, chat_id=None):
                return {}

        class FakeStore:
            def __init__(self):
                self.chats = {}

            def initialize(self):
                return None

            def user_for_token(self, token):
                return "alice" if token == "secret" else None

            def append_message(self, user_id, chat_id, role, payload):
                chat = self.chats.setdefault(chat_id, [])
                chat.append({"role": role, "payload": payload})

            def get_recent_exchanges(self, user_id, chat_id, limit=10):
                exchanges = []
                question = None
                for message in self.chats.get(chat_id, []):
                    if message["role"] == "user":
                        question = message["payload"].get("question")
                    else:
                        exchanges.append({"question": question, "payload": message["payload"]})
                        question = None
                return exchanges[-limit:]

        follow_up_intents = iter([
            '{"intent":"modify_meeting"}',
            '{"intent":"expand_strategy"}',
        ])

        def generator(prompt):
            if "Classify this follow-up" in prompt:
                return next(follow_up_intents)
            return meeting_prep_json()

        app = create_app(
            settings=APISettings(api_auth_token="secret", user_data_root=_TEST_TENANT_DATA_ROOT),
            market_intelligence_agent=FakeMI(),
            market_strategy_agent=FakeMS(),
            meeting_preparation_agent=MeetingPreparationAgent(generator=generator),
            memory_service=FakeMemory(),
            user_store=FakeStore(),
        )
        client = TestClient(app)
        headers = {"Authorization": "Bearer secret"}
        initial = client.post(
            "/agents/meeting-preparation",
            headers=headers,
            json={
                "title": "Board Review",
                "objective": "Align on AI strategy",
                "user_id": "alice",
                "chat_id": "chat-1",
            },
        )
        self.assertEqual(initial.status_code, 200)

        revision = client.post(
            "/agents/meeting-follow-up",
            headers=headers,
            json={
                "objective": "Add a point about pricing objections.",
                "user_id": "alice",
                "chat_id": "chat-1",
            },
        )
        self.assertEqual(revision.status_code, 200)
        self.assertEqual(revision.json()["agent"], "meeting_preparation")
        self.assertEqual(revision.json()["intent"], "modify_meeting")
        self.assertEqual(revision.json()["response"]["status"], "completed")

        strategy = client.post(
            "/agents/meeting-follow-up",
            headers=headers,
            json={
                "objective": "Explain in detail what else I should cover.",
                "user_id": "alice",
                "chat_id": "chat-1",
            },
        )
        self.assertEqual(strategy.status_code, 200)
        self.assertEqual(strategy.json()["agent"], "market_strategy")
        self.assertEqual(strategy.json()["intent"], "expand_strategy")
        self.assertEqual(strategy.json()["response"]["agent_name"], "market_strategy")


if __name__ == "__main__":
    unittest.main()
