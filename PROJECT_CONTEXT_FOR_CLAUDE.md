# CMO Intelligence Platform — Complete Project Context

> **Purpose of this document:** This is a single, self-contained handoff document written so that a fresh Claude session (with no prior knowledge of this repository) can understand the product, the architecture, everything that has been implemented so far, how it works, and what remains open. It consolidates and reconciles the repo's internal docs (`README.md`, `docs/PROJECT_STATUS.md`, `docs/ARCHITECTURE_REFERENCE.md`, `docs/flow.md`, `docs/decisions.md`, `AGENTS.md`) as of **2026-08-31**. Where those documents disagree (see the retrieval note below), this document states the verified-current behavior.
>
> This is a **context/reference document**, not a substitute for reading the actual source before making changes to it.

---

## 1. What this project is

**CMO Intelligence Platform** (package name `multimodal_rag`) is a full-stack, multi-tenant AI platform that turns a Chief Marketing Officer's private document corpus (PDFs, PPT/PPTX, DOC/DOCX, MP3/MP4) into:

1. **Grounded question answering** over that corpus (RAG).
2. **Market Intelligence** — evidence-backed trend/competitor/opportunity/risk analysis synthesized from both the private document corpus *and* live web search.
3. **Market Strategy** — evidence-linked strategic recommendations built on top of Market Intelligence output and the user's stored business context/memory.
4. **Company web research** — ad-hoc multi-company research reports via Tavily.

It started life as a general-purpose "Multimodal RAG for PDF Intelligence" system and has been progressively promoted into a CMO-focused, multi-agent, multi-tenant SaaS-shaped platform with accounts, persistent chat history, and a security-hardened ingestion/web-evidence boundary.

It is a **traceable** system: every retrieval and generation call preserves chunk-level provenance, scores, latency, and token telemetry so answers can be audited, not just trusted.

### Core engineering philosophy (from `AGENTS.md`, applies to any future change)
- Inspect the real execution path in code before changing it — don't guess.
- Every code change must be reflected in `docs/decisions.md` (why) and `docs/flow.md` (how the execution flow moved).
- Runtime artifacts (`runtime-data/`), credentials, and user source files (`Data/`) are never touched by source changes.
- The canonical Python environment is `.venv` (Python 3.11.9, project supports 3.10+).

---

## 2. Tech stack

| Layer | Technology | Role |
|---|---|---|
| Backend language | Python 3.10+ (verified on 3.11) | Core application |
| API framework | FastAPI + Uvicorn | HTTP service layer |
| PDF / layout | Docling + PyMuPDF | Structure-aware extraction, rendering |
| OCR | RapidOCR | Local OCR fallback |
| Vision | Gemini Vision (`gemini-3.1-flash-lite`) | Complex visual-region understanding, escalation-only |
| Chunking | Structure-aware aggregation + `RecursiveCharacterTextSplitter` + sentence-safe semantic packing | Context-preserving hierarchical chunks |
| Embeddings | `gemini-embedding-2` (hosted) | 768-dim normalized vectors by default (1536/3072 configurable) |
| Vector index | **ChromaDB** (persistent client, cosine distance) | Dense similarity retrieval — replaced FAISS in Aug 2026 |
| Sparse retrieval | Dependency-free BM25 (own implementation) | Lexical retrieval |
| Fusion | Reciprocal Rank Fusion (RRF, k=60) | Combines dense + sparse ranked lists |
| Optional reranking | `BAAI/bge-reranker-base` Cross-Encoder | Local, optional, off by default in production |
| Generation | `gemini-3.1-flash-lite` (Google GenAI SDK) | Grounded answer / agent generation |
| Agent orchestration | Custom Python agents (no LangChain/AutoGen) | Market Intelligence, Market Strategy |
| Web search | Tavily (search + async Research API) | External evidence for Market Intelligence & company research |
| Web-content security | VirusTotal v3 (URL reputation) + Check Point AI Guardrails (content/prompt-injection) | Fail-closed "Source Guard" for all external evidence |
| Upload security | ClamAV (`clamd`) | Malware scanning before any parsing |
| Media transcription | Deepgram (pre-recorded API) | MP3/MP4 → timestamped transcripts |
| Office docs | `python-docx`, `python-pptx`, LibreOffice headless (legacy `.doc`/`.ppt` conversion) | Word/PowerPoint ingestion |
| Persistence | PostgreSQL (`psycopg`) | Accounts, sessions, chats, user memory |
| Evaluation | RAGAS (5-metric), custom CMO metrics, retrieval benchmark | Answer & retrieval quality |
| Evaluator LLMs | Ollama (`qwen2.5:7b`, local, default) / Groq (`openai/gpt-oss-20b`, optional) | RAGAS judge models |
| Frontend | React 18 + Vite 6 | Browser client (only maintained frontend) |
| Testing | `pytest` / `unittest` | ~68+ tests, providers mocked |

---

## 3. High-level architecture

```mermaid
flowchart TD
    subgraph Client["apps/frontend (React + Vite)"]
        UI[Single-page app: chat, ingestion, trace, auth]
    end

    subgraph API["services/api (FastAPI)"]
        Router[router.py — HTTP routes]
        Service[service.py — RAGService]
        Ingest[ingestion.py — IngestionJobManager]
        Accounts[accounts.py — PostgresUserStore]
        Memory[MemoryService — durable user facts]
    end

    subgraph Core["packages/rag-core — RAG engine"]
        Embed[Embedding: Gemini Embedding 2]
        Index[Indexing: ChromaDB]
        Retrieve[Retrieval: Hybrid Dense+BM25+RRF+optional Cross-Encoder]
        Gen[Generation: Gemini 3.1 Flash Lite + prompt + citations]
        Trace[RAGTrace: scores, latency, tokens]
        Eval[Evaluation: RAGAS + CMO metrics + retrieval benchmark]
    end

    subgraph Ingestion["packages/ingestion"]
        Loaders[PDF/Word/PPT/Media loaders]
        Analysis[Layout + routing policy]
        Extract[Native / OCR / Vision extraction]
        Chunk[Structure-aware hierarchical chunker]
    end

    subgraph Agents["packages/agents"]
        MI[MarketIntelligenceAgent]
        MS[MarketStrategyAgent]
    end

    subgraph WebSearch["packages/web-search"]
        Tavily[Tavily search + research]
        Guard[SourceGuardService — VirusTotal + AI Guardrails]
    end

    subgraph Security["packages/security"]
        ClamAV[ClamAV malware scan]
    end

    UI -->|HTTP + bearer token| Router
    Router --> Service
    Router --> Ingest
    Router --> Accounts
    Router --> Memory
    Service --> Retrieve
    Service --> Gen
    Service --> Trace
    Ingest --> ClamAV
    Ingest --> Loaders --> Analysis --> Extract --> Chunk --> Embed --> Index
    Retrieve --> Index
    MI --> Service
    MI --> Tavily --> Guard
    MS --> MI
    MS --> Memory
    Eval --> Retrieve
    Eval --> Gen
```

### Physical source roots (all under one `multimodal_rag` namespace package)

| Component | Physical path |
|---|---|
| Frontend | `apps/frontend/` |
| API service | `services/api/src/multimodal_rag/api/` |
| Agents | `packages/agents/src/multimodal_rag/agents/` |
| Web search | `packages/web-search/src/multimodal_rag/web_search/` |
| RAG core, CLI, evaluation | `packages/rag-core/src/multimodal_rag/` |
| Ingestion | `packages/ingestion/src/multimodal_rag/ingestion/` |
| Security | `packages/security/src/multimodal_rag/security/` |
| Import-path compatibility shim | `src/multimodal_rag/__init__.py` (adds the above roots to `sys.path`) |
| Runtime data (generated, not source) | `runtime-data/` |
| User-provided source material | `Data/` |
| Tests | `tests/` |

This layout is the result of an explicit "platform promotion" refactor (see §7, DEC-2026-08-26-01): the app used to live nested under `multimodal-rag/`; it was promoted to the repo root and split into `apps/`, `services/`, `packages/` while preserving the `multimodal_rag.*` import contract via `pyproject.toml` package mappings.

---

## 4. The RAG pipeline in detail

### 4.1 Ingestion → chunking (packages/ingestion)

```mermaid
flowchart TD
    A[Raw file: PDF/DOCX/DOC/PPTX/PPT/MP3/MP4] --> B{File type}
    B -->|PDF| C[PyMuPDF load_pdf: page dims, raw text, font spans, image bounds]
    B -->|DOCX/DOC| D[python-docx direct / LibreOffice convert DOC to DOCX first]
    B -->|PPTX/PPT| E[python-pptx direct / LibreOffice convert PPT to PPTX first]
    B -->|MP3/MP4| F[Deepgram pre-recorded transcription, timestamped]

    C --> G[Page pre-analysis: scan candidacy, broken-font ratio, image ratio]
    G --> H[Docling layout segmentation: typed regions, tables, figures, reading order]
    H --> I[Layout analysis: density, fragmentation, infographic score]
    I --> J[Routing policy: use_native / use_ocr / use_gemini flags per page]
    J --> K{Region type}
    K -->|Text-like, trusted native| L[Clean + validate native text]
    K -->|Text-like, untrusted| M[RapidOCR render+extract]
    M -->|weak/empty & routed| N[Gemini Vision describe_diagram_image]
    K -->|Table| O[Docling grid / pdfplumber fallback / OCR / Vision escalation]
    K -->|Figure| P[Decorative-image dedup by avg-hash -> OCR -> Vision escalation]

    L --> Q[Structure-aware hierarchical chunker]
    N --> Q
    O --> Q
    P --> Q
    D --> Q
    E --> Q
    F --> Q

    Q --> R[Parent section chunks + sentence-safe child chunks, size 1000/overlap 150]
    R --> S[SHA-256 exact-duplicate suppression -> canonical children only]
    S --> T[chunks.json + validation_report.json + audit artifacts]
```

Key implementation facts:
- **Loader** (`ingestion/loaders/pdf_loader.py`): validates size (200MB max), page count (1–2000), encryption, corruption; a bad page becomes an empty `RawPage` with an error instead of failing the whole document.
- **Routing policy** (`ingestion/routing/routing_policy.py`): native text is trusted unless the page is a scan candidate or has a broken-font signal; OCR triggers when native is untrusted; Gemini Vision triggers only when a page's infographic score ≥ 0.6 (an explicit escalation policy, not "vision on every page").
- **Validation** (`ingestion/processing/validator.py`): every region gets a `ValidatedRegionResult` recording attempted methods, chosen method, status (`ok`/`low_confidence`/`failed`), and reason — nothing enters the vector corpus without going through this.
- **Chunking** (`ingestion/processing/chunker.py`): this is a two-level hierarchy —
  - **Parent chunks**: one per narrative section, full section text, `chunk_level="parent"`.
  - **Child chunks**: sentence-safe, size≈1000/overlap≈150, linked to their parent via `parent_chunk_id`, carrying `parent_section_id`, `child_index`, `child_count`.
  - Only **children are embedded**; at query time the child's matched parent text is fetched and given to the LLM as full context while the child's ID stays the citation anchor (`rag/trace.py::expand_parent_context()`). This balances retrieval precision (small chunks) against generation quality (full section context).
  - Tables/figures are isolated structural chunks; oversized tables split by row groups (15 rows/chunk, 2000 char cap).
  - Content never merges across page boundaries.
- **Deduplication** (`ingestion/output/deduplicator.py`): normalized-text SHA-256 hashing retains only the first canonical chunk per hash across the whole corpus, recorded in `chunk_registry.json`/`deduplication.json`. Re-uploading an identical document produces zero new chunks but reuses the canonical ones.
- **Outputs** per document: `raw/raw_text.txt`, `raw/pages.json`, `raw/tables.json`, `raw/metadata.json`, `chunks.json`, `metadata.json`, `validation_report.json`, `extracted_text_audit.md`, `human_readable_extraction.md`, `tables/<region_id>.json` — under `runtime-data/artifacts/ingestion/<document_id>/` for the CLI/global corpus, or `user-data/<user_id>/artifacts/ingestion/<document_id>/` for API/frontend-scoped uploads (see §6). These are ingestion-time audit output; as of DEC-2026-08-31-01, retrieval reads none of them back — everything retrieval needs (including parent-section context and full chunk metadata) is stored directly on the matching ChromaDB record instead.

**Known technical debt in ingestion:** `orchestrator.py` still has a hard-coded whole-page Gemini POC for PDF pages `{21, 28, 32, 34}` (`_PAGE_LEVEL_GEMINI_PAGES`), explicitly labeled TODO; should become a routing-derived signal. Debug `print()`s tied to those pages remain in validator/chunker.

### 4.2 Embedding & indexing (packages/rag-core)

- **Embedding** (`rag/embedding/embedder.py`): Gemini Embedding 2 (`gemini-embedding-2`), 768-dim by default (768/1536/3072 configurable), batch size 32, normalized output. One module-level client singleton per API key. Writes `embeddings.npy` (float32) + `embeddings_metadata.json` (model, dim, per-chunk mapping, skip reasons).
  - **Resumable/rate-limit-safe**: checkpoints after each successful batch (`.embedding_progress.npy`/`.json`), proactively paces requests to stay under Gemini's per-minute quota, and retries a 429'd batch using the provider's requested delay. This lets a large-document embed survive a quota exhaustion without redoing completed work (DEC-2026-08-19-05).
- **Indexing** (`rag/indexing/chroma_index.py`): **ChromaDB** persistent client, cosine-distance collections, one collection per user/project scope. This **replaced FAISS** in August 2026 (DEC-2026-08-26-02) for durable persistence and native collection/metadata management; ranking behavior above it (BM25/RRF/lexical) was preserved unchanged. Existing FAISS-era indexes had to be rebuilt because the on-disk format is incompatible — this is why `.codebase-memory` and some code comments may still mention FAISS/`id_map.json`; that's now historical, superseded by Chroma collections.

### 4.3 Retrieval — the current production hybrid pipeline

> **Correction to internal docs:** `docs/ARCHITECTURE_REFERENCE.md` still describes an older FAISS + lexical-overlap-only retriever. The actual current implementation (verified directly against `packages/rag-core/src/multimodal_rag/rag/retrieval/retriever_2.py` as of this writing) is materially richer: **hybrid dense (Chroma) + sparse (BM25) → Reciprocal Rank Fusion, with optional Cross-Encoder reranking**, matching what `README.md` describes. Treat this section as authoritative for retrieval behavior.

```mermaid
flowchart LR
    Q[User question] --> QE[Embed query — Gemini Embedding 2, search-query format]
    QE --> D["Dense search: Chroma top candidate_pool=40"]
    Q --> S["Sparse search: dependency-free BM25 (own impl), top 40"]
    D --> U[Union candidates by chunk_id]
    S --> U
    U --> RRF["Reciprocal Rank Fusion, k=60:
    rrf = 1/(k+dense_rank) + 1/(k+sparse_rank)"]
    RRF --> LEX["+ lexical_rerank_weight(0.15) * token-overlap tiebreak"]
    LEX --> CE{enable_cross_encoder?}
    CE -->|No — production default| FINAL[Final ranked list, sliced to top_k]
    CE -->|Yes — optional, local| BGE[BAAI/bge-reranker-base rescoring]
    BGE --> FINAL
```

Implementation details (from `retriever_2.py`):
- `RetrieverConfig` defaults: `top_k=5` (caller overrides this — see table below), `enable_hybrid=True`, `enable_cross_encoder=False`, `dense_candidate_k=40`, `sparse_candidate_k=40`, `rrf_k=60`, `lexical_rerank_weight=0.15`, `bm25_k1=1.5`, `bm25_b=0.75`, `min_score=0.0`.
- A pure `enable_keyword_only` BM25-only path also exists (no embedding call at all) — used as a benchmark baseline and low-latency fallback mode, not the production default.
- The 40-candidate pool size was empirically chosen: raises exact-label recall in the CMO evaluation without adding extra embedding API calls, so a chunk that dense search under-ranks but BM25 over-ranks (or vice versa) still survives to the fusion stage.
- `lexical_rerank_weight=0.15` was derived from a concrete failure case (query "long-term technology implications" ranking a "Measurement" chunk above the correct "Technology" chunk) — documented directly in the source as a worked-out minimum-flip-weight calculation, not a guess.
- Every `RetrievedChunk` retains `score` (raw dense score), `bm25_score`, `rrf_score`, `cross_encoder_score`, and `combined_rerank_score` separately — all exposed through the trace layer for debugging (Developer Lab / RAG Trace workspace).

**Caller-specific `top_k`:**

| Caller | Top-k |
|---|---:|
| Chat / `/answer` (current simplified React composer) | 5 |
| CLI `ask` (`--top-k`, default) | 8 |
| Question-wise evaluator | 8 |
| Batch evaluator | 8 |
| Market Intelligence / Market Strategy | 8 (default, request-overridable) |

**Retrieval benchmark (25 verified ground-truth questions, 25/25 chunk-level relevance labels):**

| Metric | Baseline (Chroma + lexical rerank) | Hybrid (Chroma + BM25 → RRF) | Hybrid + Cross-Encoder |
|---|---:|---:|---:|
| Recall@3 | 0.390 | **0.427** | 0.367 |
| Recall@5 | 0.480 | **0.527** | 0.483 |
| Recall@8 | 0.520 | **0.611** | 0.605 |
| Precision@3 | 0.293 | **0.307** | 0.267 |
| Precision@5 | 0.216 | 0.216 | 0.208 |
| Precision@8 | 0.145 | 0.165 | **0.170** |
| MRR | 0.490 | 0.474 | **0.539** |
| nDCG@3 | 0.367 | 0.372 | 0.372 |
| nDCG@5 | 0.399 | 0.409 | **0.420** |
| nDCG@8 | 0.416 | 0.446 | **0.474** |
| Avg latency | 302 ms | **58.6 ms** | 19,695 ms |

**Decision:** Hybrid (Chroma + BM25 → RRF) is production because it gives the best recall/latency trade-off (Recall@8 0.520→0.611, ~58ms). Cross-Encoder improves ranking metrics (MRR, nDCG@8) but costs ~340x the latency, so it stays implemented-but-opt-in.

### 4.4 Generation & citations

- **Prompt builder** (`rag/generation/prompt_builder.py`): each ranked chunk becomes a numbered source block `[S1] (source: <file>, page(s): <pages>) <chunk text>`; the prompt instructs the model to answer only from sources, prefer S1 when sufficient, use headings/bullets, and admit insufficient evidence. Supports bounded conversation history for follow-ups (CLI/eval use single-turn only).
- **Generation** (`rag/generation/answer_generator.py`): one call to `google.genai.Client.models.generate_content()`, model `gemini-3.1-flash-lite`, temperature `0.2`. Captures `prompt_tokens`/`completion_tokens`/`total_tokens` from the response's own usage metadata — no extra token-counting call. Missing `GEMINI_API_KEY` → `AnswerGenerationUnavailableError` (→ HTTP 503); call/response failure → `AnswerGenerationError` (→ HTTP 502).
- **Citations** (`rag/generation/citation.py`): scans generated text for `[S<n>]` markers, resolves them to source metadata in first-appearance order, and separately reports retrieved-but-uncited sources. It does not force citations into the text — the current prompt actually tells the model *not* to mention source markers explicitly, so the frontend's source cards mostly rely on retrieved-chunk metadata rather than resolved in-text markers (a known, documented inconsistency).

### 4.5 Trace / observability

`rag/trace.py::run_rag_trace()` executes retrieval → prompt build → generation → citation resolution exactly once and records a `RAGTrace`: retrieved items (rank, raw/bm25/rrf/cross-encoder/combined scores, chunk text, metadata), citations, uncited sources, retriever/embedding/generation model identifiers, configured vs. actual top-k, per-stage latency (ms), generation token counts. This trace is what powers the frontend's **RAG Trace workspace** and the **question-wise evaluator** — it is computed once and reused, never re-run for display purposes (see DEC-2026-08-19-03).

---

## 5. Multi-agent system

### 5.1 Two live agents today; a third planned

```mermaid
flowchart LR
    U[User objective] --> MI[Market Intelligence Agent]
    MI --> MS[Market Strategy Agent]
    MS -.planned.-> BA[Briefing Agent]
```

### 5.2 RAG Answer mode vs. Market Intelligence Agent mode

| | RAG Answer mode | Market Intelligence Agent mode |
|---|---|---|
| Purpose | Answer one question | Find & analyze evidence-backed trends |
| Process | Question → retrieve → generate | Objective → retrieve (RAG + web) → extract signals → group → validate trend → explain |
| Evidence rule | Most relevant retrieved chunks | A trend normally needs support from ≥2 distinct documents |
| Endpoint | `POST /answer` | `POST /agents/market-intelligence` |
| Data sources | Internal RAG index only | Internal RAG index **and** live Tavily web search (security-filtered) |

A **signal** is one individual observation from a document/web source. A **trend** is a validated pattern normally requiring ≥2 independent sources. The agent explicitly separates **facts** (directly evidenced) from **inference** (its interpretation), and will return a `partial`/`needs_input` status rather than invent a trend from weak or single-source evidence. Statuses: `completed`, `partial`, `needs_input`, `failed`.

### 5.3 Market Intelligence Agent — execution flow

```mermaid
sequenceDiagram
    participant UI as App.jsx
    participant API as router.py
    participant MI as MarketIntelligenceAgent
    participant RAG as InProcessRAGClient/RAGService
    participant Web as WebSearchClient (Tavily)
    participant Guard as SourceGuardService

    UI->>API: POST /agents/market-intelligence {objective, scope, user_id}
    API->>MI: run(request)
    MI->>MI: _plan() — bounded intent-aware web queries + RAG query
    MI->>RAG: retrieve() scoped internal evidence
    MI->>Web: search() external evidence
    Web->>Guard: filter_results()
    Guard->>Guard: validate_url() [VirusTotal v3]
    Guard->>Guard: validate_content() [Check Point AI Guardrails, "tool" message]
    Guard-->>MI: only approved results become AgentEvidence
    MI->>MI: _deduplicate() + _with_recency()
    MI->>MI: _analyze() — Gemini structured generation over combined evidence
    MI->>MI: _build_findings() — join model signal IDs to evidence, keep groups with >=2 docs
    MI-->>API: AgentResponse (findings, sources, task_id, trace_id)
    API-->>UI: MarketIntelligenceMessage renders findings + source cards
```

- Missing industry/geography/time-range resolve to cross-industry / global / last-30-days defaults rather than blocking.
- Error classification at the agent boundary: missing model config → `generation_unavailable`/503; invalid/failed model response → `generation_error`/502. A RAG-only or web-only source failure is treated as a partial-result limitation, not a hard failure, as long as the other source is usable.
- Candidates that can't be normalized or evidence-linked are silently dropped rather than causing a 502 — the agent returns whatever validated intelligence remains.

### 5.4 Source Guard — the external-evidence security boundary

**Every** web result must pass **both** checks before it can become agent evidence — a fail-closed design:

```mermaid
flowchart LR
    R[Tavily search/research result] --> V1{validate_url — VirusTotal v3}
    V1 -->|malicious/suspicious above threshold, unknown+strict, malformed, or API failure| BLOCK1[Blocked]
    V1 -->|passes| V2{validate_content — Check Point AI Guardrails, tool message}
    V2 -->|prompt-injection/unsafe content detected, or credentials/service unavailable| BLOCK2[Blocked]
    V2 -->|passes| E[Becomes AgentEvidence]
```

- Both checks fail closed: missing credentials, malformed URLs, rate limits, and invalid/unavailable security responses all result in exclusion, never inclusion-by-default.
- **History of this component** (see §7 decisions): originally used Google Web Risk → replaced with URLhaus (avoid GCP billing) → replaced again with **VirusTotal v3** (configurable malicious/suspicious thresholds). Check Point AI Guardrails content screening has been constant throughout.
- Config: `WEB_SEARCH_SECURITY_ENABLED`, `VIRUSTOTAL_API_KEY`, `VIRUSTOTAL_MAX_MALICIOUS` (opt.), `VIRUSTOTAL_MAX_SUSPICIOUS` (opt.), `LAKERA_GUARD_API_KEY`... *(README shows Lakera-named env vars for the Guardrails step; verify current names in `services/api/src/multimodal_rag/api/config.py::APISettings` before configuring)*, `WEB_SEARCH_SECURITY_TIMEOUT_SECONDS`.

### 5.5 Market Strategy Agent

- Consumes **only**: a strict `MarketStrategyRequest`, a completed `MarketIntelligenceResponse`, and the user's stored memory/context — it has **no** RAG, web-search, or Market Intelligence client of its own. This is a deliberately enforced boundary: the API is the intelligence-first coordinator, not the agent.
- Flow: `_assess_readiness()` (considers MI handoff + stored context) → if ready, `_develop_strategy()` (validates every recommendation's evidence IDs against MI's approved sources) → `_response()` (copies the validated MI categories directly into `MarketStrategyResponse`, no re-summarization) → frontend renders a compact intelligence snapshot + strategy, or one clarification question.
- **Readiness gate:** proceeds as long as MI returned **at least one approved source**, even if MI's stricter structured-finding normalization stripped out every trend/competitor/opportunity/risk category (DEC-2026-08-30-09) — avoids a false "no evidence" failure when partial-but-real evidence exists. Zero approved sources still blocks.
- **Clarification continuation:** when readiness is `needs_input`, the *original* strict request and the *serialized* MI response are persisted on the user's chat row (`PostgresUserStore.set_strategy_state()`). The user's next reply is folded into memory, then `get_strategy_state()` restores the original objective + intelligence so Market Intelligence is **not re-run** — avoiding duplicate research cost and evidence drift across a multi-turn clarification. State is cleared on completion or terminal failure.
- Optional-field robustness: the model's structured output is normalized field-by-field — a malformed optional item (e.g., a plain string where a typed risk object was expected) is silently dropped rather than failing the entire response (DEC-2026-08-30-11).
- Frontend: `MarketStrategyMessage()` in `App.jsx`, with a dedicated `market-strategy-message` CSS scope for consistent 14px/1.65 typography and near-black list contrast (DEC-2026-08-30-12).

### 5.6 Planned: Briefing Agent

Not implemented. The intended chain is `Market Intelligence → Market Strategy → Briefing`, where Briefing would turn strategy output into an executive-ready briefing. No code exists for this yet.

---

## 6. Accounts, persistence & memory

```mermaid
sequenceDiagram
    participant UI as App.jsx
    participant API as router.py
    participant Acc as PostgresUserStore
    participant Mem as MemoryService

    UI->>API: POST /auth/signup or /auth/login
    API->>Acc: create_user() [scrypt hash] / authenticate()
    Acc-->>API: user_id
    Acc->>Acc: create_session() — random bearer token, only its SHA-256 hash stored
    API-->>UI: bearer token

    UI->>API: POST /answer or /agents/... (Authorization: Bearer <token>)
    API->>API: require_api_token() resolves token -> authenticated_user_id
    API->>API: _authenticated_user_id() rejects any mismatched scope in the payload
    API->>Mem: process_message(user_id, message) — extract durable facts
    Mem->>Mem: MemoryExtractor.extract() -> structured candidates
    Mem->>Mem: validate/dedupe/insert/update -> PostgresMemoryRepository
    API->>API: run existing RAG / MI / MS flow
    API->>Acc: record user + assistant messages under chat_id (user_chat_messages)

    UI->>API: GET /chats
    API->>Acc: list chats for authenticated_user_id
    UI->>API: GET /chats/{chat_id}
    API->>Acc: fetch full conversation, scoped to authenticated_user_id
```

- Passwords: **scrypt**, salted, never stored in plaintext. Sessions: random bearer token; **only its hash** is persisted server-side.
- Every protected route checks that the request's claimed scope (`user_id`, `project_id`) matches the token's authenticated identity — this runs before ingestion, retrieval, RAG answers, and Market Intelligence.
- **Legacy fallback mode** (no PostgreSQL configured): a single shared bearer token from `RAG_API_AUTH_TOKEN`/`RAG_API_USERNAME`/`RAG_API_PASSWORD` — no per-user accounts, no saved chat history.
- **Memory service**: authenticated user messages pass through a service-owned, user-scoped memory boundary (`MemoryService.process_message` / `get_user_context`) whenever persistence is configured. This is what lets Market Strategy use "stored business context" without the user re-stating it every session.
- Config: `RAG_DATABASE_URL` (preferred) or `RAG_MEMORY_DATABASE_URL` (compat fallback) — Postgres connection string. On startup the API auto-creates account/session/chat/memory tables.

### User/project data isolation (independent of accounts)

Every ingestion and retrieval call is additionally scoped by `user_id` (and optional `project_id`) at the **filesystem/index** level:

```text
user-data/<user_id>/artifacts/
├── ingestion/    # per-document raw extraction, chunks.json, audit files (write-only for retrieval)
└── index/        # ChromaDB PersistentClient directory (the actual vector DB + all metadata retrieval reads)
```

`RAG_USER_DATA_ROOT` overrides the root (defaults to `<project root>/user-data`, **not** `runtime-data`). This scoping predates and is orthogonal to the account system — even in legacy single-token mode, each `user_id` string gets its own isolated corpus and index.

> **As of 2026-08-31 (DEC-2026-08-31-01):** `runtime-data/` is exclusively the CLI/evaluation global corpus and application logs — no API/frontend-scoped tenant data lives there anymore. Retrieval also no longer reads `chunks.json` at query time at all: each child chunk's parent-section text and full `ChunkMetadata` are now stored directly on its ChromaDB record (`parent_chunk_text`, `metadata_json` fields) at embedding time, so Chroma alone is sufficient to serve `/retrieve` and `/answer`. `chunks.json`/raw extraction/audit files are still written during ingestion for debugging, but retrieval never reads them back. Existing content under the old `runtime-data/users/` location was **not** auto-migrated; those documents need re-ingesting under the new `user-data/` root to pick up both the new location and the new Chroma-native metadata.

---

## 7. Ingestion & upload security pipeline (production API path)

```mermaid
flowchart TD
    A[Multipart upload: PDF/MP4/MP3/DOC/DOCX/PPT/PPTX, <=50MB] --> B[router.py::ingest_pdf format+MIME allowlist check]
    B --> C{PDF?}
    C -->|yes| D[signature/readability/page-count checks + normalized-text duplicate hash]
    D -->|SHA-256 match| E[HTTP 409, temp upload deleted]
    C -->|any type| F[IngestionJobManager.submit — re-checks registry, reserves in-flight hash]
    F --> G{RAG_CLAMAV_ENABLED?}
    G -->|true| H[ClamAVScanner.scan via clamd]
    H -->|malware| Q[Move to quarantine/, fail job]
    H -->|scanner failure, fail-closed default| Q
    H -->|clean| I
    G -->|false| I[Parse by type]
    I --> I1[PDF: orchestrator.ingest_document]
    I --> I2[MP3/MP4: Deepgram ingest_media]
    I --> I3[DOC/DOCX: python-docx / LibreOffice-then-python-docx]
    I --> I4[PPT/PPTX: python-pptx / LibreOffice-then-python-pptx]
    I1 --> J[chunks.json written]
    I2 --> J
    I3 --> J
    I4 --> J
    J --> K[_embed_pending_documents — embeds new upload + any previously un-embedded scoped docs]
    K --> L[build_index_from_output_dir + save_index -> scoped ChromaDB collection]
    L --> M[clear_scoped_index_cache]
    M --> N[Job reports: upload, security_scan, extract, chunk, embed, store, index, complete]
```

Notable design points:
- **File-level dedup at two layers**: PDF text-fingerprint check at upload time (409 fast-fail) *and* chunk-level SHA-256 dedup at ingestion time — an exact duplicate PDF can legitimately produce zero new chunks.
- **Backfill-before-rebuild** (DEC-2026-08-20-03): before rebuilding a scope's index, the job re-embeds *every* scoped document that has chunks but no complete embedding artifact — not just the new upload. This prevents a duplicate-upload edge case from producing an empty/broken index for the whole scope. Empty/malformed embedding arrays are skipped by the index builder, and the API explicitly rejects an empty collection as unavailable rather than serving it.
- **ClamAV** streams to a local `clamd` daemon (not `clamscan` per-request, to avoid signature-reload latency; not linked to libclamav directly, to avoid GPL coupling). Fails closed by default (`RAG_CLAMAV_FAIL_CLOSED=false` is an explicit opt-out).
- **Deepgram** (media), **LibreOffice headless** (legacy DOC/PPT conversion) are the two external/local tooling dependencies added for non-PDF formats — both run only *after* the ClamAV gate.
- **Per-region progress reporting**: `ingest_document()` accepts an optional progress callback; the API job snapshot and the frontend ETL workspace show live per-region (not just per-stage) progress during long PDF extractions (DEC-2026-08-27-01).

---

## 8. API surface (FastAPI)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/auth/signup` | Create account |
| `POST` | `/auth/login` | Authenticate, returns bearer token |
| `GET` | `/chats` | List authenticated user's saved conversations |
| `GET` | `/chats/{chat_id}` | Fetch one full conversation |
| `POST` | `/ingest` | Upload + queue a scoped ingestion job (multipart, PDF/MP4/MP3/DOC/DOCX/PPT/PPTX) |
| `GET` | `/ingest/{job_id}` | Poll ingestion job status/stage/progress |
| `POST` | `/retrieve` | User-scoped retrieval only (no generation) |
| `POST` | `/answer` | Retrieval + grounded Gemini generation (requires `GEMINI_API_KEY`) — also returns `rag_trace` |
| `POST` | `/agents/market-intelligence` | Run the Market Intelligence Agent |
| `POST` | `/agents/market-strategy` | Run the Market Strategy Agent (intelligence-first, API-coordinated) |
| `POST` | `/web-search/research` | Multi-company Tavily Research report (0–10 named companies, async polling under the hood) |

All routes except signup/login require `Authorization: Bearer <token>` (or the legacy shared token in single-account mode). `/retrieve` and `/answer` return the trace ID in the `X-Trace-ID` response header; `/answer` also includes `trace_id` in the JSON body.

Response codes of note: `404` unknown user index, `503` generation credentials/auth not configured, `422` invalid payload, `409` duplicate PDF upload, `502` invalid/failed model response, `503` missing model config.

---

## 9. Frontend (apps/frontend — React + Vite)

Single-page app (`App.jsx`) with these workspaces/modes, all going through `api.js` to the FastAPI backend:

- **Ask the intelligence base** — normal RAG chat (`/answer`), fixed `top_k=5` in the current simplified composer, no exposed project/user-scope/depth controls.
- **Market Intelligence Agent** — optional `company_name`/`company_url` focus fields, same fixed `top_k=5`.
- **Market Strategy** — intelligence-first flow described in §5.5; renders a compact MI snapshot + strategy or a single clarification prompt.
- **Web Search** — multi-company Tavily Research mode, 0–10 optional `{name, url}` rows; question-only research is supported (no company rows required).
- **Ingest documents (ETL workspace)** — drag-and-drop/file-picker for the PDF/MP4/MP3/DOC/DOCX/PPT/PPTX allowlist (50MB client-side cap), animated pipeline progress (Upload → Extraction → Chunking → Embedding → Store → Build index) driven by polling `GET /ingest/{job_id}`, plus a `localStorage`-backed recent-artifacts list (`cmo-etl-documents`) — this local list is a UI convenience, not the source of truth.
- **RAG Trace workspace** — renders the `rag_trace` object returned alongside an `/answer` response: ranked chunks, all four score types, citations, latency, tokens — without making a second RAG call.
- **Saved chats** — a menu-controlled left drawer (not top nav) listing past conversations via `GET /chats`; on desktop it reserves layout width when open, on narrow screens it overlays.
- **Auth** — sign up / sign in / sign out; only the signed-in user's chats are restored.

The current starter-question suggestions are the three tied highest-scoring questions from `evaluation/evaluation_result.md` (questions 1, 2, 4), not generic placeholders — a static UI-only change with no backend effect.

Dev commands:
```powershell
cd apps/frontend
npm install
npm run dev       # http://localhost:5173
npm run build      # production bundle
npm run preview    # serve the build locally
```
`VITE_API_URL` (in `apps/frontend/.env`) points at the API, default `http://127.0.0.1:8000`. `RAG_API_CORS_ORIGINS` on the backend allows additional browser origins.

---

## 10. Evaluation

Two deliberately separate evaluation tracks:

### 10.1 Retrieval-only benchmark
`python -m multimodal_rag.evaluation.retrieval_benchmark` — Recall@k, Precision@k, MRR, nDCG@k, latency across 3 controlled modes (baseline / hybrid / hybrid+cross-encoder) over 25 questions with **25/25 verified chunk-level relevance labels**. Results table is in §4.3 above; full report at `runtime-data/evaluation/retrieval/retrieval_benchmark_results.md`.

### 10.2 RAGAS (generated-answer quality)
5 metrics: **Faithfulness**, **Answer Relevancy**, **Context Precision**, **Context Recall**, **Answer Correctness**; composite = arithmetic mean of non-NaN metrics.
- Evaluator: **Ollama `qwen2.5:7b`** (local, default) or **Groq `openai/gpt-oss-20b`** (optional, `EVALUATOR_PROVIDER=groq`). Both share the same Gemini-Embedding-2 adapter; only the judge model differs.
- Groq gets metric-specific handling: `Faithfulness`/`AnswerCorrectness` cloned with `max_tokens=4096`; `AnswerRelevancy` forced to `strictness=1` because Groq rejects `n>1`.
- `RunConfig(max_retries=3, max_wait=120, timeout=600, max_workers=1)`, `raise_exceptions=False`.
- **Single-question**: `python -m multimodal_rag.evaluation.question_runner --id N` (or `--question "..."`, or `--interactive`) — print-only, no persistence, runs one top-8 `run_rag_trace()` + one RAGAS call.
- **Batch (25-question ground truth)**: `python -m multimodal_rag.evaluation.runner` — resumable via completed-ID tracking in provider-specific CSVs, regenerates `evaluation_report_<provider>.md` after every success.
- **CMO 30-question corpus** (source-linked, exact chunk labels): `python -m multimodal_rag.evaluation.cmo_metrics` — retrieval accuracy (Hit@5, first-relevant-rank, MRR) reported **separately** from exact chunk precision/recall/F1, plus faithfulness/relevancy/correctness as Gemini-judge 0–1 scores. Resumable with `--resume`; pauses interactively on Gemini quota exhaustion, waiting for a `.env` key swap before retrying the same question (DEC-2026-08-24-08). Single consolidated output: `evaluation/evaluation_result.md` (no more per-question JSON sprawl — this was deliberately consolidated, DEC-2026-08-24-05/06/07).
- **Hybrid-vs-BM25-only comparison**: `python -m multimodal_rag.evaluation.cmo_retrieval_comparison` → `evaluation/results/cmo_retrieval_comparison.json`.

**Latest verified live rehearsal** (ground-truth ID 1, "What does 'Enterprise AI' mean..."): Faithfulness 1.000, Answer Relevancy 0.794, Context Precision 0.367, Context Recall 1.000, Answer Correctness 0.407, **Composite 0.713**. Full RAG 20.30s, evaluation 183.14s (Groq rate-limited and recovered mid-run).

---

## 11. Testing

`python -m pytest` (or `.venv\Scripts\python.exe -B -m unittest discover -s tests -v`). Provider calls (Gemini, Groq, Ollama, RAGAS, Tavily, VirusTotal, Check Point, ClamAV, Deepgram) are **mocked** — no live credentials needed for the standard suite. Last recorded baseline: 68 tests passing (README), with a later decision log entry noting 152 passing plus one expected local-Chroma skip after a subsequent feature addition.

Key test files: `test_api.py`, `test_ingestion_jobs.py`, `test_pdf_loader.py`, `test_presentation_ingestion.py`, `test_word_ingestion.py`, `test_semantic_chunking.py`, `test_offline_embeddings.py`, `test_rag_trace.py`, `test_cli_ask.py`, `test_cmo_metrics.py`, `test_cmo_retrieval_comparison.py`, `test_clamav.py`, `test_deepgram_media.py`, `test_web_search_client.py`, `test_company_research.py`, `test_market_intelligence_agent.py`, `test_market_strategy_agent.py`, `test_source_guard.py`, `test_virustotal_source_guard.py`, `test_memory_service.py`, `test_deduplication.py`.

---

## 12. What has been implemented, in build order (condensed engineering history)

This is a chronological digest of `docs/decisions.md` (100+ entries) — the "how we got here":

1. **Core RAG pipeline first**: PDF loading (PyMuPDF) → Docling layout segmentation → OCR/Vision escalation policy → structure-aware chunking → local embeddings → FAISS retrieval → Gemini generation → citations.
2. **Trace & evaluation layer**: `RAGTrace`, question-wise evaluator, resumable batch RAGAS evaluator, retrieval-only benchmark separate from RAGAS.
3. **Embedding migration**: local MiniLM → hosted **Gemini Embedding 2** (768-dim default), with checkpointed/rate-limit-paced batch embedding.
4. **Chunking quality pass**: character-splitting → **sentence-safe semantic packing**; then **parent/child hierarchy** (small embedded children + full-section parent context at query time); then **SHA-256 exact-dedup** across children.
5. **Web search seam**: provider-neutral `WebSearchClient` → Tavily adapter → multi-company async **Tavily Research** route → **question-only research** (companies became optional).
6. **Platform promotion** (DEC-2026-08-26-01): nested `multimodal-rag/` repo → promoted root layout (`apps/`, `services/`, `packages/`), one coordinated `multimodal_rag.*` namespace package.
7. **FAISS → ChromaDB** (DEC-2026-08-26-02): persistent, collection-managed dense index; ranking logic above it preserved.
8. **Upload security hardening**: ClamAV malware scan gate → MP3/MP4 via Deepgram → DOC/DOCX via python-docx/LibreOffice → PPT/PPTX via python-pptx/LibreOffice, each gated behind the same ClamAV check.
9. **Per-region ingestion progress** surfaced through the API and frontend ETL workspace.
10. **Backend launcher resilience**: `run_backend.py` re-executes itself under `.venv` to avoid Windows PATH/global-interpreter mismatches.
11. **Hybrid retrieval** activated as production default: Chroma dense + BM25 sparse → RRF, with an optional (off-by-default) BGE Cross-Encoder — validated by a dedicated 25-question, exact-label retrieval benchmark.
12. **Market Intelligence Agent** built: signal extraction → grouping → ≥2-document trend validation → fact/inference separation → provenance-preserving response.
13. **Web-evidence Source Guard** (fail-closed): Google Web Risk → **URLhaus** → **VirusTotal v3** for URL reputation, plus Check Point AI Guardrails for content, both mandatory before any web result becomes agent evidence.
14. **Accounts & persistence**: PostgreSQL user accounts (scrypt passwords), bearer sessions (hash-only storage), persisted per-user chat history, request-scope-vs-token authorization enforcement.
15. **User memory boundary**: `MemoryService` extracts and stores durable user/business facts from authenticated messages, later consumed by Market Strategy.
16. **Market Strategy Agent** built, then iteratively hardened: intelligence-first API coordination (agent has zero research clients of its own) → evidence-based readiness gate relaxed to "any approved source" → intelligence snapshot copied (not regenerated) into the strategy response → malformed optional model output normalized field-by-field instead of failing the whole response → consistent strategy-response typography/contrast.
17. **UI consolidation**: saved chats moved into a collapsible drawer; simplified research composer with fixed top-k=5 and optional company-focus fields; evaluation-derived starter questions.

---

## 13. Known issues / current limitations (as of latest docs)

- **Retrieval precision** can still include noisy top-k chunks even when the required evidence is present (Context Recall 1.0 but Context Precision ~0.37 in the ID-1 rehearsal) — expected given top-k=8 without a hard relevance cutoff.
- **`source_pages` ground-truth annotations** have known inconsistencies vs. actual extracted/indexed page metadata; do not treat as judged relevance labels without an audit.
- **Generation cost** is not computed — Gemini token counts are captured but no pricing table exists for them (Groq evaluator cost is computed; Gemini generation cost is not).
- **Citation contract mismatch**: the generation prompt discourages the model from emitting `[S#]` markers while the citation resolver still supports parsing them — so citation UI mostly falls back to retrieved-chunk metadata rather than resolved in-text markers.
- **Page-level Vision POC** hard-coded to specific PDF pages (`orchestrator.py`) — flagged as technical debt, not a routing decision yet.
- **Dependency drift**: evaluation stack (RAGAS/LangChain family) is not fully declared in `requirements.txt`; some `pip check` conflicts exist in `.venv` (LangChain-family versions, `pdftext`/`pypdfium2`).
- **Scale**: no large-corpus (multi-thousand-document) retrieval benchmark exists yet; Chroma's HNSW behavior at scale is unverified beyond the current corpus size.
- **Job state is process-local**: the ingestion job manager's status is in-memory per API process — lost on restart, and a multi-worker deployment would need a shared job store (explicitly called out as a future replacement, not a current bug).
- **No Briefing Agent yet** — the documented three-agent chain (`Market Intelligence → Market Strategy → Briefing`) is only two-thirds built.

---

## 14. How to run this locally

```powershell
# 1. Environment
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install --no-deps --no-build-isolation -e .

# 2. Secrets (minimum for RAG chat)
$env:GEMINI_API_KEY = "..."
$env:GOOGLE_API_KEY = "..."          # embeddings; GEMINI_API_KEY also accepted
$env:GEMINI_EMBEDDING_DIMENSION = "768"

# 3. Optional: accounts + persisted chats
$env:RAG_DATABASE_URL = "postgresql://user:password@host:5432/cmo_intelligence"
# else legacy single-account mode: RAG_API_AUTH_TOKEN / RAG_API_USERNAME / RAG_API_PASSWORD

# 4. Optional: web research + its security gate
$env:TAVILY_API_KEY = "..."
$env:WEB_SEARCH_SECURITY_ENABLED = "true"
$env:VIRUSTOTAL_API_KEY = "..."
$env:LAKERA_GUARD_API_KEY = "..."     # verify exact var name in api/config.py

# 5. Ingest a PDF and build the index (CLI path)
python -m multimodal_rag.cli.ingest "runtime-data/input/my-document.pdf"
python -m multimodal_rag.cli.build_index
python -m multimodal_rag.cli.ask "What are the key market trends?"

# 6. Run the full stack
python run_backend.py                 # FastAPI on :8000, asks for bearer token interactively (legacy mode)
cd apps/frontend; npm install; npm run dev   # React on :5173
```

---

## 15. Where to look for more (in this repo)

| Question | File |
|---|---|
| "What's the exact current repo state / handoff notes?" | `docs/PROJECT_STATUS.md` |
| "What was decided and why, for a specific change?" | `docs/decisions.md` (chronological, DEC-YYYY-MM-DD-N entries) |
| "What's the exact call chain for feature X?" | `docs/flow.md` (kept in sync with real code per `AGENTS.md` rules) |
| "Deep implementation details, module-by-module" | `docs/ARCHITECTURE_REFERENCE.md` — **note**: its retrieval section (FAISS-based) is stale; trust §4.3 of this document or the source (`retriever_2.py`) instead |
| "RAG Answer vs Market Intelligence, explained simply" | `docs/RAG_MODE_VS_MARKET_INTELLIGENCE_AGENT.md` |
| "Rules for making changes as a coding agent" | `AGENTS.md` |
| "Package/folder purpose, one paragraph each" | `docs/Project-architecture.md` |
