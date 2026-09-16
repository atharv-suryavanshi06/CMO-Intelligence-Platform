# System Execution Flow

## Current Market Strategy Flow

`apps/frontend/src/App.jsx::submitQuestion()`
    → `apps/frontend/src/api.js::createMarketStrategy()` sends the strategy request including `document_context: attachedDoc?.text`
    → `POST /agents/market-strategy`
    → `services/api/src/multimodal_rag/api/router.py::create_market_strategy()`
    → `MemoryService.process_message(user_id, chat_id, message)` stores explicit durable facts into `chat_memories` and `chat_profiles`
    → `MemoryService.get_user_context(user_id, chat_id)` loads the persisted profile/business context strictly for this chat (empty for a new chat)
    → **when no clarification is pending**: `api/conversation_reuse.py::eligible_exchanges()`/`exact_repeat()` check the last 10 assistant turns in this chat for a normalized-text match on the objective; a hit replays the stored `MarketStrategyResponse` verbatim (new `trace_id`, `reused_from_message_id` set) with no Market Intelligence or generation call
    → `MarketStrategyAgent.classify_message(objective, prior_exchanges=...)` gates off-topic/greeting messages before any pending state is touched
    → `_strategy_research_request()` creates an `AgentRequest` containing the objective, `document_context`, operational scope, and stored context, plus `prior_exchanges`/`prior_sources` when the chat has reuse-eligible history
    → `MarketIntelligenceAgent.run()` converts `document_context` into citable `AgentEvidence` (`kind="document"`, chunk ID `doc:attached_page_...`), retrieves scoped RAG and Tavily web evidence, and applies the `SOURCE WEIGHTING POLICY` (~60% attached document / ~25% web / ~15% RAG)
    → `MarketStrategyAgent.run(request, intelligence, user_context)`
    → `_assess_readiness()` evaluates readiness with full visibility into the attached document, treating requirements covered by the document as satisfied
    → `_develop_strategy()` combines attached document evidence with research sources and enforces the `SOURCE WEIGHTING POLICY` (~60% attached document / ~25% web / ~15% RAG)
    → `_response()` copies the validated intelligence categories into `MarketStrategyResponse`
    → `MarketStrategyMessage` renders the strategic executive summary, verified sources, collapsible strategic breakdown, and prominently displays **What's Trending in the Market** and **Competitor Insights** at the last of the response

`MarketStrategyAgent._has_supported_intelligence()` accepts a Market Intelligence response with at least one approved source even when no normalized finding category survived validation. Responses with no approved sources remain blocked. When readiness returns `needs_input`, `PostgresUserStore.set_strategy_state()` stores the original strict request, the clarification question, missing context keys, and serialized Market Intelligence response on the user-scoped chat.

When the user replies to a pending clarification:
- Memory processing (`_remember_message`) and direct candidate upsert into PostgreSQL run **synchronously** (not deferred to background tasks), guaranteeing that `MemoryService.get_user_context()` immediately reflects the clarified attributes (e.g. `target_geography`).
- `classify_message` receives `prior_exchanges_text = f"Clarification requested: {clarif_q}"` so terse replies (e.g., "India") are accurately judged in context.
- The stored `strategy_request` objective is rehydrated and supplemented with `(Target/Clarification: <answer>)`.
- `MarketIntelligenceAgent` is not called again; the rehydrated intelligence is handed directly to `MarketStrategyAgent.run()`.
- `_assess_readiness()` detects the missing items are satisfied and generates the full 3-paragraph executive strategy on the first answer turn.
- Upon completion or terminal failure, `clear_strategy_state()` purges the pending state.

Reuse detection (the exact-repeat check and `prior_exchanges` passed into `classify_message`) is skipped entirely whenever `pending_state` is present: a clarification answer is not a reuse candidate, and scoring it against the window would mis-bucket it. The off-topic/greeting gate still runs unconditionally, before pending state is touched, so an unrelated aside sent mid-clarification is declined without discarding or answering the pending question.

## Meeting Preparation Flow

`apps/frontend/src/App.jsx::agentMode === "meeting_preparation"`:
    → User selects "Meeting Preparation Agent" in Mode dropdown
    → Liquid glass tinted modal popup dialog opens capturing:
        - Mandatory: `Meeting Title`, `Meeting Objective`
        - Optional: `Attendee Context`, `Product / Offering`, `Industry / Vertical`, `Geographic Location`, `Budget / Financials`, `Key Competitors`, `Timeline / Horizon`, and document attachment
    → Submitting form calls `apps/frontend/src/api.js::prepareMeeting()` with all populated parameters
    → `POST /agents/meeting-preparation`
    → `services/api/src/multimodal_rag/api/router.py::prepare_meeting()`
    → Authenticates user and loads user profile/memory context via `MemoryService`
    → Synthesizes augmented objective incorporating all provided meeting parameters
    → Invokes `MarketIntelligenceAgent.run()` (retrieves scoped RAG, Tavily web search, and attached document chunks with 60/25/15 weighting)
    → Invokes `MarketStrategyAgent.run()` (evaluates readiness and generates strategic opportunities/risks/actions)
    → Invokes `MeetingPreparationAgent.run()` (synthesizes executive brief, key facts, talking points, questions to ask, risks/watchouts with countermeasures, recommended actions, and highlights relevant trends & competitor moves)
    → Records assistant message to PostgreSQL `user_chat_messages` with `agent="meeting_preparation"`
    → `apps/frontend/src/App.jsx::MeetingPreparationMessage` renders comprehensive executive meeting brief in the chat stream with strategic context chips

### Meeting Follow-up Flow

After a completed meeting briefing is present in the current chat, submitQuestion() calls api.js::followUpMeeting() instead of starting a new meeting request.

When a saved chat is rehydrated, App.jsx::selectChat() keeps the mode as
meeting_preparation whenever any completed or partial meeting briefing is
present, even if a later strategy-coaching response is the latest assistant
message. This preserves the meeting follow-up route across reloads and recent
chat selection.

POST /agents/meeting-follow-up
    â†’ router.py::meeting_follow_up() loads the latest completed meeting briefing from the authenticated chat
    â†’ MeetingPreparationAgent.classify_follow_up() classifies the new message as modify_meeting or expand_strategy
    â†’ modify_meeting: reconstructs the meeting request, adds the revision instruction and prior briefing, and reuses prepare_meeting() to regenerate the complete meeting response
    â†’ expand_strategy: sends the follow-up question and bounded prior briefing to MarketIntelligenceAgent.run() and MarketStrategyAgent.run() through the existing strategy research path
    â†’ Persists the returned assistant payload with the selected agent identity
    â†’ App.jsx unwraps the routing envelope and renders MeetingPreparationMessage or MarketStrategyMessage according to the returned agent

For strategy follow-ups, MarketStrategyAgent.run() treats the completed briefing as sufficient context and skips the fresh-request readiness clarification gate. It suppresses the market-intelligence snapshot, including trends and competitor insights, unless the follow-up explicitly asks for trends, market updates, competitors, or competitive activity. MarketStrategyResponse includes meeting_questions when the user asks for additional questions; MarketStrategyMessage renders those questions directly above the normal strategy details.

The follow-up route is intentionally separate from the initial meeting endpoint contract, while both branches reuse the existing agent instances and orchestration functions.

**Key Components & Responsibilities:**
- `MeetingPreparationAgent`: Orchestrates upstream structured responses from `MarketIntelligenceAgent` and `MarketStrategyAgent`. Uses structured Gemini generation (`MeetingPreparationDraft`) with JSON schema validation and graceful fallback.
- `apps/frontend/src/App.jsx`: Manages liquid glass tinted modal dialog state, auto-opens upon mode selection, and renders `MeetingPreparationMessage` with executive hero banner, strategic context chips, talking points, questions, risk matrix, and source cards.

### Chat-scoped Presenton PPT generation

`apps/frontend/src/App.jsx::MeetingPreparationMessage()` renders `Create PPT`
only on the latest completed Meeting Preparation message in the active chat.
The button opens the existing modal visual style with dynamically loaded
Presenton templates and Auto/5/7/10 slide choices.

`apps/frontend/src/api.js::listPresentationTemplates()`
    -> `GET /presentations/templates`
    -> `services/api/src/multimodal_rag/api/router.py::list_presentation_templates()`
    -> `PresentationGenerationService.list_templates()`
    -> `PresentonClient.list_templates()` sends the provider bearer key to
       `GET /api/v1/ppt/template/all?include_defaults=true`
    -> accepts Presenton's `{items: [...]}` envelope (and the legacy array)
       and maps `layout_count` (or legacy `total_layouts`) to the API schema

On generation:

`apps/frontend/src/App.jsx::handleGeneratePresentation()`
    -> `apps/frontend/src/api.js::generatePresentation()` sends only `chat_id`, `template_id`, and nullable `slide_count`
    -> `POST /presentations/generate`
    -> `services/api/src/multimodal_rag/api/router.py::generate_presentation()` authenticates the bearer session and loads the owned chat through `PostgresUserStore.get_chat()`
    -> `multimodal_rag.api.presentations::PPTContextBuilder.build()` finds the newest completed `agent=meeting_preparation` payload
    -> the builder includes only subsequent completed/partial `agent=market_strategy` payloads, de-duplicates exact public payloads, and excludes user messages, unrelated agents, sources, traces, and internal metadata
    -> `PresentationGenerationService.start_from_chat()` validates the selected template and calls `PresentonClient.start_async()` with deterministic Markdown, `content_generation=preserve`, `web_search=false`, and chat-only instructions
    -> the frontend polls `GET /presentations/generate/{task_id}` for the real pending, completed, or error provider state
    -> on completion, `GET /presentations/generate/{task_id}/download` validates and returns the PPTX
    -> `GET /presentations/generate/{task_id}/preview` exports and streams a PDF into the local iframe preview

This path does not call RAG, any business agent, web search, the Knowledge
Graph, or external project documents. It requires persistent PostgreSQL chat
storage because legacy single-account mode has no trusted conversation record.


## Current Market Intelligence Flow

`apps/frontend/src/App.jsx::submitQuestion()`
    → `apps/frontend/src/api.js::analyzeMarketIntelligence()`
    → `POST /agents/market-intelligence`
    → `services/api/src/multimodal_rag/api/router.py::analyze_market_intelligence()`
    → `api/conversation_reuse.py::eligible_exchanges()`/`exact_repeat()` check the last 10 assistant turns in this chat for a normalized-text match on the objective; a hit replays the stored `MarketIntelligenceResponse` verbatim (new `trace_id`, `reused_from_message_id` set) with no retrieval, web search, or generation call
    → when there is reuse-eligible history but no exact match, `additional_context` gains `prior_exchanges` (formatted Q/A text) and `prior_sources` (the most recent turn's evidence, capped)
    → `MarketIntelligenceAgent.run()`
    → `_plan()` creates bounded intent-aware web queries, and - when `prior_exchanges` is present - also classifies `relation` (repeat/refinement/follow_up/new) so a refinement's `search_queries` cover only the delta, not the whole prior topic
    → `InProcessRAGClient.retrieve()` and `WebSearchClient.search()` collect scoped internal and fresh external evidence
    → `run()` merges any `prior_sources` (validated back into `AgentEvidence`, placed FIRST) with the freshly retrieved evidence
    → `_deduplicate()` and `_with_recency()` normalize evidence and provenance; merging prior evidence first means a duplicate keeps the prior turn's `chunk_id`, so a carried-over finding stays citable
    → `_analyze()` generates source-linked structured intelligence
    → `_build_findings()` validates evidence references and separates trends, competitor activity, opportunities, and risks
    → `MarketIntelligenceMessage` renders the response

Generation errors are classified at the agent boundary: missing model configuration returns `generation_unavailable`/HTTP 503, while an invalid or failed model response returns `generation_error`/HTTP 502. RAG and web-source failures remain attributed partial-result limitations when the other source is usable.

`_plan()` explicitly scopes searches across relevant market and competitor categories. `_analyze()` consolidates the complete evidence set into a concise CMO overview and source-linked findings, omitting unsupported categories rather than inventing coverage.

The analysis parser tolerates non-structured internal signal summaries, but preserves strict source-ID validation for every returned finding.

Candidates that cannot be normalized or linked to retrieved evidence are discarded before response construction, allowing the agent to return the remaining validated intelligence instead of a format-related 502.

### External Source Guard

`services/api/src/multimodal_rag/api/main.py::create_app()` constructs `SourceGuardService` from `APISettings` and injects it into `MarketIntelligenceAgent`.

`MarketIntelligenceAgent.run()` → `WebSearchClient.search()` → `SourceGuardService.filter_results()` → `SourceGuardService.validate_url()` (VirusTotal) → `SourceGuardService.validate_content()` (Check Point AI Guardrails with a `tool` message) → approved results become `AgentEvidence` → `MarketIntelligenceAgent._analyze()`.

`apps/frontend/src/App.jsx::MarketIntelligenceMessage()` renders response-level source cards for every RAG document and approved web URL returned by the agent.

`apps/frontend/src/App.jsx::App()` keeps saved-chat loading unchanged but renders the existing history list in a menu-controlled left drawer. When opened on desktop, the main panel reserves the drawer width and reflows inside the remaining viewport; on narrow screens, the drawer overlays without moving the workspace.

VirusTotal threat matches, malformed URLs, invalid security responses, missing credentials, and security-provider failures are all blocked. No rejected external result is included in the agent prompt or returned by Tavily Research.

## Entry Points

### Backend interpreter bootstrap

`apps/frontend/src/App.jsx::ETLWorkspace()`
    → re-executes with `.venv\Scripts\python.exe` when needed
    → `services/api/run_backend.py::main()`
    → `uvicorn`
    → `multimodal_rag.api.main:app`

### React market-trend request

React form submission
    ↓
`apps/frontend/src/App.jsx::submitQuestion()`
    ↓
`apps/frontend/src/api.js::analyzeMarketIntelligences()`
    ↓
`POST /agents/market-intelligence`
    ↓
`services/api/src/multimodal_rag/api/router.py::analyze_market_intelligences()`
    ↓
`packages/agents/src/multimodal_rag/agents/market_intelligence.py::MarketIntelligenceAgent.run()`

### React document ingestion

React ETL workspace
    â†“
`apps/frontend/src/App.jsx::ETLWorkspace::runPipeline()`
    â†“
`apps/frontend/src/api.js::uploadDocument()`
    â†“
`services/api/src/multimodal_rag/api/router.py::ingest_pdf()`
    â†“
`services/api/src/multimodal_rag/api/ingestion.py::IngestionJobManager._run()`
    |
`packages/security/src/multimodal_rag/security/clamav.py::ClamAVScanner.scan()`
    â†“
`ingest_document()` / `ingest_media()` -> `embed_document()` -> `build_index_from_output_dir()`
    â†“
`apps/frontend/src/api.js::getIngestionStatus()` polling

### Backend process

`services/api/run_backend.py::main()`
    ↓
`uvicorn` loads `multimodal_rag.api.main:app`
    ↓
`services/api/src/multimodal_rag/api/main.py::create_app()`
    ↓
FastAPI includes the authentication and application routers

### Maintained CLI entry points

- `packages/rag-core/src/multimodal_rag/cli/ingest.py::main()` — document ingestion and artifacts
- `packages/rag-core/src/multimodal_rag/cli/build_index.py::main()` — embedding/index construction
- `packages/rag-core/src/multimodal_rag/cli/ask.py::main()` — command-line RAG question answering
- `packages/rag-core/src/multimodal_rag/evaluation/cmo_metrics.py::main()` — ground-truth RAG evaluation and Markdown report

## Main Runtime Flows

### Market Intelligence Agent

1. `App.jsx::submitQuestion()` parses optional JSON context and calls `analyzeMarketIntelligences()`.
2. `api.js::analyzeMarketIntelligences()` sends the objective, scope fields, user/project scope, top-k, and bearer token to `/agents/market-intelligence`.
3. `api/router.py::analyze_market_intelligences()` validates the `AgentRequest`, creates a trace ID, invokes the request-scoped `MarketIntelligenceAgent`, and maps structured errors to HTTP status codes.
4. `MarketIntelligenceAgent.run()` resolves missing scope fields to cross-industry/global/last-30-days defaults, builds a market-trend retrieval query, and calls `InProcessRAGClient.retrieve()`.
5. `InProcessRAGClient.retrieve()` calls `RAGService.retrieve()` and converts each `ChunkResponse` into `AgentEvidence`, preserving chunk identifiers, pages, scores, excerpts, metadata, and nullable date/URL provenance fields.
6. `RAGService.retrieve()` resolves the user/project storage scope, loads the persistent Chroma collection and chunk metadata, calls the active retriever, and creates API chunk responses.
7. `rag/retrieval/retriever_2.py::retrieve()` embeds the query or executes its configured retrieval path, ranks chunks, applies score filtering, and returns `RetrievedChunk` values.
8. `MarketIntelligenceAgent.run()` classifies each dated evidence item as recent/not-recent only when its publication date falls in the resolved window; undated or unparsable dates remain unknown.
9. `MarketIntelligenceAgent._analyze()` supplies evidence, provenance, and resolved scope to the configured Gemini generation seam. The structured output contains atomic signals and grouped trend candidates.
10. `MarketIntelligenceAgent._build_findings()` joins model signal IDs to retrieved evidence and keeps only groups supported by at least two distinct documents.
11. `MarketIntelligenceAgent._response()` returns the stable `AgentResponse` envelope to FastAPI; the React `MarketIntelligenceMessage()` renders findings, evidence, provenance, scope, and limitations.

### Normal RAG answer

`App.jsx::submitQuestion()`
    ↓
`api.js::askQuestion()`
    ↓
`POST /answer`
    ↓
`api/router.py::answer_question()`
    ↓
`api/conversation_reuse.py::eligible_exchanges()`/`exact_repeat()` check the last 10 assistant turns in this chat (RAG turns only - agent-mode turns are filtered out) for a normalized-text match; a hit replays the stored `AnswerResponse` verbatim (new `trace_id`, `reused_from_message_id` set, `rag_trace.reused = true`) with no retrieval or generation call
    ↓
when there is no exact match but eligible history exists, `ConversationReuseClassifier.classify()` (one LLM call) labels the question `repeat`/`refinement`/`follow_up`/`new`; `refinement`/`follow_up` produce a narrowed `retrieval_question` and a `conversation_history` built from the matched prior turn(s)
    ↓
`RAGService.answer(question, ..., conversation_history=None, retrieval_question=None)`
    ↓
`run_rag_trace(retrieval_question or question, ..., prompt_question=question, conversation_history=...)` retrieves on the (possibly narrowed) `retrieval_question` but builds the prompt and records the trace against the user's actual `question`
    ↓
executes active retrieval, prompt building (folding `conversation_history` into `FOLLOW_UP_INSTRUCTIONS` when present), answer generation, and citation resolution
    ↓
answer, chunks, sources, trace ID, and query-time RAG trace

### Persistent account and conversation flow

1. `apps/frontend/src/App.jsx::submitLogin()` calls `api.js::signup()` or
   `api.js::login()`. `router.py::signup()` creates the user with
   `accounts.py::PostgresUserStore.create_user()`, while `login()` verifies the
   stored scrypt password hash through `PostgresUserStore.authenticate()`.
2. `PostgresUserStore.create_session()` returns a random bearer token and
   persists only its SHA-256 hash. `router.py::require_api_token()` resolves
   that token to `request.state.authenticated_user_id` for protected routes.
3. `router.py::_authenticated_user_id()` rejects any request whose supplied
   scope does not match the signed-in user. This check runs before ingestion,
   retrieval, RAG answers, and Market Intelligence execution.
4. `App.jsx::submitQuestion()` assigns a `chat_id`; `answer_question()` or
   `analyze_market_intelligence()` records the user payload, executes the
   existing answer/agent flow, then records the structured assistant payload
   in `user_chat_messages`.
5. After sign-in and each answer, `App.jsx::loadPastChats()` calls
   `GET /chats`. Selecting a conversation calls `GET /chats/{chat_id}`; both
   queries include the authenticated user ID in the PostgreSQL lookup before
   the React message renderer restores the conversation.
6. `services/api/run_backend.py::main()` starts directly when
   `RAG_DATABASE_URL` (or the memory-URL fallback) is present. Without it, it
   retains the legacy prompt for a single configured bearer token.

### Ground-truth RAG evaluation

1. `packages/rag-core/src/multimodal_rag/evaluation/cmo_metrics.py::main()` resolves the repository root from the script location, then loads the canonical JSON question set and report path.
2. `cmo_metrics.py::run()` reads completed rows from `cmo_metrics.py::_load_completed_rows()` and skips only rows marked `completed`.
3. For each remaining question, `run()` calls `runner.py::load_ground_truth()` and `runner.py::ask_rag_timed()`.
4. `cmo_metrics.py::retriever_accuracy()` computes question-level Hit@5, first relevant rank, and MRR for the hybrid results; `cmo_metrics.py::chunk_accuracy()` computes exact expected-chunk precision, recall, and F1.
5. If `ask_rag_timed()` raises a quota/rate-limit error, `cmo_metrics.py::_wait_for_api_key_change()` pauses for a `.env` key change, reloads the key, resets the embedding client, and retries the same question.
6. `cmo_metrics.py::judge_with_retry()` optionally scores faithfulness, answer relevancy, and answer correctness using the configured Gemini judge.
7. `cmo_metrics.py::_write_report()` writes the summary and per-question metrics to `evaluation/evaluation_result.md` after every question. Failed non-quota rows are marked `failed` and retried on a later run; no JSON, CSV, or result-directory artifacts are generated.

### RAG Trace workspace

1. `apps/frontend/src/App.jsx::submitQuestion()` calls `apps/frontend/src/api.js::askQuestion()` for a normal RAG question.
2. `services/api/src/multimodal_rag/api/router.py::answer_question()` calls `services/api/src/multimodal_rag/api/service.py::RAGService.answer()`.
3. `RAGService.answer()` calls `packages/rag-core/src/multimodal_rag/rag/trace.py::run_rag_trace()`, which executes retrieval, prompt construction, generation, and citation resolution once.
4. `RAGService._trace_payload()` serializes the same execution trace into the additive `AnswerResponse.rag_trace` field.
5. `App.jsx::submitQuestion()` retains `rag_trace` on the assistant message. `App.jsx::RAGTraceWorkspace()` renders the selected response's metrics, ranked chunks, scores, citations, and diagnostics without making another RAG request.

### Simplified research composer and company-focused Market Intelligence

1. `apps/frontend/src/App.jsx::App()` presents Ingest documents, Ask the intelligence base, and RAG Trace as the workspace navigation, and keeps the authenticated user identifier internal to the UI.
2. `App.jsx::submitQuestion()` submits normal RAG questions through `api.js::askQuestion()` with a fixed `top_k=5`; no project ID, user-scope, or evidence-depth control is exposed.
3. In Market Intelligence mode, `App.jsx` additionally collects optional `company_name` and `company_url`, then `api.js::analyzeMarketIntelligence()` sends them with the same fixed `top_k=5` to `router.py::analyze_market_intelligence()`.
4. `agents/models.py::AgentRequest` normalizes the optional company strings. `market_intelligence.py::MarketIntelligenceAgent.run()` adds a bounded company-focused query when either value is present, alongside plan-generated web queries and the RAG query built by `_build_rag_query()`.
5. The existing dual-source agent deduplicates the retrieved evidence, validates grounded findings, and `App.jsx::MarketIntelligenceMessage()` renders the response.

### Hierarchical semantic ingestion chunking

1. `packages/ingestion/src/multimodal_rag/ingestion/pipeline/orchestrator.py::ingest_document()` passes ordered validated layout regions to `chunker.py::chunk_document()`.
2. `chunk_document()` preserves heading/page boundaries and delegates narrative buffers to `_flush_paragraph_buffer()`; tables and figures follow their existing structural chunk paths.
3. `_flush_paragraph_buffer()` forms one parent section, calls `_semantic_children()` to pack complete paragraphs/sentences into overlap-aware child chunks, and writes a shared `parent_section_id` with `child_index` and `child_count` into each child metadata record.
4. `ingestion/output/writer.py::write_document_output()` persists the child chunks to `chunks.json`; `rag/embedding/embedder.py::embed_chunks()` embeds each child for the ChromaDB collection.

### Resumable embedding and index construction

1. `packages/rag-core/src/multimodal_rag/cli/build_index.py::main()` scans each ingestion document that has `chunks.json` but no final `embeddings.npy`.
2. `rag/embedding/embedder.py::embed_document()` loads its chunks and calls `embed_chunks()` with the document directory as a checkpoint location.
3. `embed_chunks()` filters unembeddable chunks, validates any `.embedding_progress.npy` / `.embedding_progress.json` checkpoint against the model and ordered chunk IDs, then resumes from the stored vector count.
4. `_embed_texts()` sends complete batches to Gemini. It checkpoints each successful batch, pauses before exceeding the configured input budget, and retries a 429 batch after Gemini's requested delay.
5. `write_embeddings()` persists final `embeddings.npy` and `embeddings_metadata.json`, then removes the temporary checkpoint. `chroma_index.py::build_index_from_output_dir()` reads only complete final embeddings, and `save_index()` persists them to the scoped ChromaDB collection.

### Multi-level retrieval context

1. `chunker.py::_flush_paragraph_buffer()` writes a `chunk_level="parent"` section record and sentence-safe `chunk_level="child"` records linked to it by `parent_chunk_id`.
2. `embedder.py::embed_chunks()` records the parent as intentionally skipped, resolves each embedded child's `parent_chunk_text` from the same in-memory chunk-record list, and carries the child's full `ChunkMetadata` dict alongside it on `EmbeddedChunk`.
3. `write_embeddings()` persists `parent_chunk_text`/`metadata` into `embeddings_metadata.json`; `chroma_index.py::build_index_from_output_dir()` reads them onto `IndexedChunkRef`, and `save_index()` stores them as `parent_chunk_text`/`metadata_json` fields directly on each child's Chroma record.
4. `retriever_2.py::retrieve()` ranks children from ChromaDB/BM25 as usual and carries `metadata`/`parent_chunk_text` from the matched `IndexedChunkRef` onto each returned `RetrievedChunk`.
5. `trace.py::expand_parent_context()` reads `chunk.parent_chunk_text` directly off the retrieved chunk (no disk lookup) and swaps it in as the chunk body, retaining the child ID for citations.
6. `trace.py::run_rag_trace()` builds its debug `metadata_by_id` map from each retrieved chunk's own `.metadata` (also sourced from Chroma) when no override is supplied. `api/service.py::RAGService.retrieve()` and `.answer()` apply the same Chroma-only path — neither reads `chunks.json` at query time. The legacy disk-scanning `trace.py::load_chunk_metadata()` function remains only as an explicit override seam used by `evaluation/question_runner.py`'s developer CLI trace, not by the default path; the now-uncalled `load_chunk_texts()` was removed.

### Tenant storage root relocated outside runtime-data

1. `paths.py::USER_DATA_ROOT_DEFAULT` resolves to `<project root>/user-data`, a new top-level directory that is not part of `runtime-data`.
2. `api/config.py::APISettings.from_environment()` defaults `user_data_root` to `USER_DATA_ROOT_DEFAULT` when `RAG_USER_DATA_ROOT` is unset; `CorpusScope.root`/`.ingestion_artifacts_dir`/`.index_dir` are unchanged and simply resolve under the new root.
3. `runtime-data/` remains exclusively the CLI/evaluation global corpus (`cli/ingest.py`, `cli/build_index.py`, `cli/ask.py`, `evaluation/*`) and application logs; it is no longer used by any per-user/API-scoped path (frontend uploads, `/retrieve`, `/answer`, agents).
4. Existing content under the old `runtime-data/users/` location is not migrated automatically; scoped documents must be re-ingested under the new root, which also picks up the Chroma-native metadata described above.

### Exact duplicate child suppression

1. `orchestrator.py::ingest_document()` creates multi-level chunks.
2. `output/deduplicator.py::deduplicate_chunks()` normalizes and hashes each non-parent chunk, bootstraps `chunk_registry.json` from active artifacts when needed, and retains only canonical children.
3. `deduplication.json` records reused canonical chunks for the new document; the indexer embeds only the retained chunks.

### React ETL workspace

1. The authenticated `apps/frontend/src/App.jsx::App()` renders the `Ingest documents` navigation item and selects `ETLWorkspace()` when `workspaceView === "etl"`.
2. `ETLWorkspace()` recognizes the explicit PDF/MP4/MP3/DOC/DOCX/PPT/PPTX allowlist from the dropzone or file picker. All listed formats are active and are checked for emptiness and the 50 MB client-side size limit before being stored in component state.
3. `runPipeline()` starts the authenticated job, polls its status, and maps the server's Upload, Extraction, Chunking, Embedding, Store document, and Build index stages to the progress bar/status banner.
4. After the server job completes, the component records document name, size, timestamp, chunk count, embedding count, and status in `localStorage` under `cmo-etl-documents`.
5. The recent-artifacts list renders the browser-local records. Production extraction, chunking, embedding, and ChromaDB storage continue through `cli/ingest.py::main()` and `cli/build_index.py::main()`.

### Authenticated API document ingestion

1. `apps/frontend/src/App.jsx::ETLWorkspace::runPipeline()` submits the selected PDF, MP3, MP4, DOC, DOCX, PPT, or PPTX, user ID, and optional project ID through `apps/frontend/src/api.js::uploadDocument()` as multipart form data with the bearer token.
2. `services/api/src/multimodal_rag/api/router.py::ingest_pdf()` applies `packages/ingestion/src/multimodal_rag/ingestion/formats.py`'s PDF/MP4/MP3/DOC/DOCX/PPT/PPTX allowlist, matching media/Office MIME types and limiting all uploads to 50 MB. PDFs additionally receive signature/readability/page-count checks and a normalized-text duplicate check. A file SHA-256 match returns `409` and deletes the temporary upload.
3. `services/api/src/multimodal_rag/api/ingestion.py::IngestionJobManager.submit()` repeats the file-registry check and reserves in-flight hashes under its job lock, preventing two identical uploads from entering the worker concurrently.
4. Before parsing or provider submission, `_run()` reports `security_scan` and calls `security/clamav.py::ClamAVScanner.scan()` when `RAG_CLAMAV_ENABLED=true`. A clean verdict proceeds; a malware verdict moves the upload to `<scope root>/quarantine/<job id><extension>` and fails the job. Scanner failures fail closed unless `RAG_CLAMAV_FAIL_CLOSED=false` is explicitly configured.
5. After the scan, `_run()` calls the PDF `cli/ingest.py::load_config()` / `ingestion/pipeline/orchestrator.py::ingest_document()` path, `ingestion/media/deepgram.py::ingest_media()` for MP3/MP4, `ingestion/word/extractor.py::ingest_word()` for DOC/DOCX, or `ingestion/presentation/extractor.py::ingest_presentation()` for PPT/PPTX. PPTX is read with `python-pptx`; PPT is converted in a temporary directory through LibreOffice headless mode before the same parser reads it. Each adapter writes standard `chunks.json` records.
6. The job calls `IngestionJobManager._embed_pending_documents()`, which embeds every scoped document with chunks but without a complete non-empty embedding artifact, attempting the new upload first. Exact duplicate uploads may produce zero new chunks while their canonical source document is backfilled.
7. The job calls `rag/indexing/chroma_index.py::build_index_from_output_dir()` and `save_index()` only after complete non-empty vectors are available. Empty/malformed embedding arrays are skipped, and `api/service.py::RAGService._load_scope()` rejects an empty collection.
8. `api/service.py::clear_scoped_index_cache()` invalidates cached Chroma handles after a successful rebuild. The manager reports `upload`, `security_scan`, `extract`, `chunk`, `embed`, `store`, `index`, `complete`, or `error` stages, and `ETLWorkspace::runPipeline()` polls them until completion/failure.

## Module Responsibilities

- `agents/models.py` defines request, evidence, finding, and response contracts.
- `agents/market_intelligence.py` performs signal extraction, trend validation, provenance attachment, and response construction.
- `agents/market_strategy.py` assesses readiness and produces evidence-linked strategy only from a supplied Market Intelligence response and stored context.
- `agents/rag_client.py` adapts the API RAG service to the agent evidence contract.
- `api/router.py` exposes authenticated FastAPI routes and translates agent failures to HTTP responses.
- `api/accounts.py` stores authenticated chats and pending user-scoped strategy continuations, and reads back the last N (question, assistant-payload) pairs of a chat for reuse detection.
- `api/conversation_reuse.py` filters stored chat messages into safe reuse candidates, detects exact repeats via normalized-text hashing, formats prior exchanges for prompts, and classifies RAG follow-ups/refinements for `/answer` (the two agents fold the same classification into their own existing LLM calls instead).
- `api/ingestion.py` serializes scoped upload jobs and reports ingestion/embedding/index stages.
- `security/clamav.py` streams enabled raw-file scans to the local ClamAV daemon and reports clean, threat, or scanner-failure verdicts.
- `ingestion/media/deepgram.py` submits scanned MP3/MP4 bytes to Deepgram and writes timestamped transcript chunks compatible with the existing embedding artifact contract.
- `ingestion/word/extractor.py` reads DOCX paragraphs/table rows and converts legacy DOC files through LibreOffice before writing embedding-compatible chunks.
- `ingestion/presentation/extractor.py` reads PPTX slide text, tables, and notes and converts legacy PPT files through LibreOffice before writing slide-aware chunks.
- `api/service.py` resolves scoped indexes, loads chunk metadata, and invokes retrieval.
- `rag/retrieval/retriever_2.py` performs the active dense/lexical/hybrid retrieval behavior configured by the repository.
- `apps/frontend/src/api.js` sends API requests and parses responses.
- `apps/frontend/src/App.jsx` submits RAG, Market Intelligence, and Web Search questions and renders their messages.
- `web_search/client.py` exposes simple provider-neutral web search contracts.
- `web_search/research.py` builds bounded multi-company research prompts.
- `web_search/providers/tavily.py` calls Tavily Search and Research APIs and normalizes reports/sources.
- `ingestion/processing/chunker.py` converts validated layout regions into sentence-safe hierarchical semantic children with provenance metadata.

## Active Modification Path

**Task:** Replace the Source Guard URL-reputation check with URLhaus.

**Execution path affected:**

`apps/frontend/src/App.jsx::submitQuestion()`
    → `/answer` or `/agents/market-intelligence`
    → `api/router.py::answer_question()` or `analyze_market_intelligence()`
    → `MemoryService.process_message(user_id, message)`
    → `MemoryExtractor.extract()` produces structured candidates only
    → `MemoryService` validates, deduplicates, inserts, or updates candidates
    → `PostgresMemoryRepository` stores user-scoped profile and memory rows
    → the existing RAG or Market Intelligence route continues unchanged

**Files/functions being modified:**
- `packages/web-search/src/multimodal_rag/web_search/source_guard.py::SourceGuardService`
- `packages/agents/src/multimodal_rag/agents/market_intelligence.py::MarketIntelligenceAgent.run`
- `services/api/src/multimodal_rag/api/main.py::create_app`
- `services/api/src/multimodal_rag/api/config.py::APISettings.from_environment`
- `tests/test_source_guard.py`
- `tests/test_market_intelligence_agent.py`

**Reason this part of the flow is changing:**
External search results must be validated before their URLs or content enter
Market Intelligence. The guard blocks malformed URLs, threat matches, invalid
security responses, missing credentials, and provider failures.

**Current URL-reputation path:**
`main.py::create_app()` → `SourceGuardService` → `MarketIntelligenceAgent.run()` → `WebSearchClient.search()` → `SourceGuardService.validate_url()` → URLhaus → `SourceGuardService.validate_content()` → Check Point AI Guardrails → approved `AgentEvidence` only.

**Final implementation:**
`create_app()` injects an enabled-by-default `SourceGuardService` configured
from environment variables. Its URLhaus URL check runs before the AI
Guardrails content check; only results that pass both become web evidence.

## Flow Change History

- DEC-2026-09-02-01 adds conversation-aware reuse to `/answer`, Market Intelligence, and Market Strategy: an exact-repeat question (SHA-256 normalized-text hash) replays the stored response verbatim with no retrieval/search/generation; a near-repeat researches only the delta while a follow-up finally activates the previously-dormant `conversation_history`/`FOLLOW_UP_INSTRUCTIONS` plumbing in `run_rag_trace()`/`build_prompt()`. Reuse detection is skipped while a Market Strategy clarification is pending.
- DEC-2026-08-30-12 scopes a consistent readable text scale and high-contrast near-black list content to Market Strategy responses without changing other agent views or compact metadata.
- DEC-2026-08-30-11 normalizes optional strategy findings independently so malformed strings are omitted without weakening provenance or failing valid output.
- DEC-2026-08-30-10 adds a compact, directly copied Market Intelligence snapshot to every Market Strategy response.
- DEC-2026-08-30-09 allows Strategy to proceed from approved Market Intelligence sources when normalized intelligence categories are empty, while retaining the no-source block and evidence-ID validation.
- DEC-2026-08-30-08 makes the API the intelligence-first strategy coordinator, removes research ownership from Market Strategy, and persists clarification continuations without repeated research.

- DEC-2026-08-26-02: dense indexing and search changed from FAISS files to
  persistent ChromaDB collections; BM25/RRF remains downstream.
- DEC-2026-08-27-01: PDF extraction reports per-region progress through API
  status, terminal logs, and the frontend ETL workspace.
- DEC-2026-08-28-01: the primary research workspace uses fixed top-five
  retrieval and optional company-focused Market Intelligence searches.
- DEC-2026-08-28-02: populated conversations share a tinted liquid-glass
  presentation layer regardless of answer mode.
- DEC-2026-08-28-03: populated conversation copy uses a restrained larger
  type scale, and source-card metadata truncates inside its card.
- DEC-2026-08-28-04: the ingestion workspace uses light tinted glass surfaces
  with dark text while retaining green operational state cues.
- DEC-2026-08-28-05: Market Intelligence generation now requests detailed,
  evidence-dependent briefs while retaining validated structured findings.
- DEC-2026-08-28-06: authenticated user messages pass through a service-owned,
  user-scoped PostgreSQL memory boundary when memory persistence is configured.

**Task:** Replace frontend starter questions with three highest-scoring
questions from the RAG evaluation report.

**Execution path affected:**

`apps/frontend/src/App.jsx::App()`
    → `suggestions` constant
    → suggestion button rendering
    → `setQuestion(suggestion)` on click
    → `submitQuestion()` when the user sends the question
    → `apps/frontend/src/api.js::askQuestion()` or the selected request mode

The selected questions are static UI prompts. They are not automatically
submitted and do not change API, retrieval, generation, or evaluation logic.
Their source is `evaluation/evaluation_result.md`, where questions 1, 2, and
4 are tied for the strongest reported quality and rank results.

**Files/functions being modified:**
- `apps/frontend/src/App.jsx::suggestions`
- `docs/decisions.md`
- `docs/flow.md`

**Reason this part of the flow is changing:**
The starter prompts should showcase verified, high-performing questions from
the current CMO evaluation corpus instead of generic placeholders.

Historical evaluator path:

`packages/rag-core/src/multimodal_rag/evaluation/cmo_metrics.py::main()`
    → `cmo_metrics.py::run()`
    → `runner.py::load_ground_truth()`
    → `runner.py::ask_rag_timed()`
    → `cmo_metrics.py::retrieval_scores()` / `judge_with_retry()`
    → `cmo_metrics.py::_write_report()`
    → `evaluation/evaluation_result.md`

Prior market-trend and web-research paths remain documented in the main
runtime-flow sections and flow history.

`App.jsx::submitQuestion()`
→ `api.js::analyzeMarketIntelligences()`
→ `api/router.py::analyze_market_intelligences()`
→ `MarketIntelligenceAgent.run()`
→ `MarketIntelligenceAgent._build_query()`
→ `InProcessRAGClient.retrieve()`
→ `RAGService.retrieve()`
→ `MarketIntelligenceAgent._analyze()`
→ `MarketIntelligenceAgent._build_findings()`
→ `MarketIntelligenceMessage()`

**Files/functions being modified:**
- `packages/web-search/src/multimodal_rag/web_search/client.py::WebSearchClient.search`
- `packages/web-search/src/multimodal_rag/web_search/providers/tavily.py::TavilySearchProvider.search`
- `packages/web-search/src/multimodal_rag/web_search/research.py::CompanyResearchClient.research`
- `packages/web-search/src/multimodal_rag/web_search/providers/tavily.py::TavilySearchProvider.research`
- `services/api/src/multimodal_rag/api/router.py::research_companies`
- `apps/frontend/src/App.jsx::submitQuestion`
- `apps/frontend/src/App.jsx::MarketIntelligenceMessage`
- `apps/frontend/src/api.js::analyzeMarketIntelligence`
- `packages/web-search/src/multimodal_rag/web_search/models.py::SearchResult`
- `tests/test_web_search_client.py`
- `tests/test_company_research.py`
- `docs/decisions.md`
- `docs/flow.md`

**Previously modified ingestion path:**
- `packages/ingestion/src/multimodal_rag/ingestion/processing/chunker.py::_flush_paragraph_buffer`
- `packages/rag-core/src/multimodal_rag/rag/embedding/embedder.py::embed_chunks`
- `packages/rag-core/src/multimodal_rag/rag/trace.py::run_rag_trace`
- `services/api/src/multimodal_rag/api/service.py::RAGService.retrieve`
- `tests/test_semantic_chunking.py`
- `tests/test_rag_trace.py`

**Reason this part of the flow is changing:**
The frontend needs a user-facing web research path for one or more companies. The new route uses Tavily's asynchronous research task and returns a concise cited report without changing the existing document-only agent execution path.

**Historical ingestion rationale:**
The vector index needs small, precise child chunks, while answer generation needs the complete parent section that gives each child its meaning.

## Flow Change History

- DEC-2026-08-13-01 and DEC-2026-08-13-02 define the current Market Intelligence Agent modification.
- DEC-2026-08-19-03 adds the query-time RAG Trace response and React workspace.
- DEC-2026-08-19-04 replaces narrative character splitting with hierarchical semantic child chunks.
- DEC-2026-08-19-05 adds rate-limit pacing and per-document embedding checkpoints.
- DEC-2026-08-19-06 adds stored parent context with child-only retrieval and query-time parent expansion.
- DEC-2026-08-19-07 adds canonical SHA-256 child-chunk deduplication.
- DEC-2026-08-20-01 adds the client-side React ETL workspace and documents its boundary from production CLI ingestion.
- DEC-2026-08-20-02 connects the ETL workspace to serialized scoped API ingestion and status polling.
- DEC-2026-08-20-03 backfills pending scoped embeddings and prevents empty FAISS indexes after duplicate uploads.
- DEC-2026-08-20-04 adds empty-upload rejection and 50-page PDF limits while documenting the current PDF-only/video boundary.
- DEC-2026-08-20-05 adds the shared PDF/MP4/MP3/DOC/DOCX allowlist and separates planned formats from the currently active PDF worker.
- DEC-2026-08-20-06 adds file-level SHA-256 preflight and in-flight duplicate rejection before extraction.
- DEC-2026-08-20-07 fixes the post-completion log reference so successful ingestion jobs remain completed.
- DEC-2026-08-20-08 adds normalized extracted-text fingerprints and preflight rejection for text-identical PDF re-exports.
- DEC-2026-08-24-01 adds optional fail-closed ClamAV scanning before API upload extraction.
- DEC-2026-08-24-02 activates MP3/MP4 ingestion and Deepgram timestamped transcription after the security scan.
- DEC-2026-08-24-03 activates DOC/DOCX ingestion through `python-docx` and LibreOffice conversion after the security scan.
- DEC-2026-08-24-04 activates PPT/PPTX ingestion through `python-pptx` and LibreOffice conversion after the security scan.
- DEC-2026-08-24-05 consolidates ground-truth RAG evaluation output into one Markdown report.
- DEC-2026-08-24-06 separates hybrid retriever accuracy from exact chunk accuracy in that report.
- DEC-2026-08-24-07 makes direct evaluator execution independent of the current working directory.
- DEC-2026-08-25-01 adds the standalone provider-neutral Tavily web-search path; no current agent or API route consumes it.
- DEC-2026-08-25-02 adds the authenticated multi-company Tavily Research route and frontend Web Search mode.
- DEC-2026-08-26-04 replaces generic frontend starter questions with three tied highest-scoring evaluation questions.
- DEC-2026-08-30-02 replaces the Web Risk URL check with URLhaus while retaining fail-closed Check Point AI Guardrails filtering.
- DEC-2026-08-28-07 adds PostgreSQL user accounts, user-bound sessions, and
  persisted user-scoped conversation history.
- DEC-2026-08-31-01 stores parent-chunk text and full chunk metadata directly
  on each child's Chroma record so retrieval never reads `chunks.json`, and
  relocates the per-tenant ingestion/index storage root from
  `runtime-data/users/` to a new top-level `user-data/` directory.

## Canonical component paths after platform promotion

The namespace package remains `multimodal_rag`, but its physical source roots
are now component-specific:

- API: `services/api/src/multimodal_rag/api/`
- Agents: `packages/agents/src/multimodal_rag/agents/`
- Web search: `packages/web-search/src/multimodal_rag/web_search/`
- RAG core, CLI, and evaluation: `packages/rag-core/src/multimodal_rag/`
- Ingestion: `packages/ingestion/src/multimodal_rag/ingestion/`
- Security: `packages/security/src/multimodal_rag/security/`
The root `run_backend.py` file is a compatibility launcher; execution
delegates to the canonical API service location above.

## Backend launcher compatibility path

When `run_backend.py` is invoked from the platform root, it delegates to
`services/api/run_backend.py::main()`. After collecting the bearer token,
`main()` calls `_ensure_source_roots()`, which places the component `src`
directories on `sys.path` for interpreters that do not have an editable install.
Uvicorn then resolves the unchanged target `multimodal_rag.api.main:app`,
which imports the API factory and its in-process agents, web-search, and RAG
components from their canonical roots.

## Active Modification Path

**Task:**
Show real Presenton task stages and a wide local preview for generated decks.

**Execution path affected:**

`MeetingPreparationMessage` -> `openPresentationConfig()`
-> `api.js::listPresentationTemplates()`
-> `router.py::list_presentation_templates()`
-> `PresentationGenerationService.list_templates()`
-> `PresentonClient.list_templates()`
-> `PresentonClient.start_async()` creates the provider task
-> `PresentonClient.task_status()` supplies the actual task state
-> `PresentationGenerationService.download_task()` returns the validated PPTX
-> `PresentationGenerationService.preview_task()` exports a validated PDF
-> `App.jsx` switches the modal to a wide preview layout and opens the local PDF at page-width zoom in a 16:9 viewer

**Files/functions being modified:**
- services/api/src/multimodal_rag/api/presentations.py::PresentonClient.start_async()
- services/api/src/multimodal_rag/api/presentations.py::PresentationGenerationService.preview_task()
- services/api/src/multimodal_rag/api/router.py::generate_presentation()
- apps/frontend/src/App.jsx::handleGeneratePresentation()
- apps/frontend/src/App.jsx::openPresentationPreview()
- apps/frontend/src/App.jsx::presentation preview modal rendering
- apps/frontend/src/styles.css::.presentation-preview-frame
- tests/test_presentations.py::PresentationAPITest
- docs/decisions.md
- docs/flow.md

**Reason this part of the flow is changing:**
The local preview was constrained by the standard narrow configuration modal.
The preview uses an expanded, horizontal frontend-only viewer once its PDF URL
is available; no API or generation behavior changes.

## Flow Change History

- DEC-2026-09-10-06: Added optional LangSmith request and nested execution tracing.
- DEC-2026-09-15-01: Added chat-scoped deterministic Presenton PPT generation.
- DEC-2026-09-15-02: Normalized current and legacy Presenton template response shapes.
- DEC-2026-09-15-03: Normalized Presenton artifact URL origin ports before download.
- DEC-2026-09-15-04: Preserved trusted presentation Markdown and surfaced bounded provider diagnostics.
- DEC-2026-09-15-05: Canonicalized Presenton artifact paths to the configured provider origin.
- DEC-2026-09-15-06: Preserved verified Presenton Cloud artifact URLs while retaining external-host rejection.
- DEC-2026-09-15-07: Added signed public-HTTPS artifact support without external credential forwarding.
- DEC-2026-09-15-08: Added async task polling, template cards, and local PDF preview.
- DEC-2026-09-15-09: Expanded the local PDF preview into a horizontal frontend-only viewer.
- DEC-2026-09-15-10: Requested page-width zoom for the embedded local PDF viewer.
