# Engineering Decisions

## DEC-2026-09-08-04 — Meeting Preparation Agent with Multi-Agent Synthesis and Executive Briefing Interface

**Status:** Accepted

**Context**
CMOs and marketing executives frequently prepare for high-stakes meetings with board members, executive peers, agency partners, or key clients. Prior to this change, obtaining comprehensive meeting preparation required querying the Market Intelligence Agent for market/competitor dynamics and then querying the Market Strategy Agent for positioning and initiatives, followed by manual collation of notes.
The user requested a dedicated **Meeting Preparation Agent** that:
1. Combines outputs from both the Market Intelligence Agent and Market Strategy Agent.
2. Produces structured executive briefing deliverables:
   - Meeting objective
   - Attendee/company context
   - Relevant market trends
   - Relevant competitor intelligence
   - Strategic talking points
   - Questions the CMO should ask
   - Risks / things to watch (with countermeasures and severity)
   - Recommended responses/actions
   - Key facts to remember
   - Executive meeting brief
3. Displays an interactive form popup in the frontend when the agent mode is selected to capture meeting details (title, objective, attendee/company context) before generating the briefing.

**Decision**
1. **Agent Domain Models (`packages/agents/src/multimodal_rag/agents/models.py`)**:
   - Defined `MeetingPreparationRequest` holding required `title` and `objective`, plus optional fields: `attendee_context`, `product`, `industry`, `geography`, `budget`, `key_competitors`, `timeline`, `company_name`, `company_url`, and `document_context`.
   - Defined structured component models: `MeetingTalkingPoint`, `MeetingQuestion`, `MeetingRisk` (with `severity` and `countermeasure`), and `MeetingAction` (with `priority` and `rationale`).
   - Defined `MeetingPreparationResponse` incorporating meeting metadata, optional strategic context fields, `executive_brief`, `key_facts`, `talking_points`, `questions_to_ask`, `risks_to_watch`, `recommended_actions`, `relevant_trends`, `competitor_intelligence`, and `sources`.
2. **Synthesis Engine (`packages/agents/src/multimodal_rag/agents/meeting_preparation.py`)**:
   - Implemented `MeetingPreparationAgent` with structured prompt design and Pydantic validation via `MeetingPreparationDraft`.
   - Incorporates all provided optional strategic parameters (`PRODUCT / OFFERING`, `INDUSTRY / VERTICAL`, `GEOGRAPHY / TARGET MARKET`, `BUDGET / RESOURCES`, `KEY COMPETITORS`, `TIMELINE / HORIZON`) directly into the LLM synthesis context.
   - Combines validated intelligence from `MarketIntelligenceResponse` and strategic positioning from `MarketStrategyResponse` into a cohesive briefing tailored specifically to the meeting parameters.
   - Includes graceful fallback synthesis when LLM generation is declined or encounters non-JSON output.
3. **API Orchestration Endpoint (`services/api/src/multimodal_rag/api/router.py`)**:
   - Exposed `POST /agents/meeting-preparation` handled by `prepare_meeting()`.
   - Dynamically resolves user identity and loads chat memory profile from `MemoryService`.
   - Augments the research objective with provided optional parameters (`Product`, `Industry`, `Geography`, `Budget`, `Competitors`, `Timeline`, `Attendees`) and invokes `MarketIntelligenceAgent.run()` followed by `MarketStrategyAgent.run()`.
   - Calls `MeetingPreparationAgent.run()` with the request and both agent responses.
   - Persists the assistant turn in `user_chat_messages` with `agent="meeting_preparation"` to support chat persistence across reloads.
4. **Interactive Liquid Glass Modal Form & Visual Briefing (`apps/frontend/src/`)**:
   - Styled the modal popup and briefing elements (`.meeting-prep-header-banner`, `.meeting-exec-brief`, `.modal-card`, and form inputs/textareas) in an ultra-crisp **liquid glass** aesthetic (`backdrop-filter: blur(36px) saturate(200%)`, translucent white glass gradient, and luminous border highlights) with deep black typography (`#000000` / `#0f172a`) replacing previous dark/black box backgrounds.
   - Added a dedicated 2-column grid inside the modal form for optional strategic context (`Product / Offering`, `Industry / Vertical`, `Geographic Location`, `Budget / Financials`, `Key Competitors`, `Timeline / Horizon`).
   - Added `prepareMeeting()` to `apps/frontend/src/api.js` accepting and serializing all optional parameters.
   - Updated `MeetingPreparationMessage` to display optional context as stylish glass chips (`📦 Product`, `🏢 Industry`, `🌍 Geo`, `💰 Budget`, `⚔️ Competitors`, `⏱️ Timeline`).
5. **Automated Testing**:
   - Added `tests/test_meeting_preparation_agent.py` covering model serialization, draft parsing, fallback handling, optional fields propagation, and FastAPI endpoint execution.

**Why**
Eliminates manual synthesis for executive meeting prep by chaining multi-agent intelligence and strategic planning into an actionable, tailored brief.

**Alternatives considered**
- Single-prompt end-to-end generation from raw query: rejected because it bypasses the verified multi-source retrieval (RAG + Web + Document) and strict evidence guardrails established in the Market Intelligence and Strategy agents.
- Embedding form fields directly inside the chat input box: rejected because meeting prep requires multi-field inputs (title, objective, attendees) which are better captured in an accessible modal dialog.

**Trade-offs**
Requires sequentially or concurrently executing intelligence retrieval, strategy formulation, and final synthesis, which increases latency (~10–15s in production). This is tracked via `meeting_prep_ms` latency reporting.

**Affected code**
- `packages/agents/src/multimodal_rag/agents/models.py`
- `packages/agents/src/multimodal_rag/agents/meeting_preparation.py`
- `packages/agents/src/multimodal_rag/agents/__init__.py`
- `services/api/src/multimodal_rag/api/schemas.py`
- `services/api/src/multimodal_rag/api/main.py`
- `services/api/src/multimodal_rag/api/router.py`
- `apps/frontend/src/api.js`
- `apps/frontend/src/App.jsx`
- `apps/frontend/src/styles.css`
- `tests/test_meeting_preparation_agent.py`
- `docs/decisions.md`
- `docs/flow.md`

**Verification**
- Automated tests: 187 tests passing (`.venv\Scripts\python.exe -m unittest discover -s tests`).
- Frontend production bundle build verified clean (`npm run build`).

---

## DEC-2026-09-10-04 - Preserve meeting follow-up mode when reopening chats

**Status:** Accepted

**Context**
Meeting follow-ups correctly persist their selected agent, so a Market
Strategy coaching response can become the latest assistant message in a chat.
When a saved chat was reopened, the frontend selected its mode only from that
latest agent. That changed the next meeting follow-up into a fresh
/agents/market-strategy request, which legitimately ran the geography
readiness gate even though the chat already contained a meeting briefing.

**Decision**
During chat rehydration, select meeting_preparation mode whenever the chat
contains any completed or partial meeting-preparation response. This keeps the
next question on the existing /agents/meeting-follow-up route, where the
meeting briefing is recovered and the question is classified as either a
meeting revision or Market Strategy coaching.

**Why**
The presence of a completed meeting briefing is the durable conversation
context; the latest agent identity is not sufficient because coaching replies
are intentionally produced by Market Strategy.

**Alternatives considered**
- Keep using only the latest agent: rejected because it loses the meeting
  workflow after the first strategy coaching reply.
- Add a separate persisted mode field: rejected because existing chat payloads
  already contain enough authoritative meeting context and would require a
  schema/data migration.

**Trade-offs**
A saved chat containing a meeting briefing reopens in meeting mode even when
the latest response is Market Strategy. Users can still explicitly select a
different mode for an independent strategy request.

**Affected code**
- apps/frontend/src/App.jsx::selectChat
- docs/flow.md

**Verification**
The frontend production build and the full Python test suite are run after the
change; the project knowledge graph is reindexed afterward.


## DEC-2026-09-10-05 - Suppress unsolicited market snapshots on meeting follow-ups

**Status:** Accepted

**Context**
The standalone Market Strategy response is intentionally a complete research
snapshot, but meeting follow-ups are focused requests about an existing
briefing. The strategy prompt and response builder were returning market
trends and competitor insights on every strategy turn, even when the user
asked for questions, risks, or explanation.

**Decision**
Keep the full market and competitor snapshot for the first standalone Market
Strategy response. For meeting follow-ups, include it only when the follow-up
explicitly mentions trends, market updates, competitors, competition, or
competitive activity. Otherwise, omit the snapshot from both the generation
instructions and the MarketStrategyResponse fields.

**Why**
This keeps follow-up answers scoped to the requested meeting deliverable and
prevents the renderer from displaying unrelated intelligence sections.

**Alternatives considered**
- Hide the sections only in the frontend: rejected because the model would
  still spend output budget generating them and the API would still expose
  unrelated fields.
- Remove trends and competitors from all strategy responses: rejected because
  they remain required for the initial standalone Market Strategy experience.

**Trade-offs**
Explicit detection uses a small deterministic vocabulary, so unusual wording
may omit the snapshot unless the user uses a clear trend or competitor term.

**Affected code**
- packages/agents/src/multimodal_rag/agents/market_strategy.py
- tests/test_market_strategy_agent.py
- docs/flow.md

**Verification**
The focused follow-up regression test, full Python suite, frontend build, and
knowledge-graph reindex are run after the change.

## DEC-2026-09-10-01 — Route meeting follow-ups by intent

**Status:** Accepted

**Context**
The frontend previously dispatched every message while Meeting Preparation mode was selected to the initial meeting-preparation endpoint. That made follow-up questions regenerate or repeat a briefing without distinguishing a requested briefing change from a request for additional strategic coaching.

**Decision**
Add a focused authenticated meeting-follow-up endpoint to the existing API router. It loads the latest completed meeting briefing from the current user-owned chat, uses MeetingPreparationAgent.classify_follow_up() to choose between modify_meeting and expand_strategy, and returns a typed routing envelope. Modification requests reuse the existing prepare_meeting() orchestration with bounded prior briefing context and a revision instruction. Explanatory requests reuse the existing Market Intelligence and Market Strategy agents with the meeting briefing included as strategy context. The frontend renders the returned agent rather than the selected mode.

**Why**
This preserves the existing initial meeting endpoint and response schemas while making follow-up behavior explicit, tenant-scoped, and compatible with the existing chat persistence and agent instances.

**Alternatives considered**
- Replace the existing router or create a versioned agent: rejected because the change is a routing/orchestration concern and would duplicate stable contracts.
- Always send follow-ups to Market Strategy: rejected because requests to add, correct, or remove briefing content must regenerate the complete meeting kit.
- Route only from frontend keywords: rejected because intent classification belongs at the authenticated backend boundary and must have access to the prior briefing.

**Trade-offs**
Follow-ups add one classification generation call. Strategy-expansion follow-ups run the existing intelligence and strategy pipeline, while modification follow-ups run the existing three-agent meeting pipeline again. The stored prior briefing is bounded to the fields needed for context.

**Affected code**
- services/api/src/multimodal_rag/api/router.py
- packages/agents/src/multimodal_rag/agents/models.py
- packages/agents/src/multimodal_rag/agents/meeting_preparation.py
- packages/agents/src/multimodal_rag/agents/market_strategy.py
- apps/frontend/src/api.js
- apps/frontend/src/App.jsx
- tests/test_meeting_preparation_agent.py
- docs/flow.md

**Verification**
Python compilation, focused meeting-preparation tests, the full Python test suite, and the frontend production build must pass. The project knowledge graph is reindexed after implementation.

---


**Status:** Accepted

**Context**
When the Market Strategy Agent produced a response, it rendered a 3-paragraph executive strategy followed by supporting sources. While `MarketStrategyResponse` already received structured `market_trends` and `competitor_intelligence` from the upstream Market Intelligence research phase, these sections were previously buried inside a collapsed `<details>` drawer ("Market Intelligence Snapshot") under numerous intermediate strategic breakdown cards. Users were unable to readily view what was trending in the market or observe competitor moves in their strategy briefing.

**Decision**
1. **Frontend Presentation (`apps/frontend/src/App.jsx::MarketStrategyMessage`)**:
   - Render dedicated, prominent sections at the last of the Market Strategy response:
     - **What's Trending in the Market** (`message.market_trends`): Renders cards with trend title, confidence badge, description (`what_is_happening`), momentum/significance tags, evidence facts, and strategic importance.
     - **Competitor Insights** (`message.competitor_intelligence`): Renders cards with competitor name, confidence badge, activity change, evidence facts, strategic importance, and source attribution.
   - Streamline the collapsible `<details>` drawer ("View detailed strategic breakdown & additional intelligence") so it retains strategic situation, opportunities, risks, next actions, and assumptions without duplicating trends and competitor sections.
2. **Strategy Prompt Synthesis (`packages/agents/src/multimodal_rag/agents/market_strategy.py::_develop_strategy`)**:
   - Instructed the model prompt to conclude `executive_summary` with concise takeaways:
     - `What's Trending in the Market`: 1–2 sentences synthesizing primary category shifts observed in research.
     - `Competitor Insights`: 1–2 sentences summarizing active competitor movements and strategic actions.
   - Keeps text responses self-contained in raw exports/API calls while the UI provides rich structured cards.
3. **Documentation Updates**:
   - Updated `docs/agent_prompts_guide.md`, `docs/decisions.md`, and `docs/flow.md`.

**Why**
Gives CMOs and strategic planners immediate, actionable visibility into both consumer/category trends and competitor shifts directly alongside their strategic recommendations, eliminating friction and discovery barriers.

**Alternatives considered**
- Expanding the entire `<details>` drawer by default: rejected because it introduces overwhelming clutter (all operational cards, risks, opportunities, channel lists) when users only sought trends and competitor insights.
- Relying solely on raw text bullets in `executive_summary`: rejected because rich structured UI cards (with confidence indicators, momentum badges, and evidence facts) offer superior readability and executive polish.

**Trade-offs**
None; responses are enriched with immediately visible market trends and competitor insights while keeping granular tactical breakdowns cleanly collapsed.

**Affected code**
- `apps/frontend/src/App.jsx::MarketStrategyMessage`
- `packages/agents/src/multimodal_rag/agents/market_strategy.py::_develop_strategy`
- `tests/test_market_strategy_agent.py`
- `docs/agent_prompts_guide.md`
- `docs/decisions.md`
- `docs/flow.md`

**Verification**
- Full test suite: 184 tests passed (`.venv\Scripts\python.exe -m unittest discover -s tests`).
- Frontend production bundle build verified with zero errors (`npm run build`).

---

## DEC-2026-09-08-02 — Document-grounded synthesis with 60/25/15 source weighting in Market Intelligence and Strategy

**Status:** Accepted

**Context**
When users uploaded or attached a document to a conversation, the Market Strategy and Market Intelligence agents answered primarily from internal RAG knowledge base items and external web search, with minimal focus on the user's attached document.
Investigation revealed multiple root causes:
1. `apps/frontend/src/App.jsx` and `apps/frontend/src/api.js` only passed `document_context` to the simple `/answer` RAG endpoint; `analyzeMarketIntelligence()` and `createMarketStrategy()` completely dropped `attachedDoc.text`.
2. `MarketIntelligenceAgent` treated `document_context` as raw supplemental text rather than first-class citable `AgentEvidence`. Consequently, findings could not cite attached document chunks via `evidence_ids`.
3. In `MarketStrategyAgent`, the handoff readiness check (`_assess_readiness()`) lacked access to the attached document text, sometimes requesting clarification on details explicitly stated in the document.
4. Neither agent prompt enforced relative source grounding weights when an attached document was present, allowing general RAG and web search to dominate synthesis.

**Decision**
1. **Frontend Request Propagation (`apps/frontend/src/api.js`, `apps/frontend/src/App.jsx`)**:
   - Extended `createMarketStrategy()` and `analyzeMarketIntelligence()` to accept and serialize `document_context: attachedDoc?.text` in the JSON request payload.
2. **API Router Request Pipeline (`services/api/src/multimodal_rag/api/router.py`)**:
   - Updated `_strategy_research_request()` to pass `document_context=request.document_context` to the underlying Market Intelligence research request.
3. **Structured Document Evidence (`packages/agents/.../models.py`, `market_intelligence.py`)**:
   - Extended `EvidenceKind` with `"document"` (`Literal["rag", "web", "document"]`).
   - In `MarketIntelligenceAgent.run()`, chunk attached document text into `AgentEvidence` items (`kind="document"`, chunk ID `doc:attached_page_1`, `doc:attached_page_2`, etc.) and prepend them directly to `evidence`.
   - In `MarketStrategyAgent.run()`, combine attached document evidence with research sources so strategy recommendations and priorities citing attached document chunks (`doc:attached_...`) pass evidence validation and achieve `completed` status.
4. **Enforced Source Weighting Policy (~60% Document / ~25% Web / ~15% RAG)**:
   - Updated `MarketIntelligenceAgent._analyze()` and `MarketStrategyAgent._develop_strategy()` prompt instructions with an explicit `SOURCE WEIGHTING POLICY`:
     - ~60% primary synthesis anchored in the user's attached document.
     - ~25% fresh external market corroboration via Tavily web search.
     - ~15% internal benchmark and baseline context via internal RAG.
   - Instructed `MarketStrategyAgent._assess_readiness()` that requirements addressed by the attached document must be considered satisfied and not re-clarified.

**Why**
Ensures that whenever a user attaches a document, the platform prioritizes and answers predominantly from that document, while preserving the competitive advantage of real-time web search and internal enterprise benchmarks.

**Alternatives considered**
- Ignoring web search and RAG entirely when a document is attached: rejected because users still require competitive market validation and baseline industry metrics alongside their uploaded deck/brief.
- Treating the attached document as purely an unindexed prompt injection: rejected because findings without valid `evidence_ids` fail strict validation checks and prevent proper source attribution cards in the UI.

**Trade-offs**
Document text is converted into synthetic evidence chunks (`doc:attached_...`), slightly increasing prompt token size for the LLM analysis and strategy generation steps, but guaranteeing grounded citations and reliable synthesis weighting.

**Affected code**
- `apps/frontend/src/api.js`
- `apps/frontend/src/App.jsx`
- `services/api/src/multimodal_rag/api/router.py`
- `packages/agents/src/multimodal_rag/agents/models.py`
- `packages/agents/src/multimodal_rag/agents/market_intelligence.py`
- `packages/agents/src/multimodal_rag/agents/market_strategy.py`
- `tests/test_market_intelligence_agent.py`
- `docs/decisions.md`
- `docs/flow.md`

**Verification**
- Backend test suite: 183 tests passed (`.venv\Scripts\python.exe -m unittest discover -s tests`).
- Frontend production build: verified without errors (`npm run build`).

---

## DEC-2026-09-08-01 — Automatic conversation rehydration on reload and strict empty-chat ID isolation

**Status:** Accepted

**Context**
When users refreshed the browser or reopened a tab during an ongoing conversation, `messages` reset to `[]`, causing the UI to display the empty welcome screen and starter suggestions as if on a new chat. However, `currentChatId` remained initialized to the previously stored chat ID from `localStorage` (`cmo-current-chat-id`). Submitting a query from this ostensibly new conversation sent the prior `chat_id` to `/answer` or `/agents/market-strategy`, causing the backend to load past memories from `chat_memories` and prior turns from `user_chat_messages`. As a result, the new query inherited the previous chat's business context and constraints.

**Decision**
1. **Atomic Session Rehydration (`apps/frontend/src/App.jsx::initSession`)**:
   - On application mount or sign-in (`useEffect([accessToken, userId])`), retrieve the user's past chats via `listChats()`.
   - Inspect `localStorage.getItem("cmo-current-chat-id")`.
   - If `storedChatId` matches an existing conversation, call `selectChat(storedChatId)` to fetch `messages` via `GET /chats/{chat_id}` and atomically populate both `messages` and `currentChatId`. The user sees their active conversation restored rather than a confusing blank screen.
   - If `storedChatId` is absent or does not match any existing conversation (e.g. was deleted or never had messages sent), invoke `startNewChat()`, which clears `messages` and generates a clean, unique `makeId()`.
2. **Initial State Sanitization**:
   - Initialize `currentChatId` with a fresh `makeId()` instead of reading `localStorage` directly into state without corresponding messages, preventing any window where `messages` is empty while `currentChatId` points to an old conversation.
3. **Unified Navigation**:
   - Extracted `selectChat(chatId)` to share conversation loading across sidebar navigation and initial mount.

**Why**
Ensures the UI state and backend chat identity are strictly consistent. If a conversation exists, its messages are visibly restored; if starting fresh, a clean UUID is guaranteed, preventing memory and conversation reuse leaks from prior chats.

**Alternatives considered**
- Unconditionally clearing `localStorage` and starting a fresh chat on every page reload: rejected because users expect page refreshes to preserve their active session messages.
- Relying on the user to manually click "＋ New conversation" after every page refresh: rejected because it is error-prone and violates principle of least surprise.

**Trade-offs**
A brief network fetch (`GET /chats/{chat_id}`) runs on initial mount when rehydrating a saved conversation. If network fails or chat is not found, it gracefully falls back to starting a clean new chat.

**Affected code**
- `apps/frontend/src/App.jsx::selectChat`, `initSession`, `startNewChat`, `loadPastChats`
- `docs/decisions.md`
- `docs/flow.md`

**Verification**
- Production bundle compiled successfully (`npm run build` in `apps/frontend`).
- Backend test suite verified intact (181 tests passed).

---

## DEC-2026-09-07-02 — Synchronous clarification processing and context replenishment in Market Strategy

**Status:** Accepted

**Context**
When the Market Strategy Agent requested clarification (e.g., asking "Which specific geographic market are you targeting?"), user replies (such as "India") caused the agent to loop and prompt the exact same clarifying question again on the first answer, only completing on a second identical response.

Investigation revealed two root causes:
1. `_remember_message` was being scheduled as a FastAPI `background_task`. Because background tasks execute *after* the HTTP response is completed, the user's clarification answer had not yet been extracted or written to PostgreSQL when `_memory_service(request).get_user_context()` and `_assess_readiness()` were called during that turn.
2. The reconstructed `strategy_request` from `pending_state` was restored with the original unclarified objective without incorporating the user's answer into the objective or immediate context profile.
Consequently, `_assess_readiness` saw the exact same empty user context and unclarified objective, determining that "target geography" was still unaddressed and repeating the question. In turn 2, the background task from turn 1 had finally persisted the detail to the database, allowing turn 2 to succeed.

**Decision**
1. **Synchronous Memory Ingestion on Pending Clarification (`services/api/src/multimodal_rag/api/router.py`)**:
   - When `pending_state` is present, `_remember_message` runs synchronously before `get_user_context()` rather than deferred to `background_tasks`.
   - Directly upsert the clarification candidate into `chat_memories` and `chat_profiles` via `MemoryCandidate` for known missing keys (e.g. `target_geography`).
   - Immediately enrich `user_context["profile"]` with canonical aliases (`target_geography`, `target_market`, `geography`).
2. **Objective Enrichment on Resumption**:
   - Update `strategy_request.objective` on resumption with `(Target/Clarification: <answer>)` so both `_assess_readiness` and `_develop_strategy` see the clarified scope in the prompt.
3. **Intent Preservation in Message Classification**:
   - Pass `prior_exchanges_text = f"Clarification requested: {clarif_q}"` to `classify_message` so terse single-word answers (e.g., "India") are properly evaluated in context rather than misclassified as ungrounded chatter.
4. **State Persistence**:
   - Store `clarification_question` in `pending_state` for contextual rehydration.

**Why**
Eliminates race conditions between memory extraction and readiness assessment, ensuring single-turn clarification resolution while preserving asynchronous background processing for standard conversational messages.

**Alternatives considered**
- Running all memory extractions synchronously: rejected because normal conversational extraction incurs extra latency on every turn.
- Relying exclusively on LLM extraction without direct profile injection: rejected because single-word replies like "India" can occasionally fail loose keyword extraction if evaluated without prompt context.

**Trade-offs**
Resuming from clarification pays the slight synchronous latency of memory upsert, but guarantees that the clarification is immediately resolved on turn 1.

**Affected code**
- `services/api/src/multimodal_rag/api/router.py`
- `docs/decisions.md`
- `docs/flow.md`

**Verification**
All 52 unit tests passed (`tests.test_memory_service`, `tests.test_api`, `tests.test_market_strategy_agent`).

---

## DEC-2026-09-07-01 — Make Market Strategy responses concise (3-paragraph executive synthesis and collapsed frontend breakdown)

**Status:** Accepted

**Context**
The Market Strategy Agent previously generated and rendered an exhaustive volume of structured content in the frontend: an open Market Intelligence snapshot with multiple trend sections and competitor cards, strategic situation, separate multi-card priority lists, top opportunity cards, risk cards, channel lists, next actions, assumptions, and full source grids. Users reported that the response was overwhelmingly large and contained too much low-value content for an executive view. The requirement was to distill the visible response into a concise 3-paragraph executive summary focusing exclusively on high-impact insights.

**Decision**
1. **Prompt Refinement (`MarketStrategyAgent._develop_strategy`)**:
   - Instructed the model to format `executive_summary` into exactly 3 focused, decision-ready paragraphs:
     - Paragraph 1: Strategic Situation & Core Market Shift.
     - Paragraph 2: Core Strategic Priorities, Positioning & Channel Focus.
     - Paragraph 3: Primary Opportunity, Main Risk & Immediate Next Steps.
   - Reduced item limits to 2 top opportunities, 2 key risks, and 2 recommended priorities, explicitly instructing the model to omit low-value padding.
2. **Frontend UI Streamlining (`MarketStrategyMessage`)**:
   - The primary response rendered in the chat is now cleanly focused on the 3-paragraph `executive_summary` followed by the verified sources block.
   - All granular multi-card sections (priorities, opportunities, risks, channel notes, market intelligence snapshot) are grouped into a single collapsible `<details>` drawer (`View detailed strategic breakdown & intelligence snapshot`), preventing UI clutter while preserving full auditability.
3. **Documentation Updates**:
   - Updated `docs/agent_prompts_guide.md` with the updated prompt, schema, and guidelines.

**Why**
Delivers an immediate, readable executive summary that respects the user's attention while retaining full evidence traceability and optional access to granular breakdowns on demand.

**Alternatives considered**
- Completely removing the underlying structured findings: rejected because the RAG evidence IDs and source citations depend on structured findings for auditability.
- Truncating text with CSS: rejected because LLMs would still spend tokens generating verbose content that gets hidden.

**Trade-offs**
None; granular findings remain accessible via one click in the collapsed drawer.

**Affected code**
- `packages/agents/src/multimodal_rag/agents/market_strategy.py`
- `apps/frontend/src/App.jsx`
- `docs/agent_prompts_guide.md`

**Verification**
Frontend production bundle verified (`npm run build`). Full Python unit test suite passing (52 tests OK).

---

## DEC-2026-09-04-03 — Remove Market Intelligence Agent from frontend mode selector

**Status:** Accepted

**Context**
The frontend composer controls included a dropdown allowing the user to explicitly select between "RAG Answer", "Market Intelligence Agent", and "Market Strategy Agent". However, Market Intelligence operates as an internal research and evidence-retrieval engine for the Market Strategy Agent (which automatically runs Market Intelligence to retrieve and validate grounded facts before developing actionable strategy). Exposing both separately in the mode dropdown created confusion for users, as the primary executive workflow is Market Strategy or direct document RAG answers.

**Decision**
1. Removed the "Market Intelligence Agent" option from the mode `<select>` in `apps/frontend/src/App.jsx`, retaining "RAG Answer" and "Market Strategy Agent".
2. Removed the conditional `company-context` inputs that were exclusively tied to the standalone `market_intelligence` dropdown mode.
3. Preserved backwards-compatible rendering in the message list (`MarketIntelligenceMessage`) so historic messages generated by the Market Intelligence Agent continue to display cleanly when loading past chat logs.
4. Updated past chat selection logic so previous agent chats cleanly map to "market_strategy".

**Why**
Streamlines the user interface, eliminating duplicate/confusing agent modes while keeping the full underlying Market Intelligence agent intact to serve the Market Strategy agent pipeline.

**Alternatives considered**
- Removing Market Intelligence completely: rejected because Market Strategy strictly depends on Market Intelligence to supply grounded findings and validated citations.

**Trade-offs**
Users can no longer trigger standalone raw intelligence without the strategy synthesis wrapper from the UI dropdown; however, all market intelligence findings, trends, and citations are still displayed inside the Market Strategy response snapshot.

**Affected code**
- `apps/frontend/src/App.jsx`

**Verification**
`npm run build` succeeded; Python unit test suite passed (52 tests OK).

---

## DEC-2026-09-04-02 — Move conversation continuation option into new chat view with past-5 chats picker

**Status:** Accepted

**Context**
In the previous iteration of context branching, the sidebar displayed a "Continue with context" button directly below "New conversation" whenever an existing chat was loaded. This was unintuitive because clicking a previous chat showed the branch button in the sidebar, whereas users starting a *new* chat expected the choice of starting completely clean or continuing from a relevant prior research conversation directly on the new chat screen. Furthermore, users needed to choose which of their past 5 conversations to carry context from, rather than only being able to branch from the currently open chat.

**Decision**
1. **Sidebar simplification**:
   - Removed the `Continue with context` button from underneath `New conversation` in the sidebar.
2. **New Chat Welcome Panel Continuation**:
   - In `apps/frontend/src/App.jsx` when in an empty conversation view (`!hasConversation`), if past conversations exist, render a "Continue with a previous conversation" action.
   - Clicking this option displays an interactive picker showing the user's past 5 conversations (`pastChats.slice(0, 5)`), each displaying the chat title, message count, and last activity date.
3. **Context carryover**:
   - Clicking one of the past 5 chat cards triggers `handleContinueWithChat(chat)`, which invokes `POST /chats/{chat_id}/fork` via `forkChat({ chatId: chat.id, accessToken })`.
   - The newly created branched chat receives the cloned persistent memory and business profile from the selected conversation.
   - The UI displays a context linkage banner (`Continuing with memory and context from "[Chat Title]"`) with a `Start fresh` button allowing the user to unbind context at any point before sending.
   - If the user prefers a clean slate, they simply type in the composer or click a suggested prompt without selecting a prior conversation.

**Why**
Centralizing the continuation decision into the empty chat canvas aligns with standard user mental models: clicking "New conversation" opens the new workspace, where the CMO can immediately decide whether this research session is completely new or an extension of one of their recent 5 research topics.

**Alternatives considered**
- Modal dialog on clicking "New conversation": intrusive and interrupts the fast path of starting a clean chat.
- Unconditional automatic carryover: risks contaminating fresh queries with unrelated past market intelligence.

**Trade-offs**
Requires an extra click if the user wants to branch, but guarantees clean isolation by default and intentional user control.

**Affected code**
- `apps/frontend/src/App.jsx`
- `apps/frontend/src/styles.css`

**Verification**
Frontend production bundle verified (`npm run build`). Backend test suite passing (52 tests OK).

---

## DEC-2026-09-04-01 — Shift persistent memory from user profile to chat scope with Option 1 context branching

**Status:** Accepted

**Context**
Persistent business memory (extracted industry, market, company context, target audience, budget, competitors, KPIs) was previously persisted across the entire user profile in PostgreSQL tables `user_memories` and `user_profiles`, keyed solely by `user_id`. Consequently, facts and constraints stated in one research conversation leaked across all other conversations for that user. Opening a new chat did not provide a clean slate, and researching distinct industries/products produced cross-polluted context and misaligned strategy recommendations.

**Decision**
1. **Shift all memory persistence from user profile to chat scope.**
   - Created PostgreSQL tables `chat_memories` and `chat_profiles` parameterized by `(user_id, chat_id)`.
   - Purged legacy persistent memory data (`user_memories` and `user_profiles`) in PostgreSQL while completely preserving accounts, sessions, chats, and chat message history (`users`, `user_sessions`, `user_chats`, `user_chat_messages`).
2. **Strict Chat Isolation by default.**
   - `MemoryRepository` and `PostgresMemoryRepository` take `(user_id, chat_id)`.
   - `MemoryService.process_message(user_id, chat_id, message)` requires a valid `chat_id` to store extracted facts; calls lacking `chat_id` skip persistence.
   - `MemoryService.get_user_context(user_id, chat_id)` returns an empty profile and business context when `chat_id` is None or for any newly created chat. Existing chats load only their own specific memory.
   - `services/api/src/multimodal_rag/api/router.py` threads `payload.chat_id` through `_remember_message` across `/agents/market-intelligence`, `/agents/market-strategy`, and `/answer`.
3. **Option 1 Context Branching for CMO Research Continuity.**
   - Added `POST /chats/{chat_id}/fork` endpoint in `router.py`.
   - Added `clone_chat_memory` on `PostgresMemoryRepository` and `clone_context` on `MemoryService` to duplicate the established memory and profile from a parent chat to a newly created branch chat on-demand.
   - Added `forkChat` API helper in `apps/frontend/src/api.js` and a "Continue with context" action in `apps/frontend/src/App.jsx`.
   - Updated `delete_chat` to clean up `chat_memories` and `chat_profiles` when a conversation is deleted.

**Why**
Scoping persistent memory to `(user_id, chat_id)` eliminates unexpected cross-topic contamination when switching tasks or starting new research, while the explicit branching mechanism allows CMOs to intentionally fork multi-session campaigns without arbitrary countdown heuristics or context bleed.

**Alternatives considered**
- Unconditional 5-chat countdown carryover: rejected because an arbitrary counter contaminates unrelated topics when the user pivots to new research and causes hallucinations.
- Project-only hierarchy: deferred to future enterprise scope expansion; explicit branching gives immediate user control with minimal UI complexity.

**Trade-offs**
A newly created chat will not know previous company details unless explicitly branched using "Continue with context" or stated again in the new conversation.

**Affected code**
- `services/api/migrations/001_user_memory.sql`
- `packages/agents/src/multimodal_rag/memory/repository.py::MemoryRepository`, `PostgresMemoryRepository`
- `packages/agents/src/multimodal_rag/memory/service.py::MemoryService`
- `services/api/src/multimodal_rag/api/accounts.py::PostgresUserStore.create_chat`, `delete_chat`
- `services/api/src/multimodal_rag/api/router.py::_remember_message`, `create_market_strategy`, `fork_chat`
- `apps/frontend/src/api.js::forkChat`
- `apps/frontend/src/App.jsx`
- `tests/test_memory_service.py`
- `tests/test_api.py`
- `tests/test_market_strategy_agent.py`

**Verification**
1. Ran database migration and verification: confirmed `user_memories` and `user_profiles` purged; `users` (1), `user_chats` (10), `user_chat_messages` (43) intact; `chat_memories` (0) and `chat_profiles` (0) initialized.
2. Ran unit test suite: 52 tests passed (`tests.test_memory_service`, `tests.test_market_strategy_agent`, `tests.test_api`), including chat isolation tests and fork endpoint tests.

---

## DEC-2026-09-02-01 — Conversation-aware reuse: repeat/refinement/follow-up detection across `/answer`, Market Intelligence, and Market Strategy

**Status:** Accepted

**Context**
Every request to `/answer`, `/agents/market-intelligence`, and `/agents/market-strategy` was fully stateless with respect to its own chat. Asking the exact same question twice re-ran retrieval, web search, and generation from scratch (and could return differently-worded answers); a question that only changed one facet ("...but for India?") re-researched the entire topic; and a genuine follow-up ("explain that further") got an answer with no memory of what "that" referred to. Investigation found the follow-up plumbing already existed and was simply never wired up: `ConversationTurn` → `build_prompt(conversation_history=...)` → `run_rag_trace(conversation_history=...)` was fully implemented in `rag/generation/prompt_builder.py` and `rag/trace.py`, including the `FOLLOW_UP_INSTRUCTIONS` system-prompt addendum, but `RAGService.answer()` never passed `conversation_history`, and `router.py`'s `/answer` handler stripped `chat_id` before calling the service.

**Decision**
1. New module `services/api/src/multimodal_rag/api/conversation_reuse.py` holds all the shared, reusable pieces: `eligible_exchanges()` (filters stored chat messages down to safe reuse candidates - matching agent, no declines/clarifications/errors, non-empty Q and A), `exact_repeat()` (a cheap SHA-256 normalized-text hash comparison, reusing `ingestion/output/deduplicator.py::normalized_hash` - the same fingerprint technique already used for ingestion dedup), `format_exchanges()`/`to_conversation_turns()` (prompt/history adapters), and `ConversationReuseClassifier` (one small LLM call, used only by `/answer`).
2. **Exact repeats replay verbatim.** `PostgresUserStore.get_recent_exchanges(user_id, chat_id, limit=10)` reads the last `limit*10` chat rows (bounded, served by the existing `(user_id, chat_id, id)` index) and pairs them into (question, assistant-payload) tuples in Python - not SQL - because a mid-request failure can leave an orphaned user row (the user message is recorded before the answer is generated), and a SQL window/LAG join would silently mispair across that gap. A hash hit on the newest matching exchange short-circuits the whole route: the stored payload is replayed with a new `trace_id` and a `reused_from_message_id` marker, with zero retrieval, web search, or LLM calls.
3. **Near-repeats research only the delta.** For `/answer`, a semantic (non-hash) classification step (`ConversationReuseClassifier`, one new LLM call, only invoked when there is at least one eligible prior exchange and the hash check missed) labels the new question `repeat | refinement | follow_up | new` and extracts a standalone delta query plus shared context. `refinement`/`follow_up` pass a narrowed `retrieval_question` into `RAGService.answer()` while the ORIGINAL question still reaches the LLM (via the new `prompt_question` param threaded through `run_rag_trace()`) alongside `conversation_history` built from the matched prior turn(s) - this is what finally activates the dormant follow-up plumbing. For the two agents, the SAME classification is folded into their EXISTING first LLM call instead of paying for a second one: `MarketIntelligenceAgent._plan()` gained `relation`/`prior_turn`/`shared_context`/`delta` fields (only populated when `additional_context["prior_exchanges"]` is present) and an instruction that `search_queries` must cover only the delta on a refinement; `MarketStrategyAgent.classify_message()` (already a separate per-request gate added for off-topic detection) gained an optional `prior_exchanges` param to correctly classify terse continuation replies as on-topic.
4. **Evidence carries over.** The router passes the most recent exchange's `sources` into `additional_context["prior_sources"]`; `MarketIntelligenceAgent.run()` validates and merges them (`prior + fresh`, prior FIRST) through the EXISTING `_deduplicate()` staticmethod before `_with_recency()` re-stamps recency against the current window. Merging prior-first means the surviving copy keeps the prior turn's `chunk_id`, so a carried-over finding is still citable by an ID the user already saw.
5. **The pending-clarification path in Market Strategy always wins over reuse.** Reuse detection (`exchanges`, exact-repeat, and the `prior_exchanges` passed to `classify_message`) is computed only when there is no pending `strategy_state` - a clarification answer like "India" is not a reuse candidate, and scoring it against the window would mis-bucket it. The existing on-topic/greeting gate still runs unconditionally on every message, before pending-state is touched, so an off-topic aside sent mid-clarification is declined without discarding or answering the pending question.
6. Every new field on `ResearchPlanDraft`/`MessageRelevanceDraft`/`ReuseDecision` defaults to the "proceed exactly as before" value (`on_topic: bool = True` was already this pattern from a prior change), and the router only adds `conversation_history`/`retrieval_question` kwargs to the service call when they are non-empty - so a chat with no reuse-eligible history produces byte-identical behavior and byte-identical service call kwargs to before this change.

**Why**
Users explicitly reported (a) repeating a question producing a fresh, sometimes-different answer at full retrieval/web-search cost, and (b) follow-up questions being answered as if the prior turn never happened. Folding classification into each agent's existing first LLM call (rather than adding a dedicated call per agent) was chosen specifically to avoid a second LLM round-trip per agent request and to avoid breaking the agents' existing sequential-fake-generator test fixtures (`queued_generator`/hand-counted `iter([...])` lists) - both agent test files pass unmodified. `/answer` genuinely needs its own classifier call because `run_rag_trace()` only calls the LLM once, AFTER retrieval, so the reuse decision has nowhere else to piggyback.

**Alternatives considered**
- Embedding-similarity-based repeat detection (e.g. cosine similarity between question embeddings): rejected for the exact-match case - a cheap SHA-256 normalized-text hash is free and cannot be wrong for whitespace/case-only differences, while an embedding call costs API quota for a check a hash already answers deterministically.
- A single shared classification endpoint/service called by all three routes: rejected in favor of folding into each agent's existing call, specifically to avoid adding LLM round-trips to the two agents and to keep the existing test fixtures intact.
- SQL-side window function (`LAG`) to pair user/assistant rows for `get_recent_exchanges`: rejected because a request that fails after recording the user message but before generating an answer leaves an orphaned user row, which a SQL-side pairing would silently misalign with a later message; Python-side sequential pairing tolerates orphans and consecutive same-role rows.
- Verbatim semantic replay for Market Intelligence (treating an LLM-judged, non-hash "repeat" as a full replay rather than a zero-search refinement): deferred - would need a third agent-level LLM call or a lower-confidence hash-only heuristic; the chosen approach (treat semantic repeat as a refinement with empty `search_queries`) already avoids the web-search cost, which is the expensive part.

**Trade-offs**
A verbatim replay does not check whether new documents were ingested since the cached answer (per product decision, made explicit here rather than silently accepted: a repeat always replays regardless of intervening ingestion). Cross-mode contamination is prevented by filtering `eligible_exchanges()` on the stored payload's `agent` key, but this depends on every future response payload continuing to carry the same `agent`/`status`/`error_code` shape - a schema change there needs to keep `eligible_exchanges()` in mind. The reuse window (last 10 responses) and the prompt-render slice (last 3-5 turns) are deliberately different numbers so they never interact surprisingly.

**Affected code**
- `services/api/src/multimodal_rag/api/conversation_reuse.py` (new)
- `services/api/src/multimodal_rag/api/accounts.py::PostgresUserStore.get_recent_exchanges()`
- `services/api/src/multimodal_rag/api/router.py::answer_question()`, `analyze_market_intelligence()`, `create_market_strategy()`, `_recent_exchanges()`, `_reuse_classifier()`
- `services/api/src/multimodal_rag/api/service.py::RAGService.answer()`, `_trace_payload()`
- `services/api/src/multimodal_rag/api/main.py::create_app()` (new `reuse_classifier` param/`app.state.conversation_reuse_classifier`)
- `services/api/src/multimodal_rag/api/schemas.py::AnswerResponse.reused_from_message_id`
- `packages/rag-core/src/multimodal_rag/rag/trace.py::run_rag_trace()` (`prompt_question` param), `RAGTrace.retrieval_question`
- `packages/agents/src/multimodal_rag/agents/market_intelligence.py::ResearchPlanDraft`, `_plan()`, `_analyze()`, `run()` (prior-evidence merge)
- `packages/agents/src/multimodal_rag/agents/market_strategy.py::MessageRelevanceDraft`, `classify_message()`
- `packages/agents/src/multimodal_rag/agents/models.py::MarketIntelligenceResponse.reused_from_message_id`, `MarketStrategyResponse.reused_from_message_id`
- `apps/frontend/src/App.jsx` (reused-answer badge)

**Verification**
Full suite (167 tests, 1 pre-existing skip) passes, including 9 new tests covering: exact-repeat replay without calling the service/agent for all three routes, follow-up `conversation_history` reaching the service, graceful degradation when the configured chat store lacks `get_recent_exchanges`, prior-evidence merge/dedup keeping the prior `chunk_id`, the `_plan` prompt including prior exchanges when supplied, and the pending-clarification-wins-over-reuse ordering in Market Strategy. Every pre-existing test in `test_api.py`, `test_market_intelligence_agent.py`, and `test_market_strategy_agent.py` passes unmodified except two hand-counted generator-response lists that needed one extra queued response inserted for the (pre-existing, unrelated) `classify_message` gate call.

---

## DEC-2026-08-31-02 — Stop tests from writing a real `tenant-data/` directory into the repo root

**Status:** Accepted

**Context**
Several API test files constructed `APISettings(user_data_root=Path("tenant-data"), ...)` using a bare relative path. `test_api.py::test_pdf_upload_creates_scoped_ingestion_job` (and similar upload tests) exercise the real `/ingest` upload-saving code in `router.py`, which resolves that relative path against the process's working directory — so running the suite from the repo root left a real `tenant-data/alice/...` directory behind after every run, already worked around by a `.gitignore` entry rather than fixed at the source.

**Decision**
`test_api.py`'s `APITest.setUp()` now creates a `tempfile.TemporaryDirectory()` per test (cleaned up via `addCleanup`) and uses it as `user_data_root` everywhere in that class, including the two path-assertion tests that previously hardcoded `Path("tenant-data/...")` string literals. `test_company_research.py`, `test_market_strategy_agent.py`, and `test_market_intelligence_agent.py` never actually perform file I/O through `user_data_root` (their routes don't reach ingestion), but were switched from the bare relative path to a module-level `Path(tempfile.gettempdir()) / "cmo-rag-tests-tenant-data"` constant as a defensive measure so a future test addition can't accidentally start writing into the repo either.

**Why**
A relative path passed to a dataclass is not itself wrong, but any code that later joins and writes to it resolves it against whatever the current working directory happens to be — the repo root, in the normal case of running `pytest`/`unittest` from there. Tests that intentionally exercise real upload-saving code need a real, but disposable and cleaned-up, filesystem location; tests that never touch disk are cheapest to just point somewhere outside the repo entirely.

**Trade-offs**
None of substance — this only affects test fixtures. The OS-temp-based path used by the three "never write" files is a fixed constant rather than a fresh directory per test, but since nothing ever writes there, no cleanup or isolation between test runs is needed.

**Affected code**
- `tests/test_api.py`
- `tests/test_company_research.py`
- `tests/test_market_strategy_agent.py`
- `tests/test_market_intelligence_agent.py`

**Verification**
Full suite passes: 155 tests, 1 expected skip. Ran the suite twice in a row from the repo root and confirmed no `tenant-data/` directory exists afterward either time.

---

## DEC-2026-08-31-01 — Make Chroma the sole source of truth for retrieval; move tenant storage out of runtime-data

**Status:** Accepted

**Context**
FAISS had already been fully replaced by ChromaDB (DEC-2026-08-26-02), but the retrieval path still re-read `chunks.json` off disk on every `/retrieve` and `/answer` request (`rag/trace.py::load_chunk_metadata()`/`load_chunk_texts()`, called from `api/service.py`), to resolve parent-section context and full chunk metadata that were never stored in Chroma itself — only child chunk_id/document_id/source_file/page_numbers/section_title were. Separately, the per-tenant ingestion and index artifacts for API/frontend-uploaded documents lived under `runtime-data/users/<user_id>/...`, the same root used for CLI/evaluation global artifacts and logs, with no separation between "durable tenant data" and "local dev/eval scratch data."

**Decision**
1. At embedding time, `embedder.py::embed_chunks()` now resolves each child chunk's `parent_chunk_text` from the same in-memory `chunks.json` record set (parents are skipped for embedding, not discarded) and carries the child's complete `ChunkMetadata` dict alongside it. `write_embeddings()` persists both into `embeddings_metadata.json`; `chroma_index.py::build_index_from_output_dir()`/`save_index()` write them onto each child's Chroma record as `parent_chunk_text` and a JSON-encoded `metadata_json` field; `load_index()` reads them back onto `IndexedChunkRef`. `retriever_2.py::RetrievedChunk` carries the same two fields through retrieval (hybrid, keyword-only, and non-hybrid paths).
2. `trace.py::expand_parent_context()` now takes only the retrieved chunks and reads `chunk.parent_chunk_text` directly — no external maps, no disk read. `run_rag_trace()` builds its debug `metadata_by_id` from each retrieved chunk's own `.metadata` when the caller does not supply an override, instead of calling `load_chunk_metadata()` by default. `api/service.py::RAGService.retrieve()`/`.answer()` no longer read `chunks.json` at all. `load_chunk_texts()` had zero remaining callers after this change and was deleted; `load_chunk_metadata()` is kept as it is still used explicitly by `evaluation/question_runner.py`'s developer CLI trace.
3. `paths.py` adds `USER_DATA_ROOT_DEFAULT` (`<project root>/user-data`), and `api/config.py::APISettings.from_environment()` defaults `user_data_root` to it instead of `runtime-data/users`. `RAG_USER_DATA_ROOT` still overrides it. `runtime-data/` is now exclusively the CLI/evaluation global corpus and logs.

**Why**
Chroma already stores document text and metadata per record; duplicating chunk metadata in loose JSON files and re-reading all of them on every request was redundant I/O and an unnecessary runtime dependency on the filesystem layout. Storing parent text and full metadata directly on the child's Chroma record makes Chroma the single source of truth for everything retrieval needs, and separating tenant storage from the CLI/eval scratch root makes the durable, per-user data boundary explicit instead of incidental.

**Alternatives considered**
- Keep disk-based metadata but just relocate the directory: fixes the location but not the actual ask — retrieval would still perform redundant disk I/O and depend on `chunks.json` staying in sync with Chroma.
- Move to a remote Chroma server: unnecessary operational overhead for the current scale; a local `PersistentClient` pointed outside `runtime-data` satisfies the actual requirement (no code reads/writes `runtime-data` for tenant data) without standing up new infrastructure.
- Drop raw extraction/audit files from disk entirely: rejected — they remain valuable ingestion-time debugging output (`validation_report.json`, `human_readable_extraction.md`, etc.) and are never read back by retrieval, so keeping them costs nothing at query time.

**Trade-offs**
Existing Chroma collections built before this change lack `parent_chunk_text`/`metadata_json` and fall back to `None`/`{}` (handled by `.get()` defaults in `load_index()`) until their documents are re-ingested. Content previously under `runtime-data/users/` is not migrated automatically and is now orphaned relative to the new default `user_data_root`; affected users must re-upload/re-ingest under the new location. `question_runner.py`'s CLI/evaluation trace still explicitly loads metadata via the retained `load_chunk_metadata()` disk-scanning path (an intentional override kept for that developer tool, unrelated to the frontend/API path).

**Affected code**
- `packages/rag-core/src/multimodal_rag/rag/embedding/embedder.py::EmbeddedChunk`, `embed_chunks()`, `write_embeddings()`
- `packages/rag-core/src/multimodal_rag/rag/indexing/chroma_index.py::IndexedChunkRef`, `build_index_from_output_dir()`, `save_index()`, `load_index()`
- `packages/rag-core/src/multimodal_rag/rag/retrieval/retriever_2.py::RetrievedChunk`, `retrieve()`
- `packages/rag-core/src/multimodal_rag/rag/trace.py::expand_parent_context()`, `run_rag_trace()`
- `packages/rag-core/src/multimodal_rag/paths.py::USER_DATA_ROOT_DEFAULT`
- `services/api/src/multimodal_rag/api/config.py::APISettings.from_environment()`
- `services/api/src/multimodal_rag/api/service.py::RAGService.retrieve()`, `.answer()`
- `tests/test_rag_trace.py`
- `.gitignore`

**Verification**
Full offline suite passes: 155 tests, 1 expected skip (local ChromaDB artifacts unavailable). Confirmed `APISettings.from_environment()` now resolves `user_data_root` to `<project root>/user-data`, outside `runtime-data`, with no directories created as a side effect of import (consistent with `paths.py`'s existing side-effect-free contract).

---

## DEC-2026-08-30-12 — Use one readable typography scale for Market Strategy content

**Status:** Accepted

**Context**
Market Strategy responses combine summaries, list-based intelligence, cards, recommendations, collapsible notes, and source sections. Their shared styles assigned different sizes from 11px through 16px, while some unscoped paragraphs used browser defaults, making one response visually uneven.

**Decision**
Add a `market-strategy-message` root class and scope a single 14px/1.65 body-text scale to its substantive headings, paragraphs, lists, and note controls. Use the interface's near-black foreground for strategy list content, including intelligence cards, positioning, channel direction, next actions, and assumptions. Keep compact operational metadata such as agent labels, priority badges, and source metadata smaller so it remains visually secondary.

**Why**
Scoped rules make the complete strategy answer consistently readable without changing Market Intelligence, normal RAG answers, backend response models, or evidence behavior. Weight, color, and casing continue to communicate hierarchy even where content uses the same size.

**Alternatives considered**
- Change the global trend and answer styles: rejected because it would alter every agent response and unrelated workspaces.
- Assign a separate size to every strategy section: rejected because it would preserve the inconsistency and increase maintenance cost.

**Trade-offs**
Long strategy answers use slightly more vertical space because list and note text is no longer rendered at 10–11px.

**Affected code**
- `apps/frontend/src/App.jsx::MarketStrategyMessage`
- `apps/frontend/src/styles.css`

**Verification**
The Vite production build completes successfully after applying the scoped strategy typography and list-contrast rules.

---

## DEC-2026-08-30-11 — Normalize malformed optional strategy items independently

**Status:** Accepted

**Context**
The generation model can occasionally return plain strings in evidence-linked fields such as `key_risks`. Pydantic previously rejected the complete `StrategyDraft`, causing a request failure even when the remaining priorities were valid and Market Intelligence already supplied validated risks.

**Decision**
Normalize each optional strategy finding independently before validating the complete draft. Retain only dictionaries that satisfy the typed finding model, evidence-ID contract, priority enum, and horizon enum. Discard malformed entries while preserving valid findings and the directly copied Market Intelligence snapshot.

**Why**
One malformed optional model item should not remove a valid evidence-backed response. Converting uncited strings into findings would weaken provenance, so invalid items are omitted rather than guessed or promoted.

**Alternatives considered**
- Fail the complete response: rejected because it caused the reported avoidable request failure.
- Convert strings into risk objects: rejected because strings contain no validated observation, implication, recommendation, or evidence IDs.

**Trade-offs**
Malformed generated strategy items are silently absent from strategic findings, but the corresponding validated Market Intelligence categories remain visible in the response.

**Affected code**
- `packages/agents/src/multimodal_rag/agents/market_strategy.py::_normalize_strategy_payload`
- `tests/test_market_strategy_agent.py`

**Verification**
Focused Market Strategy tests pass, including the reported plain-string `key_risks` shape. The response remains completed, drops the invalid risk strings, and retains a valid evidence-linked priority.

---

## DEC-2026-08-30-10 — Include a compact intelligence snapshot in strategy responses

**Status:** Accepted

**Context**
Market Strategy consumed the complete Market Intelligence response internally but returned only strategic sections and sources. Users could not see the supporting trends, competitor activity, market opportunities, or market risks in the combined Strategy answer.

**Decision**
Add the validated Market Intelligence summary, resolved scope, trends, competitors, opportunities, risks, and takeaways to `MarketStrategyResponse`. Copy these objects directly from `MarketIntelligenceResponse`; do not regenerate them. Render the categories as compact lists before the strategic recommendations and keep the longer intelligence summary collapsed.

**Why**
The combined response now provides research context and action in one place while preserving the Market Intelligence ownership boundary and keeping the default view concise.

**Alternatives considered**
- Ask the strategy model to summarize intelligence again: rejected because it could alter or omit validated findings.
- Show only source cards: rejected because provenance alone does not expose the intelligence categories the user requested.

**Trade-offs**
The Strategy API response is larger and may repeat information visible in a separate Market Intelligence answer. The UI limits visual duplication through compact category lists and a collapsed full summary.

**Affected code**
- `packages/agents/src/multimodal_rag/agents/models.py::MarketStrategyResponse`
- `packages/agents/src/multimodal_rag/agents/market_strategy.py::_response`
- `apps/frontend/src/App.jsx::MarketStrategyMessage`

**Verification**
Focused Market Strategy and Market Intelligence tests pass, Python compilation succeeds, and the Vite production build succeeds. Regression assertions confirm Strategy returns the exact competitor, opportunity, and summary objects supplied by Market Intelligence.

---

## DEC-2026-08-30-09 — Let approved Market Intelligence evidence satisfy the strategy gate

**Status:** Accepted

**Context**
Market Intelligence can return approved RAG or web sources and a useful summary while its stricter structured-finding normalization removes every trend, competitor, opportunity, and risk draft. Market Strategy incorrectly treated that valid partial response as having no evidence.

**Decision**
Allow Strategy readiness and generation whenever `MarketIntelligenceResponse.sources` contains at least one approved source. Continue blocking strategy when Market Intelligence supplies no approved sources. Recommendation evidence IDs remain validated against those sources.

**Why**
The research boundary is the Market Intelligence response and its approved provenance, not the presence of an optional normalized finding category. This avoids a false failure without allowing Strategy to search independently or produce uncited findings.

**Alternatives considered**
- Require a normalized Market Intelligence finding: rejected because valid evidence was already available and caused the reported false failure.
- Remove the evidence gate entirely: rejected because Strategy must not run without Market Intelligence evidence.

**Trade-offs**
Strategy may perform more interpretation of MI-owned source excerpts when structured intelligence categories are empty, but every accepted recommendation must still cite a source ID supplied by Market Intelligence.

**Affected code**
- `packages/agents/src/multimodal_rag/agents/market_strategy.py::_has_supported_intelligence`
- `tests/test_market_strategy_agent.py`

**Verification**
Focused Market Strategy and Market Intelligence tests pass, including a partial intelligence response with approved sources but no structured findings and a no-source response that remains blocked.

---

## DEC-2026-08-30-08 — Make Market Intelligence the sole strategy research boundary

**Status:** Accepted

**Context**
Market Strategy assessed missing context before Market Intelligence ran, accepted temporary company fields outside stored memory, and could not resume an original objective after a short clarification reply. Although it had no direct RAG or web client, it still owned the Market Intelligence invocation and the execution order did not match the required intelligence-first workflow.

**Decision**
The API now coordinates Market Intelligence before Market Strategy. `MarketStrategyAgent` accepts only a strict strategy request, a completed `MarketIntelligenceResponse`, and stored user context. Temporary personalization fields are forbidden. Pending clarification state stores the original request and intelligence snapshot on the user-scoped chat so follow-up answers update memory and resume without repeated research.

**Why**
This makes the research ownership boundary enforceable in code, lets readiness use discovered market and competitor facts, preserves the original objective across clarification turns, and prevents unsaved form fields from changing personalized strategy.

**Alternatives considered**
- Keep Market Intelligence inside `MarketStrategyAgent`: rejected because Strategy would still own research orchestration.
- Re-run research after every clarification: rejected because it duplicates cost and can change the evidence mid-task.
- Pass the original objective back from the browser: rejected because continuation integrity and tenant scoping belong on the server.

**Trade-offs**
Clarification continuation requires configured PostgreSQL chat and memory persistence. Intelligence snapshots increase the size of `user_chats.strategy_state`, but are cleared after completion or failure. Strategy endpoint clients can no longer send temporary company, industry, geography, or arbitrary context fields.

**Affected code**
- `packages/agents/src/multimodal_rag/agents/market_strategy.py`
- `services/api/src/multimodal_rag/api/router.py`
- `services/api/src/multimodal_rag/api/accounts.py`
- `apps/frontend/src/App.jsx`

**Verification**
The complete Python suite passes 152 tests with one expected local-ChromaDB skip. Python compilation and the Vite production build pass. Focused tests confirm intelligence-first readiness, strict request validation, evidence validation, original-objective restoration, stored-context use, and single-run Market Intelligence reuse. A fresh full knowledge-graph index contains 1,901 nodes and 7,808 edges and exposes `MarketStrategyAgent`, `create_market_strategy`, and the strategy-state persistence methods.

---

## DEC-2026-08-30-07 — Align active web security, retrieval tests, and evaluator dependencies

**Status:** Accepted

**Decision**
Use the shared fail-closed `SourceGuardService` for both Market Intelligence and Tavily Research. Update obsolete URLhaus tests to assert the active VirusTotal contract, retain the documented 40-result hybrid candidate pool and reciprocal-rank-fusion score semantics in regression tests, and declare the pinned RAGAS evaluation dependencies.

**Why**
Every external web-evidence path needs the same URL and content checks. Tests must validate the active retrieval design rather than demand an older algorithm, and the offline evaluator must be reproducibly installable.

**Trade-offs**
Tavily Research now rejects reports without source URLs and may return only safety-approved citations. The evaluation dependency group is substantial but remains outside live request handling.

**Affected code**
- `packages/web-search/src/multimodal_rag/web_search/research.py`
- `services/api/src/multimodal_rag/api/main.py`
- `tests/test_source_guard.py`
- `tests/test_rag_trace.py`
- `requirements.txt`

**Verification**
Focused Source Guard, company-research, retrieval, and evaluator tests pass. The complete suite passes: 149 tests, with one expected skip when local ChromaDB artifacts are unavailable. `pip check` reports no broken requirements.

---

## DEC-2026-08-30-06 — Gate market strategy on context readiness

**Status:** Superseded by DEC-2026-08-30-08

**Context**
Strategic recommendations need company-specific context, but requiring a fixed questionnaire would delay useful work and invite the system to invent missing business facts.

**Decision**
Add a `MarketStrategyAgent` that first assesses the supplied request and persisted user business context. It returns one highest-value clarification when critical context is missing. When ready, it invokes the existing `MarketIntelligenceAgent` and generates evidence-linked strategic opportunities, risks, priorities, and next actions.

**Why**
The decision gate uses known memory before asking the user, avoids unnecessary research while context is insufficient, and keeps research collection owned by the existing Market Intelligence Agent.

**Alternatives considered**
- Always generate a generic strategy: rejected because it would conceal missing company facts.
- Require every context field up front: rejected because relevance depends on the objective.
- Put strategic recommendation logic into Market Intelligence: rejected because it mixes research collection with decision support.

**Trade-offs**
The ready path performs an additional generation step, while the incomplete path intentionally pauses for a CMO answer. Strategy output is limited to evidence returned by Market Intelligence and known user context.

**Affected code**
- `packages/agents/src/multimodal_rag/agents/market_strategy.py`
- `packages/agents/src/multimodal_rag/agents/models.py`
- `services/api/src/multimodal_rag/api/main.py`
- `services/api/src/multimodal_rag/api/router.py`
- `apps/frontend/src/App.jsx`
- `apps/frontend/src/api.js`

**Verification**
Focused agent and authenticated-route tests, Python compilation, and the frontend production build.

---

## DEC-2026-08-30-05 â€” Use a collapsible left drawer for saved chats

**Status:** Accepted

**Decision**
Keep the existing saved-chat API calls and render their results in a menu-controlled left sidebar rather than the top navigation.

**Why**
This preserves the current frontend data behavior while making long chat histories accessible without crowding the primary workspace.

**Affected code**
- `apps/frontend/src/App.jsx::App`
- `apps/frontend/src/styles.css`

**Verification**
Frontend production build completes successfully.

---

## DEC-2026-08-30-04 â€” Display Market Intelligence references

**Status:** Accepted

**Decision**
Render the response-level `MarketIntelligenceResponse.sources` collection with the existing frontend `SourceCard` component.

**Why**
The API already returns RAG document provenance and approved web URLs, but the Market Intelligence view previously showed sources only within selected findings.

**Affected code**
- `apps/frontend/src/App.jsx::MarketIntelligenceMessage`

**Verification**
`npm.cmd run build` completes successfully.

---

## DEC-2026-08-30-03 â€” Use VirusTotal v3 for Source Guard URL reputation

**Status:** Accepted

**Decision**
Replace URLhaus with VirusTotal v3 URL lookups. A URL-safe, unpadded Base64 identifier is generated locally; only URLs with analysis counts at or below configurable malicious and suspicious thresholds proceed to Check Point AI Guardrails. Unknown URLs, rate limits, malformed responses, and service failures fail closed.

**Why**
VirusTotal provides verdict counts for configurable policy decisions while preserving the established Source Guard and agent boundaries.

**Affected code**
- `packages/web-search/src/multimodal_rag/web_search/source_guard.py`
- `services/api/src/multimodal_rag/api/config.py`
- `services/api/src/multimodal_rag/api/main.py`
- `tests/test_virustotal_source_guard.py`

**Verification**
VirusTotal-focused mocked tests pass and modified modules compile.

---

## DEC-2026-08-30-02 â€” Replace Web Risk URL screening with URLhaus

**Status:** Accepted

**Context**
The Source Guard needed a known-malware URL reputation check that does not require Google Cloud billing, while retaining the existing content-level Check Point AI Guardrails check.

**Decision**
Replace the Web Risk request, configuration, response parsing, metadata, and tests with URLhaus. `SourceGuardService.validate_url()` posts each validated external URL to the URLhaus lookup API using `URLHAUS_AUTH_KEY`; `query_status=no_results` proceeds to AI Guardrails, while a recognized URL or unknown/error response blocks the result.

**Why**
URLhaus is purpose-built for malware-distribution URLs and preserves the existing Source Guard, provider, and agent responsibility boundaries. URLhaus absence is deliberately not treated as trust: AI Guardrails still screens every URLhaus-negative result as untrusted tool content.

**Alternatives considered**
- Keep Web Risk: requires a billed Google Cloud project for this use case.
- Move URL validation into the agent: would mix security policy with market analysis.
- Treat an unavailable or unrecognized URLhaus response as safe: violates fail-closed handling.

**Trade-offs**
URLhaus covers known malware-related URLs rather than general reputation or phishing coverage. Results not known to URLhaus may still be unsafe, so each one incurs the unchanged AI Guardrails check.

**Affected code**
- `packages/web-search/src/multimodal_rag/web_search/source_guard.py::SourceGuardService.validate_url`
- `services/api/src/multimodal_rag/api/config.py::APISettings`
- `services/api/src/multimodal_rag/api/main.py::create_app`
- `tests/test_source_guard.py`
- `README.md`

**Verification**
Twenty-nine focused Source Guard, Market Intelligence, and web-search tests pass with URLhaus and Check Point HTTP calls mocked. Modified modules compile with the project virtual environment.

---

## DEC-2026-08-30-01 â€” Fail closed on untrusted web-search evidence

**Status:** Accepted

**Context**
Market Intelligence previously converted every Tavily result directly into agent evidence. External URLs and returned content can contain malicious destinations or prompt-injection instructions.

**Decision**
Create `SourceGuardService` in the web-search component and inject it at the Market Intelligence boundary. For each result, URLhaus validates known malware URLs before Check Point AI Guardrails validates its content as a `tool` message. Only results that pass both checks become `AgentEvidence`; malformed URLs, missing credentials, invalid service responses, and network/API failures are blocked.

**Why**
This preserves separate responsibilities: Tavily fetches, Source Guard validates, and the agent analyzes. A fail-closed outcome prevents a security-service failure from granting access to untrusted content.

**Alternatives considered**
- Provider-side filtering: couples the Tavily adapter to security policy and leaves other providers unprotected.
- Prompt-injection string matching: cannot reliably cover configured guardrail detections.
- Fail open during service errors: violates the external-content trust boundary.

**Trade-offs**
Enabled deployments require both external security credentials and add two checks per accepted result. A temporary security-provider outage reduces external evidence rather than availability of internal RAG evidence.

**Affected code**
- `packages/web-search/src/multimodal_rag/web_search/source_guard.py::SourceGuardService`
- `packages/agents/src/multimodal_rag/agents/market_intelligence.py::MarketIntelligenceAgent.run`
- `services/api/src/multimodal_rag/api/config.py::APISettings`
- `services/api/src/multimodal_rag/api/main.py::create_app`
- `tests/test_source_guard.py`
- `tests/test_market_intelligence_agent.py`
- `README.md`

**Verification**
Twenty-eight focused Source Guard, Market Intelligence, and web-search tests pass via the project virtual environment. The tests mock both security HTTP APIs and verify that blocked content never appears in the generation prompt.

---

## DEC-2026-08-27-01 - Report per-region PDF extraction progress

**Status:** Accepted

**Context**
The API exposed only coarse ingestion stages, so a long-running PDF extraction
job appeared stalled while OCR and vision processing continued.

**Decision**
Add an optional progress callback to `ingest_document()`. The API job manager
stores completed regions, total work items, and percentage in the job snapshot,
logs each completed region, and the frontend renders this extraction-specific
progress while extraction is active.

**Why**
The orchestrator owns the actual per-region work boundary, making its callback
the most accurate progress signal without estimating from elapsed time. Page-level
vision summaries are included in the total when used.

**Alternatives considered**
- Time-based progress: inaccurate because OCR and vision calls have variable latency.
- Page-only progress: misses multiple figures/tables within a page.
- Terminal-only logging: helps operators but does not expose progress to the UI.

**Trade-offs**
Progress is region-based, and non-PDF ingestion paths do not currently provide
the same callback. The API response gains three additional fields.

**Affected code**
- `packages/ingestion/src/multimodal_rag/ingestion/pipeline/orchestrator.py::ingest_document`
- `services/api/src/multimodal_rag/api/ingestion.py::IngestionJobManager._run`
- `services/api/src/multimodal_rag/api/schemas.py::IngestionJobResponse`
- `apps/frontend/src/App.jsx::ETLWorkspace`

**Verification**
Run the ingestion job tests, Python compilation, and frontend production build.


## DEC-2026-08-26-03 - Bootstrap backend execution into the project virtual environment

**Status:** Accepted

**Context**
Windows can resolve `python.exe` to the global interpreter even when the
terminal prompt appears activated. The global interpreter did not contain
the project's Docling dependency, causing backend startup to fail before the
API application loaded.

**Decision**
Make the root `run_backend.py` launcher re-execute itself with
`.venv\Scripts\python.exe` when that interpreter exists and is not already
active.

**Why**
The launcher becomes resilient to PATH/activation inconsistencies while
preserving the existing explicit virtual-environment command.

**Trade-offs**
The project virtual environment must exist at the canonical `.venv` path;
otherwise the launcher continues with the caller's interpreter.

**Affected code**
- `run_backend.py`

**Verification**
The project interpreter imports both `docling_core` and `chromadb` successfully.

---

## DEC-2026-08-26-02 - Replace FAISS with persistent ChromaDB collections

**Status:** Accepted

**Context**
The application needed a persistent vector-store abstraction with collection
management while retaining its existing tenant-scoped hybrid retrieval path.

**Decision**
Use ChromaDB's persistent client and cosine-distance collections for dense
retrieval. Store chunk text and provenance metadata in the collection, keep
the existing NumPy embedding artifacts as the build input, and preserve the
BM25/RRF retriever contract above the indexing module.

**Why**
Chroma provides durable collections and document metadata without changing
embedding generation or the hybrid ranking behavior. A scoped persistent
directory maps directly to the existing user/project isolation model.

**Alternatives considered**
- Keep FAISS: less persistence and collection management for the requested
  architecture.
- Use a hosted vector database: adds network, credentials, and deployment
  dependencies outside the current local platform boundary.

**Trade-offs**
Chroma adds a larger dependency footprint and its default HNSW search is not
the same implementation as the previous exhaustive inner-product search.
Existing indexes must be rebuilt because the on-disk format is incompatible.

**Affected code**
- `packages/rag-core/src/multimodal_rag/rag/indexing/chroma_index.py`
- `packages/rag-core/src/multimodal_rag/rag/retrieval/retriever_2.py`
- `services/api/src/multimodal_rag/api/service.py`
- `services/api/src/multimodal_rag/api/ingestion.py`
- `requirements.txt`

**Verification**
The Chroma backend was imported, built from embedding artifacts, persisted,
reloaded, and queried successfully with cosine similarity. Python compilation
of the application source also succeeds.

---

## DEC-2026-08-26-01 - Promote the platform root and split component source roots

**Status:** Accepted

**Context**
The active application repository was nested under `multimodal-rag`, which made
the frontend, API, agents, web-search integration, ingestion, and RAG code hard
to discover. The platform root also contains a user-provided `Data/` directory;
Windows cannot host both that directory and the inner repository's `data/`
directory because names are case-insensitive.

**Decision**
Promote the platform directory as the working-tree layout and organize code
under `apps/`, `services/`, and `packages/`. Keep the established
`multimodal_rag.*` import and module-command contract through one coordinated
setuptools distribution with explicit component package mappings and a
namespace-extending package initializer. Move runtime artifacts to
`runtime-data/` and preserve `Data/` for user source material. Keep a thin
launcher at the historical backend path.

**Why**
The component directories make ownership and execution boundaries visible
without introducing network calls or changing API behavior. Explicit package
mappings support the multi-root layout while preserving existing imports.

**Alternatives considered**
- Leave all code under `multimodal-rag`: avoids migration but does not meet the
  requested platform-level organization.
- Rename or remove user `Data/`: risks user files and is unnecessary.
- Split agents and web search into network services: adds deployment and API
  compatibility concerns outside this organizational change.

**Trade-offs**
Runtime paths now use `runtime-data/`, and packaging configuration is more
explicit than a single `src/` root. The outer repository's hidden Git metadata
is managed by the host environment and could not be renamed; recoverable Git
bundles and metadata copies are kept under `.migration-backup/`.

**Affected code**
- `pyproject.toml`
- `packages/rag-core/src/multimodal_rag/__init__.py`
- `packages/rag-core/src/multimodal_rag/paths.py`
- `services/api/`, `packages/`, and `apps/`
- `run_backend.py`

**Verification**
Editable installation succeeds, imports resolve across every component source
root, and the offline test suite is run after the move. The knowledge graph is
reindexed against the promoted platform path after implementation.

---

## DEC-2026-08-25-03 - Allow question-only Web Search research

**Status:** Accepted

**Context**
Web Search should support direct questions without forcing users to provide company metadata. The company rows are useful focus hints, but they are not required for Tavily to research a question.

**Decision**
Make the research target list optional. The frontend ignores incomplete optional rows, the API accepts an empty `companies` list, and `CompanyResearchClient` instructs Tavily to identify relevant companies from the question when no targets are supplied. Remove the Web Search source/reference list from the chat result because it was frequently empty and the report is the primary output.

**Why**
This preserves the multi-company capability while reducing friction for ordinary web questions. Keeping source normalization in the backend leaves the API extensible without displaying an empty references section.

**Alternatives considered**
- Keep company rows mandatory: prevents direct question-only research.
- Submit partially filled rows: creates ambiguous provider targets and avoidable validation failures.
- Remove source data from the API: unnecessarily limits future consumers.

**Trade-offs**
Question-only research gives Tavily less explicit scope, so the report may identify a broader set of companies. Incomplete rows are silently ignored by the UI.

**Affected code**
- `frontend/src/App.jsx::submitQuestion()` and `WebResearchMessage()`
- `src/multimodal_rag/api/schemas.py::CompanyResearchRequest`
- `src/multimodal_rag/web_search/research.py::CompanyResearchClient.research()`

## DEC-2026-08-25-02 - Add a separate multi-company Tavily research workflow

**Status:** Accepted

**Context**
The frontend needs a Web Search mode for current company marketing research across zero or more named companies and supplied official URLs. Basic search results alone do not provide a concise, cross-source report, and the existing RAG and Market Trend flows must remain unchanged.

**Decision**
Add `CompanyResearchClient` and a Tavily Research provider backed by the asynchronous `/research` API. The authenticated `/web-search/research` route accepts zero to ten `{name, url}` targets plus a question, polls the Tavily task, and returns a concise report with cited sources. The React composer adds a Web Search mode with optional repeatable company rows.

**Why**
Tavily Research performs multi-source search and report generation while preserving source citations. Keeping this capability separate from `WebSearchClient` avoids mixing a long-running report workflow with the simple search contract and avoids changing the document-grounded agents.

**Alternatives considered**
- Reuse `/answer` or `MarketTrendAgent`: would incorrectly mix external web evidence with scoped RAG evidence.
- Return only search snippets: insufficient for concise company-level synthesis.
- Embed the hosted Company Researcher UI: prevents application-level validation, authentication, and response control.

**Trade-offs**
Research requests are asynchronous and may take up to the configured 120-second provider timeout. The initial implementation uses Tavily's targeted `mini` model and returns report text plus citations; streaming progress and persistent research history remain future work.

**Affected code**
- `src/multimodal_rag/web_search/research.py::CompanyResearchClient.research()`
- `src/multimodal_rag/web_search/providers/tavily.py::TavilySearchProvider.research()`
- `src/multimodal_rag/api/router.py::research_companies()`
- `frontend/src/App.jsx::submitQuestion()`
- `frontend/src/api.js::researchCompanies()`
- `tests/test_company_research.py`

**Verification**
Nine focused backend tests pass, including mocked polling and the authenticated route. The frontend Vite production build succeeds without a live Tavily call.

## DEC-2026-08-25-01 - Add a provider-neutral Tavily web-search seam

**Status:** Accepted

**Context**
Future CMO agents need fresh external search, but consumers must not depend on a specific provider. The existing codebase uses synchronous standard-library HTTP for provider integrations and does not need a web-search runtime registration yet.

**Decision**
Add `WebSearchClient` with an injected `SearchProvider` protocol, a Pydantic `SearchResult` normalization model, and a Tavily adapter. Use synchronous `urllib` transport, keep Tavily configuration in `TAVILY_API_KEY`, normalize missing/invalid optional metadata safely, and expose provider-neutral errors. Do not connect the client to `MarketTrendAgent`, `create_app()`, or `APISettings` in this increment.

**Why**
The injected seam allows Exa or GDELT to be added later without changing agent consumers. Reusing the existing transport style avoids an unnecessary dependency, while basic general search keeps this client free of LLM reasoning and extra provider behavior.

**Alternatives considered**
- Use Tavily's SDK: adds a dependency when the repository already has a working synchronous HTTP convention.
- Register it in FastAPI immediately: creates an unused runtime dependency before an agent consumes it.
- Expose raw Tavily dictionaries: couples future agents to provider response details.

**Trade-offs**
The client is synchronous and supports only the initial Tavily search capability. Provider-specific options and retries remain out of scope until a real consumer establishes those requirements.

**Affected code**
- `src/multimodal_rag/web_search/client.py`
- `src/multimodal_rag/web_search/models.py`
- `src/multimodal_rag/web_search/providers/tavily.py`
- `tests/test_web_search_client.py`

**Verification**
Focused tests mock the HTTP boundary, assert request forwarding and normalization, and verify configuration/provider failures without making network calls. The full suite and knowledge-graph reindex are run after implementation.

## DEC-2026-08-24-05 - Consolidate CMO evaluation output into one Markdown report

**Status:** Accepted

**Context**
The evaluation directory contained multiple overlapping per-question JSON files, summaries, and reports. The canonical CMO ground-truth dataset is already sufficient to reproduce the evaluation, so the generated artifacts did not need to be maintained separately.

**Decision**
`cmo_metrics.py::run()` reads every question from `evaluation/datasets/cmo_intelligence_ground_truth.json` and writes the summary plus per-question metrics directly to `evaluation/evaluation_result.md`. The evaluator no longer creates per-question JSON, summary JSON, or a separate results directory.

**Why**
This keeps one reproducible input and one human-readable output while retaining the existing retrieval and answer-quality metrics.

**Alternatives considered**
- Keep CSV/JSON checkpoints: preserves resumability but recreates the artifact sprawl the task is removing.
- Store only the summary: smaller output but loses per-question diagnostic results.

**Trade-offs**
The Markdown file also acts as the resume checkpoint. Its per-question table must retain the status column, and failed questions are retried on the next run. A manually edited or malformed table row is ignored and evaluated again.

**Affected code**
- `src/multimodal_rag/evaluation/cmo_metrics.py::run()`
- `src/multimodal_rag/evaluation/cmo_metrics.py::_write_report()`
- `evaluation/datasets/cmo_intelligence_ground_truth.json`
- `evaluation/evaluation_result.md`

**Verification**
The evaluator module compiles, its metric tests pass, the report writer is verified to produce one Markdown file from the JSON question set, and completed Markdown rows are parsed as resume state.

## DEC-2026-08-24-06 - Report hybrid retriever and chunk accuracy separately

**Status:** Accepted

**Context**
The evaluation report mixed retrieval concerns and included separate keyword-only and semantic-only comparisons, while the production RAG path uses the hybrid retriever. A useful report needs to distinguish whether a question found any correct evidence from how many retrieved chunks were correct.

**Decision**
Evaluate only the production hybrid retriever. Report question-level retriever accuracy using Hit@5, first relevant rank, and MRR. Report exact chunk-level precision, recall, and F1 separately using the manually verified expected chunk IDs.

**Why**
This matches the runtime path and makes the two failure modes visible: missing the correct evidence entirely versus retrieving too much unrelated context.

**Alternatives considered**
- Keep keyword-only and semantic-only benchmark sections: useful for tuning but not representative of the configured production path.
- Report only context precision/recall: does not show question-level retrieval success or ranking quality.

**Trade-offs**
The report no longer shows isolated component scores. Component-level tuning can still be performed separately, but the primary evaluation remains aligned with production hybrid retrieval.

**Affected code**
- `src/multimodal_rag/evaluation/cmo_metrics.py::retriever_accuracy()`
- `src/multimodal_rag/evaluation/cmo_metrics.py::chunk_accuracy()`
- `src/multimodal_rag/evaluation/cmo_metrics.py::_write_report()`
- `tests/test_cmo_metrics.py`

**Verification**
Focused metric checks verify Hit@K/MRR and exact chunk precision/recall/F1, and the generated report contains separate Retriever accuracy and Chunk accuracy sections.

## DEC-2026-08-24-07 - Resolve evaluation paths from the script location

**Status:** Accepted

**Context**
Directly running `cmo_metrics.py` from its own directory resolved the relative ground-truth path against the current working directory and could not find the dataset.

**Decision**
The evaluator derives the repository root from `__file__`, adds the repository `src` directory for direct imports, and uses absolute defaults for the ground-truth and Markdown report paths.

**Why**
The same command works from the repository root, the evaluation directory, or another working directory.

**Affected code**
- `src/multimodal_rag/evaluation/cmo_metrics.py`

**Verification**
Direct script execution is checked with the project virtual environment and resolves both evaluation artifacts independently of the current directory.

## DEC-2026-08-24-08 - Pause and resume evaluation after Gemini quota exhaustion

**Status:** Accepted

**Context**
Gemini embedding or generation quotas can be exhausted during a multi-question evaluation. Treating that provider error as a normal failed question causes the run to continue with an incomplete result and does not let the user rotate the API key without restarting.

**Decision**
Detect quota/rate-limit errors, pause with an instruction to change the Gemini key in `.env` and press Enter, reload the environment, reset the cached embedding client, and retry the same question. The Markdown checkpoint continues to skip previously completed questions. Non-quota errors remain visible failed rows.

**Why**
The current question is retried exactly after the provider credential changes, while completed work remains preserved in the existing report.

**Trade-offs**
The evaluator is interactive when quota exhaustion occurs and requires a human to provide a replacement key. Keys are never printed or written by the evaluator.

**Affected code**
- `src/multimodal_rag/evaluation/cmo_metrics.py::_is_rate_limited()`
- `src/multimodal_rag/evaluation/cmo_metrics.py::_wait_for_api_key_change()`
- `src/multimodal_rag/evaluation/cmo_metrics.py::run()`

**Verification**
Quota detection, API-client reset, and same-question retry behavior are covered by focused evaluator checks; direct execution continues to use the project `.env` and Markdown checkpoint.

## DEC-2026-08-24-04 - Ingest PPTX directly and convert legacy PPT with LibreOffice

**Status:** Accepted

**Context**
PowerPoint files need slide-aware extraction so titles, body text, tables, and speaker notes remain associated with their slide. Legacy PPT uses a binary format that requires conversion.

**Decision**
Activate PPT and PPTX in the authenticated ingestion pipeline. After ClamAV, `python-pptx` reads PPTX slides directly; legacy PPT is converted to PPTX in a temporary directory through LibreOffice headless mode and then uses the same parser. Each slide becomes a retrieval chunk with slide-number metadata, followed by the existing embedding and scoped FAISS-index stages.

**Why**
The slide boundary is a useful retrieval unit and preserves presentation provenance. The shared `chunks.json` contract avoids another indexing path.

**Alternatives considered**
- Convert every PPTX through LibreOffice: adds unnecessary conversion latency and a larger failure surface.
- Flatten the whole deck into one text blob: loses slide-level citations and retrieval precision.
- Use a cloud presentation converter: sends documents outside the application boundary.

**Trade-offs**
Image-based slide content receives best-effort RapidOCR; highly visual diagrams may still require a future Vision escalation. LibreOffice must be installed for legacy PPT, and slide numbers are stored as `page_numbers` for the existing citation schema.

**Affected code**
- `src/multimodal_rag/ingestion/presentation/extractor.py::ingest_presentation()`
- `src/multimodal_rag/api/ingestion.py::IngestionJobManager._run()`
- `src/multimodal_rag/api/router.py::ingest_pdf()`
- `src/multimodal_rag/ingestion/formats.py`
- `frontend/src/App.jsx::ETLWorkspace()`

**Verification**
Focused PPTX extraction and API format tests, Python compilation, the existing API/job regression suite, and the frontend production build validate the implementation.

---

## DEC-2026-08-24-03 - Ingest DOCX directly and convert legacy DOC with LibreOffice

**Status:** Accepted

**Context**
Word files were allowlisted but inactive. DOCX is a structured Office Open XML format that `python-docx` can read directly, whereas legacy binary DOC requires a conversion engine.

**Decision**
Activate DOC and DOCX for the authenticated API pipeline. After ClamAV scanning, DOCX is parsed with `python-docx`; DOC is converted to DOCX in a temporary directory using LibreOffice headless mode, then parsed with the same code. Paragraphs and table rows are packed into standard `chunks.json` records and reuse the existing embedding and scoped FAISS-index flow.

**Why**
This uses a maintained structured parser for DOCX and a reliable local converter for the binary legacy format. Keeping the standard chunk artifact avoids a duplicate retrieval/indexing path.

**Alternatives considered**
- Parse DOC binary bytes directly: unreliable and does not preserve Word structure.
- Cloud conversion API: moves document contents to another provider and introduces credentials/cost.
- Restrict support to DOCX: excludes existing legacy Word archives.

**Trade-offs**
LibreOffice must be installed for `.doc` ingestion; `RAG_LIBREOFFICE_PATH` can point to `soffice.exe` when it is not on `PATH`. Word chunks currently have no page numbers because `python-docx` does not expose stable rendered pagination. DOCX/DOC bytes are malware-scanned before parsing or conversion.

**Affected code**
- `src/multimodal_rag/ingestion/word/extractor.py::ingest_word()`
- `src/multimodal_rag/api/ingestion.py::IngestionJobManager._run()`
- `src/multimodal_rag/api/router.py::ingest_pdf()`
- `src/multimodal_rag/ingestion/formats.py`
- `frontend/src/App.jsx::ETLWorkspace()`

**Verification**
Focused DOCX artifact tests, API format tests, and ingestion-job regression tests pass in the project virtual environment. Legacy DOC conversion is exercised through LibreOffice when its installation completes.

---

## DEC-2026-08-24-02 - Transcribe scanned MP3 and MP4 uploads with Deepgram

**Status:** Accepted

**Context**
MP3 and MP4 were public allowlisted formats but the API worker only dispatched PDFs to the layout-extraction pipeline. Media needs timestamped speech text without introducing a local model, GPU, or FFmpeg service.

**Decision**
Activate MP3 and MP4 ingestion. After the existing ClamAV gate, `IngestionJobManager._run()` sends media directly to Deepgram's pre-recorded endpoint. Deepgram extracts an MP4 audio track server-side and the media adapter writes timestamped, speaker-aware transcript chunks in the existing `chunks.json` format before the existing embedding and scoped FAISS-index stages.

**Why**
The shared chunk artifact keeps retrieval, embeddings, and indexing unchanged, while Deepgram provides timestamps and diarization without a separate audio-extraction runtime.

**Alternatives considered**
- Faster-Whisper with FFmpeg: local and offline but adds model, decoder, and hardware operational requirements.
- A new media-specific indexer: duplicates the established embedding/index behavior.

**Trade-offs**
Media bytes are sent to Deepgram after malware scanning, requiring internet access, an API key, and compliance approval for the provider. Media transcription fails clearly when the key/provider is unavailable. The 50 MB upload limit remains in force; DOC/DOCX stay inactive.

**Affected code**
- `src/multimodal_rag/ingestion/media/deepgram.py::DeepgramTranscriber.transcribe()`
- `src/multimodal_rag/ingestion/media/deepgram.py::ingest_media()`
- `src/multimodal_rag/api/router.py::ingest_pdf()`
- `src/multimodal_rag/api/ingestion.py::IngestionJobManager._run()`
- `src/multimodal_rag/api/config.py::APISettings.from_environment()`
- `frontend/src/App.jsx::ETLWorkspace()`

**Verification**
Focused tests mock Deepgram's HTTP response and assert timestamped, embedding-compatible artifacts; API tests cover MP3 activation. Python compilation and the frontend production build validate imports and UI syntax.

---

## DEC-2026-08-24-01 - Scan API uploads with ClamAV before document parsing

**Status:** Accepted

**Context**
The authenticated ingestion worker previously passed uploaded PDF bytes directly into PyMuPDF, Docling, OCR, Vision, and embedding steps. A hostile but structurally valid file could therefore reach document parsers and downstream services without an antivirus verdict.

**Decision**
When `RAG_CLAMAV_ENABLED=true`, `IngestionJobManager._run()` streams the saved upload to a local `clamd` service before calling `ingest_document()`. A clean verdict continues the existing pipeline. A malware verdict moves the upload to the scoped quarantine directory and fails the job. Scanner connection or protocol failures also fail the job by default; `RAG_CLAMAV_FAIL_CLOSED=false` is an explicit operational override.

**Why**
Scanning raw bytes before parsing prevents a detected file from entering extraction, OCR, Vision, artifact writing, embeddings, or the FAISS index. The standard-library `clamd` streaming protocol avoids adding a Python dependency or linking the application to ClamAV's GPL library, and does not require the API and scanner to share an upload path.

**Alternatives considered**
- Run `clamscan` for each upload: reloads signature databases for each process and adds avoidable latency.
- Scan inside the HTTP upload request: keeps the client request open for scanner work and duplicates the background-job responsibility.
- Link directly to libclamav: couples the application to the scanner library and its licensing constraints.

**Trade-offs**
ClamAV must be installed, running, and supplied with current signatures before the feature is enabled. Enabling fail-closed scanning makes uploads fail while the scanner is unavailable. This change is malware scanning, not a substitute for process isolation or content-disarm tooling.

**Affected code**
- `src/multimodal_rag/security/clamav.py::ClamAVScanner.scan()`
- `src/multimodal_rag/api/config.py::APISettings.from_environment()`
- `src/multimodal_rag/api/main.py::create_app()`
- `src/multimodal_rag/api/ingestion.py::IngestionJobManager._run()`
- `tests/test_clamav.py`
- `tests/test_ingestion_jobs.py`

**Verification**
`python -m py_compile` succeeds for the changed modules and tests. `PYTHONPATH=src python -m unittest tests.test_clamav` passes all four protocol/verdict tests. The existing API and ingestion-job suites could not be imported in this environment because its Python installation lacks `faiss`.

---

## DEC-2026-08-13-01 — Market trend analysis remains scoped to indexed RAG evidence

**Status:** Accepted

**Context**
The Market Trend Agent is constructed with an in-process `RAGClient` and currently analyzes retrieved document chunks. The repository does not contain a live news or research retrieval connector.

**Decision**
Market trend analysis uses only the scoped RAG index. Missing industry, geography, and time range are resolved by the agent to cross-industry, global, and a rolling 30-day window, respectively.

**Why**
This follows the existing service boundary and avoids presenting external or unindexed information as repository-backed evidence.

**Alternatives considered**
- Add a web/news provider: outside the current RAG-only boundary and would require provider credentials and new runtime behavior.
- Require all context fields: preserves the former limitation but prevents autonomous broad-query analysis.

**Trade-offs**
The agent cannot discover live news. Sources without indexed publication dates cannot be verified as recent.

**Affected code**
- `src/multimodal_rag/agents/market_trend.py`
- `src/multimodal_rag/agents/models.py`
- `src/multimodal_rag/agents/rag_client.py`

**Verification**
The repository knowledge graph identifies `MarketTrendAgent.run()` as the analysis entry and `InProcessRAGClient.retrieve()` as its RAG boundary. Existing metadata loading is read-only and passes chunk metadata into the agent evidence model.

## DEC-2026-08-13-02 — Preserve provenance through additive agent fields

**Status:** Accepted

**Context**
Retrieved evidence already carries chunk ID, source, document, page, score, excerpt, and raw metadata. The trend UI currently renders only part of that information.

**Decision**
Add resolved scope and nullable publication-date, URL, and recency fields while retaining all existing response fields and endpoint shape.

**Why**
Downstream agents and the UI need source-level provenance, while existing clients should continue to deserialize the current envelope.

**Alternatives considered**
- Replace `sources` with a new evidence structure: would break current API consumers.
- Put date and URL only inside arbitrary metadata: makes the public contract harder to consume and does not expose recency classification.

**Trade-offs**
Older indexed documents return `null` date or URL values when those fields are absent. The agent reports those unknowns rather than inferring them.

**Affected code**
- `src/multimodal_rag/agents/models.py`
- `src/multimodal_rag/agents/rag_client.py`
- `frontend/src/App.jsx`

**Verification**
The existing `AgentEvidence` model and `InProcessRAGClient.retrieve()` path preserve raw metadata and retrieval scores; the change extends those models without removing existing fields.

## DEC-2026-08-19-03 â€” Expose query-time RAG diagnostics through the answer response

**Status:** Accepted

**Context**
The React application displayed retrieved evidence only inside each answer, while the backend already produced a richer `RAGTrace` containing ranking, citation, timing, token, and provenance information. Users need a dedicated workspace to inspect the exact evidence used for a particular question.

**Decision**
Add an additive `rag_trace` field to the existing `/answer` response and render it in a separate React RAG Trace workspace. The payload serializes the trace already produced for the answer and does not run retrieval or generation a second time.

**Why**
The approach preserves the existing answer contract, makes per-question diagnostics available to the frontend, and keeps the trace tied to the exact RAG execution that generated the selected answer.

**Alternatives considered**
- Create a second diagnostic endpoint: would require retaining or replaying request state and risks tracing a different execution.
- Re-run retrieval in the frontend workflow: adds latency and can yield different ranking results.
- Keep diagnostics only in a separate legacy UI: does not serve the primary React chat interface.

**Trade-offs**
The response is larger because it includes retrieved chunk text and metadata. Sentence-boundary and token estimates in the UI are explicitly heuristic; the trace reports retrieved chunks, not unreturned candidate chunks.

**Affected code**
- `src/multimodal_rag/api/schemas.py::AnswerResponse`
- `src/multimodal_rag/api/service.py::RAGService._trace_payload()`
- `src/multimodal_rag/api/router.py::answer_question()`
- `frontend/src/App.jsx::RAGTraceWorkspace()`
- `frontend/src/App.jsx::AnswerMessage()`

**Verification**
`python -m unittest tests.test_api` passes and `npm.cmd run build` completes successfully.

## DEC-2026-08-19-04 â€” Use sentence-safe hierarchical semantic children for narrative content

**Status:** Accepted

**Context**
Narrative PDF regions were merged by section and split with a recursive character splitter. A hard character boundary could split a sentence, leaving retrieved evidence incomplete even when the selected chunk was relevant.

**Decision**
Replace paragraph-buffer character splitting with deterministic semantic packing: keep short paragraphs intact, split oversized paragraphs into complete sentences, pack those units into child chunks with sentence-safe overlap, and record a shared parent-section ID plus child index/count on each emitted child.

**Why**
This retains the existing layout, heading, table, figure, and page-boundary protections while preventing arbitrary sentence cuts and preserving a query-time hierarchy for diagnostics and future parent-context expansion.

**Alternatives considered**
- LLM-driven splitting for every document: higher cost, latency, and non-determinism.
- Keep recursive character splitting with larger overlap: still permits broken sentence boundaries.
- Embed only whole sections: loses retrieval precision for long sections.

**Trade-offs**
An unusually long single sentence can exceed the configured target size. Existing indexes retain their current chunks until documents are re-ingested and the vector index is rebuilt.

**Affected code**
- `src/multimodal_rag/ingestion/processing/chunker.py::ChunkMetadata`
- `src/multimodal_rag/ingestion/processing/chunker.py::_semantic_children()`
- `src/multimodal_rag/ingestion/processing/chunker.py::_flush_paragraph_buffer()`
- `tests/test_semantic_chunking.py`

**Verification**
`python -m unittest tests.test_semantic_chunking` passes.

## DEC-2026-08-19-05 â€” Checkpoint and pace Gemini document embeddings

**Status:** Accepted

**Context**
Gemini's free-tier embedding quota can be exhausted while a document is being embedded. The former indexer accumulated all vectors in memory and wrote output only after every batch completed, so a rate-limit failure discarded the completed batches and caused the next run to restart the whole document.

**Decision**
Persist a per-document progress checkpoint after each successful embedding batch, proactively limit document input batches to a configurable per-minute budget, and retry a rate-limited batch using Gemini's supplied delay. On successful final output, remove the temporary checkpoint.

**Why**
The checkpoint preserves already-completed model work, while pacing keeps a large document within the provider quota without changing the completed `embeddings.npy` and FAISS formats.

**Alternatives considered**
- Increase the provider plan only: does not make the free-tier or transient-rate-limit path resilient.
- Reduce all batch sizes: increases request overhead and still loses progress after a failure.
- Persist incomplete embeddings as final output: risks indexing only part of a document.

**Trade-offs**
Large documents may pause for a quota window. Temporary checkpoint files use additional disk space until embedding completes, and stale or incompatible checkpoints are ignored safely.

**Affected code**
- `src/multimodal_rag/rag/embedding/embedder.py::_embed_texts()`
- `src/multimodal_rag/rag/embedding/embedder.py::embed_chunks()`
- `src/multimodal_rag/rag/embedding/embedder.py::write_embeddings()`
- `src/multimodal_rag/cli/build_index.py::main()`
- `tests/test_offline_embeddings.py`

**Verification**
`python -m unittest tests.test_offline_embeddings` passes, including provider-delay retry and checkpoint-resume coverage.

## DEC-2026-08-19-06 â€” Store parent sections separately from retrieval children

**Status:** Accepted

**Context**
A single target chunk size either dilutes precise matches when enlarged or loses surrounding meaning when reduced. The existing semantic hierarchy recorded a parent-section identifier but did not retain the parent text as a retrievable context object.

**Decision**
Emit one stored parent chunk for each narrative section and smaller sentence-safe child chunks linked by `parent_chunk_id`. Embed only children; at query time, preserve the child's ranking and citation identity while supplying its stored parent text to prompt construction.

**Why**
Small children make dense and lexical matching more precise. Parent expansion gives generation complete section context without inserting large parent vectors into the FAISS/BM25 candidate pool.

**Alternatives considered**
- Index both parent and child chunks: duplicates evidence and lets broad parent vectors displace precise child matches.
- Increase the single fixed target size: reduces retrieval precision and still cannot fit every document structure.
- Expand adjacent child chunks only: cannot recover context that lies beyond the immediate sibling window.

**Trade-offs**
Stored artifacts grow because parent text is retained alongside children. Prompt context can be larger for a retrieved child, so parent sections remain subject to normal downstream model-context constraints.

**Affected code**
- `src/multimodal_rag/ingestion/processing/chunker.py::_flush_paragraph_buffer()`
- `src/multimodal_rag/rag/embedding/embedder.py::embed_chunks()`
- `src/multimodal_rag/rag/trace.py::expand_parent_context()`
- `src/multimodal_rag/api/service.py::RAGService.retrieve()`
- `tests/test_semantic_chunking.py`
- `tests/test_rag_trace.py`

**Verification**
Focused semantic chunking, embedding, API, and parent-expansion tests pass. The unrelated local-index regression test in `tests.test_rag_trace` requires a live Gemini embedding call and cannot run in the sandbox.

## DEC-2026-08-19-07 â€” Canonicalize exact retrieval chunks by normalized SHA-256

**Status:** Accepted

**Context**
Repeated uploads can create identical child chunks and duplicate embedding/index entries.

**Decision**
Hash normalized child text with SHA-256, retain the first active chunk as canonical, and record later matches in a per-document deduplication report. Parent context remains document-specific.

**Why**
Exact hashing is fast and deterministic; preserving only canonical children prevents redundant embedding and retrieval entries.

**Trade-offs**
Near-duplicate wording is intentionally not merged. Parent context can still repeat across partially overlapping documents.

**Affected code**
- `src/multimodal_rag/ingestion/output/deduplicator.py`
- `src/multimodal_rag/ingestion/pipeline/orchestrator.py::ingest_document()`
- `src/multimodal_rag/ingestion/processing/chunker.py::ChunkMetadata`

**Verification**
`python -m unittest tests.test_deduplication tests.test_semantic_chunking tests.test_offline_embeddings tests.test_api` passes.

## DEC-2026-08-20-01 - Add a client-side ETL workspace before introducing an upload API

**Status:** Accepted

**Context**
The React application has chat, market-trend, and RAG Trace workspaces, while production document ingestion is currently exposed through CLI entry points rather than a document-upload HTTP endpoint. The user needs visibility into the upload, extraction, chunking, and storage handoffs while the existing background ingestion continues.

**Decision**
Add an `Ingest documents` React workspace with PDF drag-and-drop/file selection, an animated four-stage ETL progress view, validation feedback, and a browser-local recent-artifacts list. The UI explicitly labels this as a client ETL handoff and does not claim that a server upload or embedding has occurred.

**Why**
This delivers the requested workflow without inventing a backend contract or interrupting the existing CLI ingestion. Local storage keeps the recent-artifact view useful across refreshes while the upload API is designed separately.

**Alternatives considered**
- Add a new upload endpoint now: would require authentication, multipart storage, background-job tracking, and a defined handoff into the existing CLI pipeline.
- Show static progress only: would not let users validate the interaction with real PDF selections.

**Trade-offs**
The animation is a frontend preview and does not extract, chunk, embed, or persist PDF bytes on the server. A future upload API can replace the stage timers while retaining the workspace and status model.

**Affected code**
- `frontend/src/App.jsx::ETLWorkspace()`
- `frontend/src/App.jsx::App()` workspace navigation
- `frontend/src/styles.css` ETL workspace styles

**Verification**
`npm.cmd run build` completes successfully in `frontend/`.

## DEC-2026-08-20-02 - Connect the ETL workspace to a serialized scoped ingestion job

**Status:** Accepted

**Context**
The ETL workspace initially previewed stage timers because the API had no upload route. Real ingestion must reuse the existing orchestrator, embedding writer, and FAISS builder while keeping user/project artifacts isolated and avoiding concurrent index rebuilds.

**Decision**
Add authenticated multipart `POST /ingest` and `GET /ingest/{job_id}` routes. The API saves the PDF under a validated user/project scope, queues one in-process ingestion job, runs `ingest_document()`, `embed_document()`, `write_embeddings()`, and `build_index_from_output_dir()`, then exposes stage, chunk, embedding, and error counts for frontend polling.

**Why**
The existing pipeline remains the single source of truth for extraction and chunking. A one-worker manager serializes rebuilds within an API process, and scoped artifact/index directories prevent the frontend upload from colliding with the global background CLI ingestion.

**Alternatives considered**
- Run the entire pipeline inside the upload request: would hold the HTTP connection open during extraction and provider-rate-limited embedding.
- Add a second upload-specific chunker/indexer: would duplicate behavior and risk divergent retrieval artifacts.
- Use a distributed queue immediately: would add infrastructure not present in this repository; the job manager can be replaced later without changing the API contract.

**Trade-offs**
Job status is process-local and is lost on API restart; multi-worker deployments need a shared job store/queue. Upload bytes are retained only while the job runs, while the generated scoped artifacts and index remain on disk.

**Affected code**
- `src/multimodal_rag/api/router.py::ingest_pdf()`
- `src/multimodal_rag/api/router.py::ingestion_status()`
- `src/multimodal_rag/api/ingestion.py::IngestionJobManager`
- `src/multimodal_rag/api/service.py::clear_scoped_index_cache()`
- `frontend/src/api.js::uploadDocument()`
- `frontend/src/App.jsx::ETLWorkspace()`

**Verification**
`python -m unittest tests.test_api`, `python -m py_compile` for changed API modules, and `npm.cmd run build` all pass.

## DEC-2026-08-20-03 - Backfill pending scoped embeddings and reject empty indexes

**Status:** Accepted

**Context**
An exact duplicate upload can legitimately produce zero new retrieval chunks because SHA-256 deduplication retains the first canonical chunk. The first upload implementation embedded only the new document and could rebuild a scoped FAISS index from zero-dimensional embeddings, leaving previously chunked documents unavailable to retrieval.

**Decision**
Before every scoped index rebuild, the ingestion job embeds every scoped document with chunks but without a complete non-empty embedding artifact, attempting the uploaded document first. Empty or malformed embedding arrays are skipped by the FAISS builder, and the API rejects empty indexes as unavailable rather than treating them as searchable. A duplicate upload therefore preserves/rebuilds from canonical embeddings instead of replacing the scope with an empty index.

**Why**
This makes the upload contract match the user expectation that the whole selected scope is searchable after ingestion, while preserving exact-chunk deduplication and preventing silent zero-vector indexes.

**Trade-offs**
The first upload to a scope may embed several previously chunked documents and take longer. Pending documents that fail provider embedding are logged and skipped when the new upload itself succeeds; the index is still built only from complete artifacts. A scope with no usable vectors reports a clear unavailable-index error.

**Affected code**
- `src/multimodal_rag/api/ingestion.py::IngestionJobManager._embed_pending_documents()`
- `src/multimodal_rag/api/ingestion.py::IngestionJobManager._run()`
- `src/multimodal_rag/rag/indexing/faiss_index.py::build_index_from_output_dir()`
- `src/multimodal_rag/api/service.py::RAGService._load_scope()`
- `tests/test_ingestion_jobs.py`

**Verification**
The ingestion regression tests reject zero-vector artifacts and verify pending-document backfill; API tests and Python compilation pass. Live scoped repair remains pending because the environment denied external Gemini document-data egress.

## DEC-2026-08-20-04 - Enforce bounded, non-empty document uploads

**Status:** Accepted

**Context**
The ingestion path could accept an empty upload at the API boundary and allowed PDFs beyond the product's intended processing size. The repository currently has no video extractor or duration probe, so accepting video files would enqueue inputs that the PDF-only orchestrator cannot process.

**Decision**
Reject empty uploads immediately, enforce a maximum of 50 PDF pages both at the API boundary and in the canonical PDF loader, and keep non-PDF/video uploads explicitly unsupported. A future video ingestion path must enforce a duration below 10 minutes before queueing work.

**Why**
Early validation avoids creating jobs or invoking expensive layout analysis for invalid inputs. Duplicating the page guard at the API and loader protects both frontend uploads and direct CLI/orchestrator callers.

**Alternatives considered**
- Validate only in the background loader: preserves one validation location but gives the frontend a queued job that fails later.
- Add video support now: the repository has no video extraction, chunking, or embedding path, so accepting video would create a misleading and incomplete pipeline.

**Trade-offs**
The 50-page rule is intentionally strict and rejects larger PDFs rather than partially ingesting them. Video uploads remain unavailable until a dedicated media pipeline and duration probe are added.

**Affected code**
- `src/multimodal_rag/ingestion/loaders/pdf_loader.py::load_pdf()`
- `src/multimodal_rag/api/router.py::ingest_pdf()`
- `frontend/src/App.jsx::ETLWorkspace()`
- `tests/test_pdf_loader.py`
- `tests/test_api.py`

**Verification**
Focused loader/API tests cover empty files, 51-page PDFs, and valid one-page uploads; frontend build and Python compilation are run after the change.

## DEC-2026-08-20-05 - Establish the planned ingestion format allowlist

**Status:** Accepted

**Context**
The active worker is PDF-only, but the next ingestion phases will add audio, video, and Word-document pipelines. The frontend, API, and CLI should expose the same explicit format contract now instead of accepting arbitrary extensions or accidentally routing future media through the PDF loader.

**Decision**
Allowlist `.pdf`, `.mp4`, `.mp3`, `.doc`, and `.docx` through a shared ingestion-format policy. Keep only PDF active for now; API and CLI entry points report a clear not-implemented response for the other allowlisted formats, and the frontend advertises the planned formats while accepting only the active PDF pipeline.

**Why**
One shared allowlist prevents drift between upload surfaces and gives the future media/document workers a stable contract. Explicitly separating allowed from active formats avoids corrupt jobs and makes the implementation boundary visible to users.

**Alternatives considered**
- Accept every extension and let the worker fail: hides the product contract and can route unsupported bytes into PDF parsing.
- Allow only PDF until all future workers exist: safer at runtime, but forces UI/API changes when each planned pipeline is introduced.

**Trade-offs**
MP4, MP3, DOC, and DOCX are recognized but cannot be ingested until their workers are implemented. The API uses `501 Not Implemented` for those formats so clients can distinguish planned support from an invalid extension.

**Affected code**
- `src/multimodal_rag/ingestion/formats.py`
- `src/multimodal_rag/api/router.py::ingest_pdf()`
- `src/multimodal_rag/cli/ingest.py::main()`
- `frontend/src/App.jsx::ETLWorkspace::acceptFile()`
- `tests/test_ingestion_formats.py`
- `tests/test_api.py`

**Verification**
Unit/API tests cover the five allowed extensions, unrelated extensions, and the not-yet-implemented media response; frontend build and Python compilation are run after the change.

## DEC-2026-08-20-06 - Reject exact duplicate files before extraction

**Status:** Accepted

**Context**
The existing SHA-256 deduplication hashed normalized chunk text inside `deduplicate_chunks()`. That prevented duplicate retrieval children from entering the index, but it happened after PDF loading, extraction, and chunking, and still created a new zero-chunk document artifact.

**Decision**
Compute the uploaded PDF's SHA-256 while writing the temporary upload, check a persisted file-fingerprint registry before queueing, and reserve the hash for in-flight jobs. The orchestrator repeats the check for CLI/direct callers and records the fingerprint after a successful document write. Duplicate API uploads return HTTP 409 and are removed without starting extraction.

**Why**
File-level identity is the only reliable way to stop an exact duplicate before expensive work. The in-flight reservation closes the race where two identical uploads arrive before the first job persists its registry entry. Chunk-level SHA-256 remains for partial overlap and different files with shared content.

**Alternatives considered**
- Keep only chunk-level deduplication: saves index space but cannot prevent duplicate extraction or artifact folders.
- Compare filenames and sizes: false positives for renamed/revised files and false negatives for same-content files with different metadata.

**Trade-offs**
Previously ingested documents do not all have raw file fingerprints because their temporary PDFs were removed. When the original source is still present in the canonical or legacy input directory, the registry backfills its file hash on lookup; otherwise existing chunk-level deduplication remains the fallback. Exact duplicates uploaded after this decision are rejected before extraction.

**Affected code**
- `src/multimodal_rag/ingestion/output/deduplicator.py`
- `src/multimodal_rag/api/router.py::ingest_pdf()`
- `src/multimodal_rag/api/ingestion.py::IngestionJobManager.submit()`
- `src/multimodal_rag/ingestion/pipeline/orchestrator.py::ingest_document()`
- `tests/test_api.py`
- `tests/test_deduplication.py`

**Verification**
Tests cover persisted file fingerprints, API rejection before manager submission, and the existing chunk-level exact/partial deduplication behavior.

## DEC-2026-08-20-07 - Keep completed ingestion jobs completed after status logging

**Status:** Accepted

**Context**
The background ingestion worker updated a job to `completed` after embedding and FAISS index persistence, then referenced an undefined `result.chunks` variable in its completion log. The exception handler consequently changed a successful job to `failed` even though its embeddings and index had already been written.

**Decision**
Use the worker's existing `embedded_count` value in the completion log and add a regression test that runs the worker through the completed state.

**Why**
The worker already has the authoritative chunk and embedding counters in scope. Removing the undefined reference prevents a post-completion logging error from corrupting the public job status.

**Alternatives considered**
- Remove the completion log: avoids the exception but loses useful operational evidence.
- Recreate a result object only for logging: duplicates state and risks diverging from the persisted embedding count.

**Trade-offs**
The completion log reports embeddings counted by the scoped backfill operation, which is the value exposed to the frontend and index-building flow.

**Affected code**
- `src/multimodal_rag/api/ingestion.py::IngestionJobManager._run()`
- `tests/test_ingestion_jobs.py::IngestionJobTests.test_run_keeps_completed_status_after_completion_log()`

**Verification**
The focused ingestion, API, and deduplication suite passes (30 tests). The worker logs completion and preserves `status=completed`.

The duplicate shown in the 17:54 log was a content-level duplicate: its 266 chunks were all reused from `AI-marketing-playbook 4.pdf`, but its PDF bytes had a different SHA-256. File-level SHA-256 rejection therefore did not apply; preventing that class before chunking requires a separate normalized-content fingerprint and lightweight text extraction.

## DEC-2026-08-20-08 - Reject normalized text duplicates during upload preflight

**Status:** Accepted

**Context**
Exact file SHA-256 prevents byte-identical uploads, but PDFs exported or renamed by different tools can contain the same text with different metadata, object ordering, or compression. Those uploads previously reached layout analysis and chunking before chunk-level deduplication discarded every retrieval child.

**Decision**
Compute a normalized extracted-text SHA-256 fingerprint using PyMuPDF before queueing an API job or starting orchestrator layout analysis. Persist it in a separate content-fingerprint registry, reserve it for in-flight uploads, and reject a match with HTTP 409. Backfill fingerprints from retained input PDFs and raw extraction snapshots when available. Image-only PDFs return no text fingerprint and continue through normal validation and chunk-level deduplication.

**Why**
Text normalization removes export-only whitespace and PDF metadata differences while remaining deterministic and inexpensive compared with Docling layout analysis, OCR, Gemini calls, and embedding. Keeping this separate from exact-byte hashes preserves both identity checks.

**Alternatives considered**
- Hash raw PDF bytes only: misses semantically identical re-exports, which caused the reported duplicate extraction.
- Run full chunking before deduplication: detects more layout variations but repeats the expensive work the preflight is intended to avoid.
- Use perceptual image hashes for every PDF: useful for scanned PDFs but insufficient for text PDFs and adds image-rendering cost; retained as a future scan-specific extension.

**Trade-offs**
The preflight performs lightweight text extraction and cannot identify image-only duplicates. Text differing materially after normalization is intentionally treated as a new document. Existing artifacts without a source PDF are backfilled only when their raw extraction snapshot contains usable text.

**Affected code**
- `src/multimodal_rag/ingestion/loaders/pdf_loader.py::normalized_text_hash()`
- `src/multimodal_rag/ingestion/loaders/pdf_loader.py::normalized_pdf_text_hash()`
- `src/multimodal_rag/ingestion/output/deduplicator.py::find_content_duplicate()`
- `src/multimodal_rag/ingestion/output/deduplicator.py::deduplicate_chunks()`
- `src/multimodal_rag/api/router.py::ingest_pdf()`
- `src/multimodal_rag/api/ingestion.py::IngestionJobManager.submit()`
- `src/multimodal_rag/ingestion/pipeline/orchestrator.py::ingest_document()`
- `tests/test_pdf_loader.py`
- `tests/test_api.py`
- `tests/test_deduplication.py`

**Verification**
Focused PDF-loader, API, deduplication, and ingestion-job tests pass (35 tests), including two PDFs with different metadata producing the same normalized content fingerprint and preflight rejection before manager submission.

## DEC-2026-08-26-02 - Bootstrap namespace-package roots in the backend launcher

**Status**
Accepted

**Context**
The promoted repository keeps `multimodal_rag.*` imports while distributing source files across component-specific roots. The Python launcher (`py run_backend.py`) may use a system interpreter that does not have the project installed in editable mode, so Uvicorn could not resolve `multimodal_rag.api.main` after the token prompt.

**Decision**
The canonical API launcher adds the seven component `src` directories to `sys.path` immediately before handing the string application target to Uvicorn. The root launcher remains a thin delegate, and the package/import contract is unchanged.

**Why**
This makes namespace-package resolution independent of editable-install state
without eagerly importing the application or changing Uvicorn behavior. The
selected interpreter must still have the project dependencies installed; the
canonical supported environment is `.venv`.

**Alternatives considered**
- Require callers to activate the virtual environment: fragile for the documented `py run_backend.py` command.
- Replace the Uvicorn string target with an eagerly imported app object: changes startup timing and reload semantics.
- Rewrite imports to component-specific package names: breaks the existing `multimodal_rag.*` API.

**Trade-offs**
The launcher owns a small amount of path bootstrap logic, while packaging and editable installs remain the preferred development path.

**Affected code**
- `services/api/run_backend.py::_ensure_source_roots()`
- `services/api/run_backend.py::main()`
- `run_backend.py`

## DEC-2026-08-26-03 - Retire the Streamlit frontend

**Status**
Accepted

**Context**
The platform now has a maintained React/Vite client under `apps/frontend/`.
The older Streamlit client duplicated the user and developer workspaces,
required a second UI runtime, and was no longer part of the supported product
surface.

**Decision**
Remove the Streamlit application, its compatibility shim, UI-only tests, theme
configuration, dependency, and namespace/package mappings. Keep the RAG trace,
API, CLI, and evaluation code that remains used by the React client or Python
workflows.

**Why**
There is one clear frontend entry point, fewer dependencies, and no stale UI
launch commands or import paths after the removal.

**Alternatives considered**
- Keep both clients: preserves duplication and an unsupported runtime.
- Hide the Streamlit client without deleting it: leaves stale dependencies and
  misleading documentation.

**Trade-offs**
Streamlit-specific UI behavior and tests are intentionally no longer
available. The React client owns browser presentation; backend contracts and
RAG behavior are unchanged.

**Affected code**
- `apps/streamlit/` (removed)
- `src/multimodal_rag/ui/` (removed)
- `tests/test_streamlit_*.py` (removed)
- `pyproject.toml`, `requirements.txt`
- `README.md`, `docs/ARCHITECTURE_REFERENCE.md`, `docs/PROJECT_STATUS.md`,
  `docs/flow.md`, `AGENTS.md`

## DEC-2026-08-26-04 - Use highest-scoring evaluation questions as frontend suggestions

**Status**
Accepted

**Context**
The React frontend's starter questions were generic and did not demonstrate
the strongest-performing questions from the current RAG evaluation report.

**Decision**
Replace the three generic suggestions with evaluation questions 1, 2, and 4:
the Bain and Meta Conversational Commerce Survey scope, The CMO Survey 2025
sample and response rate, and technology's role in an effective Marketing
Operating Model.

**Why**
These questions are tied for the strongest reported results: each has a
perfect Hit@5, first relevant rank of 1, and perfect faithfulness, answer
relevancy, and answer correctness scores. They also provide varied examples
of the indexed CMO document corpus.

**Alternatives considered**
- Keep generic prompts: less representative of verified system capability.
- Select questions by latency: latency is operational rather than answer quality.
- Select only one evaluation topic: provides less useful coverage for users.

**Trade-offs**
The suggestions are more domain-specific and less useful as generic examples
for users with unrelated documents. Their content is coupled to the current
evaluation corpus and should be refreshed if that corpus changes.

**Affected code**
- `apps/frontend/src/App.jsx::suggestions`
- `evaluation/evaluation_result.md`

**Verification**
Compared the selected questions against the per-question results in
`evaluation/evaluation_result.md`; all three have the stated top scores.
## DEC-2026-09-10-03 - Treat completed meeting context as sufficient for strategy follow-ups

**Status:** Accepted

**Context**
A meeting follow-up requesting additional key facts was routed to Market Strategy, but the generic readiness gate interpreted the follow-up as a new market-entry request and asked for geographic context again. The prior meeting briefing already contained the meeting scope and should remain the source of context.

**Decision**
When MarketStrategyRequest.meeting_context is present, skip the fresh-request readiness clarification gate and proceed to strategy generation using the bounded prior meeting briefing and follow-up question. Leave readiness behavior unchanged for ordinary Market Strategy requests.

**Why**
The meeting-follow-up router only supplies meeting_context after finding an existing authenticated meeting briefing. This is a reliable boundary for reusing established context without weakening clarification requirements for new strategy requests.

**Alternatives considered**
- Copy geography and other fields into a new flat request: duplicates context transformation and still leaves the readiness gate vulnerable to treating the follow-up as a new objective.
- Skip readiness for all strategy requests: would regress ordinary requests that genuinely need missing context.
- Route the follow-up back to Meeting Preparation: would violate the intended Market Strategy ownership for explanatory follow-ups.

**Trade-offs**
Strategy follow-ups trust the completed meeting briefing as their context baseline. If a follow-up introduces a materially new scope, the strategy response should state assumptions or the user can start a new strategy request.

**Affected code**
- packages/agents/src/multimodal_rag/agents/market_strategy.py::MarketStrategyAgent.run
- tests/test_market_strategy_agent.py::MarketStrategyAPITests.test_meeting_question_follow_up_returns_questions
- docs/flow.md

**Verification**
The meeting-question regression test now passes without invoking readiness generation, and the existing full strategy and API tests remain covered by the full test suite.

---

## DEC-2026-09-10-02 - Preserve the requested deliverable in strategy follow-ups

**Status:** Accepted

**Context**
Meeting follow-ups were correctly routed to Market Strategy, but its standard strategy prompt and response schema forced a generic executive strategy. A request for additional questions therefore returned three strategy paragraphs instead of questions for the CMO to ask.

**Decision**
Add an optional meeting_questions list to the existing MarketStrategyResponse and StrategyDraft contracts. When meeting context is present, instruct Market Strategy to match the requested meeting deliverable: populate meeting_questions with new, non-duplicative questions when questions are requested, and use executive_summary for focused explanations otherwise. Render meeting_questions directly in MarketStrategyMessage.

**Why**
The follow-up remains owned by Market Strategy as required, while its response now preserves the user's requested output type without changing normal strategy responses.

**Alternatives considered**
- Route question requests back to Meeting Preparation: violates the requirement that explanatory and coaching follow-ups use Market Strategy.
- Put questions into recommended_next_actions: mislabels the content and makes the UI misleading.
- Return free-form text outside MarketStrategyResponse: breaks the existing typed API and saved-chat rendering contract.

**Trade-offs**
The strategy response has one additional optional field, and the model prompt must choose the requested meeting deliverable. Existing callers remain compatible because the field defaults to an empty list.

**Affected code**
- packages/agents/src/multimodal_rag/agents/models.py::MarketStrategyResponse
- packages/agents/src/multimodal_rag/agents/market_strategy.py::StrategyDraft
- packages/agents/src/multimodal_rag/agents/market_strategy.py::MarketStrategyAgent._develop_strategy
- apps/frontend/src/App.jsx::MarketStrategyMessage
- tests/test_market_strategy_agent.py
- docs/flow.md

**Verification**
The new strategy-agent regression test verifies that a meeting question follow-up returns meeting_questions and includes the meeting-specific output instruction. Full Python tests and the frontend build are rerun after this change.

---

## DEC-2026-08-27-01 - Combine market and competitor intelligence in one dual-source agent

**Status:** Accepted

**Context**
The former Market Trend Agent only analyzed scoped RAG evidence, while fresh Tavily search was exposed as a separate frontend workflow. Users selecting market intelligence need one query-aware result combining internal context with current external evidence.

**Decision**
Rename the agent to Market Intelligence Agent, inject `WebSearchClient` alongside `RAGClient`, generate a bounded research plan, normalize and deduplicate both evidence types, and return separate market-trend, competitor, opportunity, and risk sections. Individual source failures produce an attributed partial result when the other source remains usable.

**Why**
This keeps the responsibility boundary in one agent, preserves source provenance for downstream consumers, and lets the agent discover relevant competitors from the user’s question without creating a separate competitor agent.

**Alternatives considered**
- Keep web research as a separate UI mode: requires users to select and combine outputs manually.
- Reuse the Tavily report workflow: loses item-level evidence and makes source linking less reliable.
- Create a Competitor Agent: duplicates orchestration and violates the requested ownership boundary.

**Trade-offs**
The agent makes bounded external searches and may return partial results when RAG indexes or web credentials are unavailable. The market-intelligence endpoint is a breaking rename; the standalone web-research API remains for existing callers.

**Affected code**
- `packages/agents/src/multimodal_rag/agents/market_intelligence.py`
- `packages/agents/src/multimodal_rag/agents/models.py`
- `services/api/src/multimodal_rag/api/main.py` and `router.py`
- `apps/frontend/src/App.jsx` and `api.js`

**Verification**
Targeted Market Intelligence, web-search, and company-research tests pass; Python compilation and the frontend production build pass.

The implementation also preserves the distinction between unavailable generation configuration (HTTP 503) and provider/model failures (HTTP 502).

The research-plan and analysis prompts explicitly enumerate the required market and competitor categories, while requiring concise coverage only for evidence-supported findings.

Model-only prose signals are treated as optional internal observations; final response findings still require validated source IDs.

Malformed or unlinked model candidates are omitted as partial-result limitations rather than failing an otherwise usable intelligence request.

---

## DEC-2026-08-28-01 - Simplify the research composer and make company focus explicit

**Status:** Accepted

**Context**
The research workspace needed to match the company-research reference while
removing implementation-oriented controls. Market Intelligence also needed a
clear optional way to focus fresh research on a named company or official URL.

**Decision**
Use the existing Ingest documents, Ask the intelligence base, and RAG Trace
workspaces as the top navigation. Remove visible project, user-scope, and
evidence controls; send a fixed `top_k=5` for RAG and Market Intelligence
requests. Add optional company name and company URL fields only in Market
Intelligence mode, and add a bounded direct company-focused web query when
either field is supplied.

**Why**
The backend still receives its required stable user identity without exposing
tenant-scoping mechanics in the primary research UI. A fixed top-five depth
matches the requested default and makes retrieval behavior consistent. Adding
the company focus to the actual search list makes the optional fields alter
research rather than being decorative metadata.

**Alternatives considered**
- Keep the advanced scope and evidence controls: they conflict with the
  requested simplified interface.
- Send company details only as analysis context: the research planner could
  omit the company from fresh-web queries.
- Create a separate company-research mode: duplicates the existing Market
  Intelligence workflow.

**Trade-offs**
Users cannot tune retrieval depth in the UI. A company URL is treated as a
research focus string and is not independently verified before the provider
search executes.

**Affected code**
- `apps/frontend/src/App.jsx::App`
- `apps/frontend/src/api.js::askQuestion`
- `apps/frontend/src/api.js::analyzeMarketIntelligence`
- `packages/agents/src/multimodal_rag/agents/models.py::AgentRequest`
- `packages/agents/src/multimodal_rag/agents/market_intelligence.py::MarketIntelligenceAgent.run`
- `tests/test_market_intelligence_agent.py::MarketIntelligenceAgentTests`

**Verification**
The focused Market Intelligence test suite passes, including the supplied
company query case. The Vite production build and Python compilation succeed.

---

## DEC-2026-08-28-02 - Apply liquid glass at the conversation level

**Status:** Accepted

**Context**
RAG and Market Intelligence messages render through separate response
components, but the requested visual treatment applies to the complete
conversation once an answer is present.

**Decision**
Apply the tinted liquid-glass treatment to the shared
`.conversation.has-messages` container. Use blurred, non-interactive gradient
layers behind the message list and a translucent, backdrop-filtered message
surface above them.

**Why**
The existing `has-messages` state already covers both response modes, so this
creates one consistent presentation without duplicating styles in each message
component or changing response data.

**Alternatives considered**
- Style each answer card independently: does not create a complete
  conversation background and duplicates visual rules.
- Add a separate React state for answer mode: unnecessary because populated
  messages already indicate the desired visual state.

**Trade-offs**
Backdrop blur can be modestly more expensive on low-end devices. The effect is
decorative and remains behind the readable message content.

**Affected code**
- `apps/frontend/src/styles.css::.conversation.has-messages`

**Verification**
The frontend production build succeeds; both RAG and Market Intelligence use
the shared populated-conversation render path.

---

## DEC-2026-08-28-03 - Keep source metadata within responsive cards

**Status:** Accepted

**Context**
Long document names and section titles in RAG source cards could force the
flex text column beyond its card boundary. The conversation copy also needed a
slightly more prominent but still restrained reading weight.

**Decision**
Allow the source-card text flex item to shrink with `min-width: 0`, preserve
the existing single-line ellipsis behavior, and apply a one-pixel larger
medium-weight type scale to populated conversation content.

**Why**
The explicit shrink boundary fixes overflow at every viewport width without
changing source data. Medium weight and a one-pixel size increase improve
readability without the visual density of bold body copy.

**Alternatives considered**
- Wrap section titles over multiple lines: increases source-card height and
  makes a multi-card result visually uneven.
- Shorten source metadata in the API: loses information for other clients.

**Trade-offs**
Truncated labels require users to infer the hidden tail from the visible
prefix; the source data remains available in the response details.

**Affected code**
- `apps/frontend/src/styles.css::.conversation.has-messages`
- `apps/frontend/src/styles.css::.source-card`

**Verification**
The Vite production build succeeds. Source-card text now has a shrinkable flex
column before the existing ellipsis rules apply.

---

## DEC-2026-08-28-04 - Align ingestion surfaces with the light glass workspace

**Status:** Accepted

**Context**
The ingestion workspace still used the previous dark-card palette after the
primary research workspace adopted light tinted liquid glass. Its low-contrast
text was difficult to read against the dark surfaces.

**Decision**
Apply translucent light-blue glass surfaces with dark text to ETL upload,
pipeline, status, and stored-artifact elements. Retain green for browse/upload
actions, active pipeline progress, completed stages, and stored confirmations.

**Why**
The shared visual language makes the workspace coherent while preserving green
as the established operational-success and action cue.

**Alternatives considered**
- Keep the dark ETL theme: visually conflicts with the redesigned workspace.
- Remove green state cues: reduces the visibility of actionable and successful
  ingestion states.

**Trade-offs**
The glass layers use backdrop blur, which can add minor rendering work on
lower-powered devices.

**Affected code**
- `apps/frontend/src/styles.css::.etl-workspace`
- `apps/frontend/src/styles.css::.etl-upload-card`
- `apps/frontend/src/styles.css::.etl-pipeline-card`
- `apps/frontend/src/styles.css::.etl-status-banner`
- `apps/frontend/src/styles.css::.etl-history-item`

**Verification**
The frontend production build succeeds. ETL state classes continue to control
the active, complete, and stored green indicators.

---

## DEC-2026-08-28-05 - Request detailed Market Intelligence briefs

**Status:** Accepted

**Context**
The Market Intelligence analysis instruction emphasized concise summaries and
fields, producing reports that did not fully synthesize the available RAG and
web evidence.

**Decision**
Replace the brevity-oriented analysis instructions with an evidence-dependent
detailed-brief target: a 3-5 paragraph executive summary, developed finding
explanations, and indicative counts for market, competitor, opportunity, and
risk sections. Retain the existing JSON contract and exact evidence-ID
requirements.

**Why**
Prompt-level depth guidance changes the agent output without altering the API
schema, renderer, or evidence-validation boundary. The instruction explicitly
prohibits padding and unsupported claims.

**Alternatives considered**
- Increase frontend text display only: does not make the analysis more useful.
- Remove validation to allow free-form reports: risks unsupported claims and
  breaks the structured response contract.
- Enforce fixed minimum findings in code: can manufacture empty or weak
  sections when evidence is limited.

**Trade-offs**
Detailed briefs consume more generation tokens and can take longer. Sparse
evidence should still yield fewer findings rather than invented coverage.

**Affected code**
- `packages/agents/src/multimodal_rag/agents/market_intelligence.py::MarketIntelligenceAgent._analyze`
- `tests/test_market_intelligence_agent.py::MarketIntelligenceAgentTests.test_prompts_cover_market_and_competitor_research_categories`

**Verification**
The focused Market Intelligence test suite passes, including assertions that
the detailed-report instructions are present. Python compilation succeeds.

---

## DEC-2026-08-28-06 - Add a service-owned PostgreSQL user-memory boundary

**Status:** Accepted

**Context**
The API receives useful user business context but had no persistent,
user-isolated representation for future personalization. The project has no
existing database or ORM convention to extend.

**Decision**
Add `multimodal_rag.memory` as a small service layer: `MemoryExtractor` uses
the configured generation model only to return validated JSON candidates;
`MemoryService` filters sensitive values and decides insert, update, or ignore;
`PostgresMemoryRepository` performs parameterized user-scoped PostgreSQL
operations. Store stable profile facts in `user_profiles.profile_data` JSONB
and all learned facts in `user_memories`, unique by user, type, and key.

The API creates the service in `create_app()` and invokes it safely for both
`/answer` and `/agents/market-intelligence`. Memory is disabled unless
`RAG_MEMORY_DATABASE_URL` is configured.

**Why**
This satisfies the separation between LLM extraction and application-owned
persistence while avoiding a new autonomous agent or changes to existing RAG
and Market Intelligence behavior. JSONB keeps the small profile flexible,
while the unique memory identity supports deterministic updates and duplicate
suppression.

**Alternatives considered**
- Let the extraction LLM call PostgreSQL: violates application ownership and
  makes validation/auditing unreliable.
- Add an ORM and migration framework: introduces a broader persistence stack
  where none currently exists.
- Persist to local files: does not meet the PostgreSQL requirement or provide
  database-level user isolation.

**Trade-offs**
Each eligible message may require an extra generation call and database
operation. Memory is intentionally not supplied to response-generation prompts
in this increment; downstream components can call `get_user_context(user_id)`
when personalization is intentionally introduced.

**Affected code**
- `packages/agents/src/multimodal_rag/memory/models.py`
- `packages/agents/src/multimodal_rag/memory/extractor.py`
- `packages/agents/src/multimodal_rag/memory/service.py`
- `packages/agents/src/multimodal_rag/memory/repository.py`
- `services/api/migrations/001_user_memory.sql`
- `services/api/src/multimodal_rag/api/config.py`
- `services/api/src/multimodal_rag/api/main.py`
- `services/api/src/multimodal_rag/api/router.py`
- `tests/test_memory_service.py`
- `tests/test_api.py`

**Verification**
The repository knowledge graph was freshly indexed before implementation and
identified `create_app()` plus the two authenticated message routes as the
integration boundary. Focused memory, API, and Market Intelligence tests pass
(46 total), and modified Python modules compile.

---

## DEC-2026-08-28-07 - Persist user accounts and conversations in PostgreSQL

**Status:** Accepted

**Context**
The prior sign-in screen verified one environment-configured credential and
kept conversations only in browser state, so new users could not register and
past chats disappeared after signing out or refreshing.

**Decision**
Use PostgreSQL for `users`, hashed session tokens, conversations, and chat
messages. Passwords are salted scrypt hashes; bearer tokens are random values
whose SHA-256 hashes are stored server-side. A persistent session resolves the
authenticated user ID, which must match every user-scoped RAG, agent, upload,
and chat request. The React client creates/logs in users and restores their
conversation list through dedicated chat endpoints.

**Why**
This provides real account registration and durable chat history without
exposing plaintext passwords or trusting the browser's claimed user ID.

**Alternatives considered**
- Browser-local accounts/chats: not durable or multi-device and cannot safely
  authenticate a user.
- Plaintext passwords: unacceptable credential exposure risk.
- Stateless signed tokens: would not support server-side session expiry or
  revocation checks without additional infrastructure.

**Trade-offs**
Persistent accounts require `RAG_DATABASE_URL` (or the existing
`RAG_MEMORY_DATABASE_URL` fallback) and PostgreSQL availability. Sessions are
valid for 14 days unless the database record is removed.

**Affected code**
- `services/api/src/multimodal_rag/api/accounts.py`
- `services/api/src/multimodal_rag/api/router.py`
- `services/api/src/multimodal_rag/api/main.py`
- `services/api/src/multimodal_rag/api/schemas.py`
- `apps/frontend/src/App.jsx`
- `apps/frontend/src/api.js`
- `services/api/migrations/001_user_memory.sql`
- `services/api/run_backend.py`
- `README.md`

**Verification**
Focused API tests cover signup, sign-in, conversation persistence, and
cross-user rejection. The frontend production build and Python compilation
succeed.

---

## DEC-2026-09-10-06 - Add fail-open LangSmith backend observability

**Status:** Accepted

**Context**
The platform already emits an application trace ID, RAG diagnostics, and per-stage latency logs, but those signals are local and difficult to correlate across API, agent, retrieval, model, web-search, and source-safety execution.

**Decision**
Add optional backend-only LangSmith tracing through a shared multimodal_rag.rag.observability module. API route wrappers create root chain spans, nested agent/RAG/retriever/tool spans capture orchestration, and the shared Gemini generation function creates LLM spans. A FastAPI middleware binds each supported request to an environment-specific LangSmith project.

**Why**
The project calls Google GenAI directly and does not use a LangChain runtime, so explicit trace contexts provide the required hierarchy without replacing existing behavior. The shared wrapper preserves FastAPI signatures, honors runtime configuration, and makes serialization/redaction consistent.

**Alternatives considered**
- Replacing the existing trace ID and Developer Lab diagnostics: rejected because those are part of the current client/debugging contract.
- Global LangSmith environment-only decorators: rejected because missing configuration must disable tracing cleanly and route payloads require application-specific safe serialization.
- Frontend tracing: rejected because LangSmith credentials must remain server-side.

**Trade-offs**
With full-content tracing enabled, prompts, responses, chat context, document evidence, and retrieved chunks are sent to the configured LangSmith workspace. Secret-like fields and transport objects are always omitted. Initial sampling is 100%, so production cost and retention must be monitored.

**Affected code**
- packages/rag-core/src/multimodal_rag/rag/observability.py
- services/api/src/multimodal_rag/api/main.py
- services/api/src/multimodal_rag/api/router.py
- packages/rag-core/src/multimodal_rag/rag/trace.py
- packages/rag-core/src/multimodal_rag/rag/generation/answer_generator.py
- packages/agents/src/multimodal_rag/agents/market_intelligence.py
- packages/agents/src/multimodal_rag/agents/market_strategy.py
- packages/agents/src/multimodal_rag/agents/meeting_preparation.py
- packages/web-search/src/multimodal_rag/web_search/source_guard.py

**Verification**
Focused serializer/configuration tests pass. Full Python tests and knowledge-graph reindex are required after integration.

---

## DEC-2026-09-15-01 - Generate PPTX from trusted chat context through Presenton

**Status:** Accepted

**Context**
Users need an editable presentation from a completed Meeting Preparation
conversation, including applicable Market Strategy follow-ups. The generated
deck must not trigger new RAG, web, agent, or Knowledge Graph business-content
retrieval, and the browser must not be trusted to provide arbitrary assistant
content.

**Decision**
Add a deterministic `PPTContextBuilder`, an application-owned
`PresentationGenerationService`, and an isolated `PresentonClient`. The builder
reads the authenticated user's PostgreSQL chat, selects the newest completed
Meeting Preparation payload, includes eligible later Market Strategy payloads,
and excludes superseded revisions, unrelated agents, source metadata, traces,
and internal fields. The API validates the Presenton template, disables web
search, validates the provider's same-origin PPTX artifact, and streams the
bytes to the browser without adding presentation persistence.

**Why**
Existing assistant payloads already carry structured `agent` provenance and
PostgreSQL chat ownership, so no new message schema or state table is needed.
The synchronous Presenton v1 API matches the existing synchronous FastAPI
route style, while server-side artifact proxying protects provider credentials
and enforces a validated download boundary.

**Alternatives considered**
- Sending full frontend chat content: rejected because it would allow
  tampering and could cross the trusted conversation boundary.
- Calling Meeting Preparation or Market Strategy again: rejected because PPT
  creation must compose existing conversation content only.
- Adding a separate autonomous PPT agent: rejected because deterministic
  filtering and Presenton composition are sufficient.
- Persisting generated presentations in PostgreSQL: deferred because the first
  version returns a validated binary immediately and does not require history
  or asynchronous polling.
- Returning the provider URL directly: rejected because it would expose an
  unvalidated external artifact path and weaken API-owned access control.

**Trade-offs**
Generation requires persistent chat storage and a reachable Presenton
deployment. Synchronous generation keeps the first version small but holds an
HTTP request for the provider timeout; no automatic retry is used to avoid
duplicate provider charges. Generic non-factual visuals are allowed, while
Presenton web search is explicitly disabled.

**Affected code**
- `services/api/src/multimodal_rag/api/presentations.py`
- `services/api/src/multimodal_rag/api/router.py`
- `services/api/src/multimodal_rag/api/main.py`
- `services/api/src/multimodal_rag/api/config.py`
- `apps/frontend/src/App.jsx`
- `apps/frontend/src/api.js`
- `tests/test_presentations.py`

**Verification**
Focused presentation tests cover context selection, revisions, duplicate
strategy suppression, template/slide forwarding, web-search disabling,
authentication, chat isolation, and binary download responses. Python
compilation and the Vite production build pass.

---

## DEC-2026-09-15-02 - Accept the current Presenton template response envelope

**Status:** Accepted

**Context**
Presenton's template endpoint returns a response object containing an `items`
array and uses `layout_count` for each template. The CMO API expected the older
bare-array shape and `total_layouts`, so valid template responses were mapped to
a 502 error and the frontend could not open the presentation configuration.

**Decision**
Make `PresentonClient.list_templates()` accept both the legacy bare array and
the current `{ "items": [...] }` envelope. Map `layout_count` with fallback to
the legacy `total_layouts` field.

**Why**
This keeps the provider adapter tolerant across Presenton versions without
weakening the validation that each returned template has an ID.

**Alternatives considered**
- Pinning or downgrading the Presenton deployment: rejected because the API
  contract can be handled safely at the integration boundary.
- Removing layout metadata: rejected because the field is useful to the
  frontend and already exists in the internal response model.

**Trade-offs**
The adapter carries a small compatibility branch until the legacy response
shape is no longer needed.

**Affected code**
- `services/api/src/multimodal_rag/api/presentations.py::PresentonClient.list_templates()`
- `tests/test_presentations.py::PresentonClientTests`

**Verification**
Added a regression test using the live Presenton response shape. The local
Presenton endpoint returned HTTP 200 with an `items` array containing
`layout_count` values.

---

## DEC-2026-09-15-03 - Normalize Presenton artifact URL origins

**Status:** Accepted

**Context**
Presenton's v1 generation response returns an absolute PPTX URL. The artifact
validator compared `urlparse().port` directly, so a valid URL containing the
explicit HTTPS default port (`:443`) could be rejected as unsafe even though
it resolved to the configured Presenton origin.

**Decision**
Compare the generated artifact URL and configured Presenton URL using scheme,
hostname, and effective port. Treat HTTPS without a port and HTTPS with `:443`
as the same origin, and apply the equivalent HTTP normalization for `:80`.
Continue rejecting other hosts, schemes, and ports.

**Why**
This accepts valid provider URL serialization variants while retaining the
same-origin SSRF boundary around the server-side artifact download.

**Alternatives considered**
- Accepting any HTTPS artifact URL: rejected because the provider response
  must not be allowed to redirect the API to an unrelated host.
- Stripping the provider's absolute URL to its path without validation:
  rejected because it would hide origin mismatches instead of detecting them.

**Trade-offs**
The adapter now has a small URL normalization helper, but the allowed origin
remains exactly the configured Presenton host and effective port.

**Affected code**
- `services/api/src/multimodal_rag/api/presentations.py::PresentonClient._safe_artifact_url()`
- `tests/test_presentations.py::PresentonClientTests`

**Verification**
Added coverage for explicit HTTPS `:443` acceptance and a different-host
rejection.

---

## DEC-2026-09-15-04 - Preserve trusted Markdown and provider diagnostics

**Status:** Accepted

**Context**
Presenton generation was returning HTTP 500 after the request reached the
provider. The CMO API discarded the provider response body, leaving the
frontend with only a generic failure message. The presentation source is
already a deterministic Markdown transformation of trusted chat content.

**Decision**
Send Presenton's documented `content_generation: "preserve"` option and expose
the provider's bounded `detail`, `message`, or `error` diagnostic in the
`PresentationProviderError`. If the response is not structured JSON, use a
short whitespace-normalized text fallback.

**Why**
Preserving the source Markdown better matches the chat-export contract, while
bounded diagnostics distinguish provider quota, model, validation, and service
failures without exposing an unbounded raw response.

**Alternatives considered**
- Retrying HTTP 500 automatically: rejected because generation may have
  succeeded upstream and a retry can duplicate provider charges.
- Returning the complete provider body: rejected because it can be large,
  unstable, or contain implementation details not intended for the browser.

**Trade-offs**
Provider error wording becomes visible to authenticated users and may vary by
Presenton deployment, but the response is capped at 500 characters.

**Affected code**
- `services/api/src/multimodal_rag/api/presentations.py::PresentonClient.generate()`
- `services/api/src/multimodal_rag/api/presentations.py::PresentonClient._raise_for_provider()`
- `tests/test_presentations.py::PresentonClientTests`

**Verification**
Added coverage for the preserve-generation option and propagation of a
structured provider diagnostic.

---

## DEC-2026-09-15-05 - Canonicalize Presenton artifact paths to the configured provider

**Status:** Superseded by DEC-2026-09-15-06

**Context**
Presenton can return the same generated artifact with its container, proxy, or
public URL origin. Requiring that returned origin to exactly equal
`PRESENTON_BASE_URL` rejected valid PPTX downloads after successful generation.

**Decision**
Parse a returned artifact reference, retain only its HTTP(S) path, and resolve
that path against the configured Presenton base URL. Reject empty paths and
non-HTTP(S) URI schemes.

**Why**
The API connects only to the explicitly configured Presenton origin, so a
provider-supplied host or port cannot redirect the server-side download while
valid deployments behind containers and reverse proxies remain compatible.

**Alternatives considered**
- Keep exact origin matching: rejected because Presenton legitimately emits
  internal or public origins that differ from the API configuration.
- Download the returned absolute URL directly: rejected because it weakens the
  server-side SSRF boundary.

**Trade-offs**
The configured Presenton server must expose the returned artifact path. Query
parameters and fragments from a provider response are intentionally ignored.

**Affected code**
- `services/api/src/multimodal_rag/api/presentations.py::PresentonClient._safe_artifact_url()`
- `tests/test_presentations.py::PresentonClientTests.test_safe_artifact_url_uses_configured_origin_for_provider_paths()`

**Verification**
Focused tests cover public and internal provider URL representations resolving
to the configured origin and rejection of a non-HTTP(S) artifact URI.

---

## DEC-2026-09-15-06 - Preserve verified Presenton Cloud artifact URLs

**Status:** Superseded by DEC-2026-09-15-07

**Context**
Canonicalizing every absolute artifact URL to `PRESENTON_BASE_URL` prevented
server-side request forgery, but it also changed valid Presenton Cloud artifact
URLs. The configured API host then returned nginx 404 for the rewritten path.

**Decision**
Use a returned absolute URL unchanged only when it is the configured origin or
an HTTPS host in the `presenton.ai` Cloud DNS boundary and the configured host
is also in that boundary. Continue rebuilding relative and recognized internal
container paths on the configured origin; reject all other external hosts.

**Why**
Presenton Cloud can serve generated files from a different provider-owned host,
including signed URLs. Preserving that URL keeps its routing and query string,
while hostname and scheme checks retain a narrow download allowlist.

**Alternatives considered**
- Rebuild every artifact URL on the configured API host: superseded because it
  causes valid Cloud artifact URLs to return 404.
- Trust any HTTPS artifact URL: rejected because it permits a provider response
  to redirect the API's server-side download to an unrelated host.

**Trade-offs**
The Cloud exception deliberately depends on Presenton's `presenton.ai` domain.
Other managed hosting domains must be explicitly designed and added if the
provider changes its artifact-delivery architecture.

**Affected code**
- `services/api/src/multimodal_rag/api/presentations.py::PresentonClient._safe_artifact_url()`
- `tests/test_presentations.py::PresentonClientTests.test_safe_artifact_url_allows_verified_provider_artifacts_and_normalizes_internal_paths()`
- `docs/presenton.md`

**Verification**
Focused tests verify configured-origin and Presenton Cloud artifact URL
preservation, internal-path canonicalization, and rejection of file and
unrelated HTTPS hosts.

---

## DEC-2026-09-15-07 - Support signed public PPTX artifact URLs without credential forwarding

**Status:** Accepted

**Context**
Presenton can return a generated PPTX through a provider-managed storage or CDN
domain outside `presenton.ai`. A fixed Presenton-domain allowlist still rejects
that valid HTTPS artifact before it can be downloaded.

**Decision**
Accept absolute public HTTPS artifact URLs on port 443, retain their query
string, and download them without the Presenton API bearer key. Continue using
the bearer key only for the configured origin and verified Presenton Cloud
hosts. Relative paths and recognized internal paths from a local Presenton
configuration are rebuilt on the configured origin; local, private-literal-IP,
credential-bearing, and non-HTTPS URLs are rejected for cloud configurations.

**Why**
Signed artifact URLs authorize access through their own query parameters, so
they do not need—and must not receive—the Presenton API key. This accommodates
provider storage/CDN routing without forwarding credentials to an external
host.

**Alternatives considered**
- Maintain a growing fixed list of provider CDN hostnames: rejected because
  artifact-delivery hosts can change without an API contract change.
- Forward the Presenton bearer key to all accepted artifact hosts: rejected
  because it would expose an API credential outside the provider API boundary.
- Reject all non-Presenton hosts: rejected because it breaks valid signed
  provider artifact URLs.

**Trade-offs**
The public-HTTPS check cannot prove DNS ownership of a hostname, but it blocks
known local targets and never forwards credentials to an external artifact
host. HTTP redirects remain disabled by the HTTP client default.

**Affected code**
- `services/api/src/multimodal_rag/api/presentations.py::PresentonClient.download()`
- `services/api/src/multimodal_rag/api/presentations.py::PresentonClient._safe_artifact_url()`
- `services/api/src/multimodal_rag/api/presentations.py::PresentonClient._artifact_headers()`
- `tests/test_presentations.py::PresentonClientTests`

**Verification**
Focused tests cover signed public-HTTPS URLs, local/non-HTTPS rejection,
internal-path normalization, and absence of forwarded credentials on an
external artifact download. Unsafe-path errors include only the URL scheme and
hostname, never signed query parameters.

---

## DEC-2026-09-15-08 - Use Presenton async tasks for honest progress and local previews

**Status:** Accepted

**Context**
The synchronous PPTX endpoint could only show a spinner. Users also need to
compare templates and inspect a completed deck inside the local application.

**Decision**
Use Presenton's async task and status APIs. The client shows actual submitted,
generating, and downloading stages; it offers a PPTX download and requests a
PDF export for a local iframe preview. Template cards use provider thumbnails
when supplied, otherwise a clearly labeled illustrative layout guide.

**Why**
Presenton exposes task state but no per-slide percentage, so staged progress is
truthful. PDF is browser-renderable and previews the generated deck locally.

**Trade-offs**
PDF preview adds a provider export request after generation. Task state is
short-lived client state; generated presentations are not persisted by the CMO
API.

**Affected code**
- `services/api/src/multimodal_rag/api/presentations.py`
- `services/api/src/multimodal_rag/api/router.py`
- `apps/frontend/src/App.jsx`
- `apps/frontend/src/api.js`
- `apps/frontend/src/styles.css`

**Verification**
Focused API tests cover task submission, polling, PPTX download, and PDF
preview. The Vite production build passes.

---

## DEC-2026-09-15-09 - Use a wide 16:9 frontend-only presentation preview

**Status:** Accepted

**Context**
The local PDF preview inherited the presentation configuration modal's narrow
width and tall frame, making a landscape slide deck difficult to inspect.

**Decision**
When a local preview URL is present, widen only that modal and render the PDF
in a 16:9 frame at roughly 62% of viewport width, with a responsive mobile
fallback.

**Why**
This makes the viewer match the landscape presentation format without changing
the API, PDF generation, or PPTX download path.

**Trade-offs**
The preview modal occupies more screen space while open; small screens use the
available width and retain a safe viewport-height cap.

**Affected code**
- `apps/frontend/src/App.jsx`
- `apps/frontend/src/styles.css`

**Verification**
The Vite production build passes. No backend code or contracts changed.

---

## DEC-2026-09-15-10 - Open the embedded PDF preview at page width

**Status:** Accepted

**Context**
The wide preview frame can still inherit a low zoom setting from the browser's
built-in PDF viewer, leaving slides small despite the available space.

**Decision**
Append the PDF viewer's `page-width` zoom fragment to the local blob URL used
by the iframe.

**Why**
Page-width zoom makes each landscape slide occupy the viewer width rather than
showing multiple small pages vertically.

**Affected code**
- `apps/frontend/src/App.jsx`

**Verification**
The Vite production build passes. No backend code or contracts changed.
