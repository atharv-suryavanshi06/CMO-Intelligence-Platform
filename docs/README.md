# Multimodal RAG for PDF Intelligence

[![Python
3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![ChromaDB](https://img.shields.io/badge/Vector%20Search-ChromaDB-FF6F00)](https://www.trychroma.com/)
[![License:
MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A traceable multimodal RAG system for complex PDFs. It combines
layout-aware extraction, OCR and conditional vision understanding with
local embeddings, **hybrid dense + sparse retrieval (ChromaDB + BM25)**,
**Reciprocal Rank Fusion (RRF)**, optional **Cross-Encoder reranking**,
Gemini answer generation, and built-in retrieval and RAGAS evaluation.

The application is split into a clean **User Workspace** for document
Q&A and a **Developer Lab** for retrieval inspection, traces, latency,
token telemetry, and evaluation.

## What it does

``` text
PDF → Extract → Chunk → Embed → Index
                              ↓
Question → Dense ChromaDB + Sparse BM25 → RRF → Optional Cross-Encoder → Gemini → Answer
```

The pipeline keeps document, page, section, extraction, and chunk
metadata throughout processing so the same evidence used for an answer
can also be inspected in the Developer Lab.

## Key features

-   Layout-aware PDF ingestion with **Docling** and **PyMuPDF**
-   Native extraction, **RapidOCR**, and conditional **Gemini Vision**
-   Structure-aware chunking with page and section metadata
-   Gemini Embedding 2 (`gemini-embedding-2`) embeddings
-   768-dimensional normalized vectors by default (configurable to 1536 or 3072)
-   **ChromaDB** dense vector retrieval
-   Dependency-free **BM25** sparse lexical retrieval
-   **Reciprocal Rank Fusion (RRF)** across dense and sparse candidates
-   Optional local **`BAAI/bge-reranker-base` Cross-Encoder** reranking
-   Production default: **Hybrid ChromaDB + BM25 → RRF**
-   Retrieval telemetry for vector, BM25, RRF, and Cross-Encoder scores
-   **Gemini 3.1 Flash Lite** grounded answer generation
-   Reusable RAG traces with chunks, scores, latency, and token
    telemetry
-   Separate **User Workspace** and **Developer Lab**
-   Five-metric **RAGAS** answer/context evaluation
-   Retrieval-only benchmarking with **Recall@k, Precision@k, MRR, nDCG@k, and latency**
-   **25/25 verified** chunk-level retrieval relevance labels
-   Question-wise and resumable batch evaluation

# Architecture

## 1. RAG pipeline

``` mermaid
flowchart TD
    A["PDF Documents"] --> B["Ingestion & Extraction<br/>Docling + PyMuPDF<br/>RapidOCR + Gemini Vision when needed"]
    B --> C["Structure-Aware Chunking<br/>Size: 1000 · Overlap: 150"]
    C --> D["Gemini Embedding 2<br/>768 dimensions by default"]
    D --> E["ChromaDB Collection<br/>Normalized vectors · cosine space"]

    Q["User Question"] --> QE["Gemini Embedding 2 Query Embedding"]
    QE --> F["Dense Retrieval<br/>ChromaDB"]
    Q --> S["Sparse Retrieval<br/>BM25"]
    F --> U["Candidate Union<br/>Deduplicate by chunk_id"]
    S --> U
    U --> R["Reciprocal Rank Fusion<br/>RRF · k=60"]
    R --> CE{"Cross-Encoder enabled?"}
    CE -->|"No · production default"| I["Final Context<br/>Chat: Top 5 · CLI/Evaluation: Top 8"]
    CE -->|"Yes · optional"| X["BAAI/bge-reranker-base<br/>Query–Chunk Reranking"]
    X --> I
    I --> J["Gemini 3.1 Flash Lite"]
    J --> K["Grounded Answer"]
```

The ingestion stage first tries local document understanding. OCR and
Gemini Vision are used when the document region requires them; the
downstream RAG pipeline operates on the validated extracted content.

## 2. User Workspace and Developer Lab

``` mermaid
flowchart LR
    A["Multimodal RAG"] --> B["User Workspace"]
    A --> C["Developer Lab"]

    B --> B1["Clean RAG Chat"]
    B --> B2["Curated Questions"]
    B --> B3["Conversation"]

    C --> C1["Playground"]
    C --> C2["Trace Inspector"]
    C --> C3["Evaluation Bench"]

    C2 --> D["Chunks · ChromaDB · BM25 · RRF · Cross-Encoder<br/>Tokens · Latency · Evidence"]
    C3 --> E["RAGAS Metrics<br/>+ Retrieval Benchmark"]
```

**User Workspace** focuses on the answer and conversation. Technical
retrieval evidence stays out of the normal chat view.

**Developer Lab** can run test questions, inspect previously stored
traces, compare retrieved chunks and ranking scores, and explicitly run
RAGAS evaluation.

## 3. RAGAS evaluation

``` mermaid
flowchart TD
    A["Ground-Truth Question<br/>+ Reference Answer"] --> B["RAG Run"]
    B --> C["Retrieved Context"]
    B --> D["Gemini Answer"]
    C --> E["RAGAS"]
    D --> E
    E --> F["Faithfulness"]
    E --> G["Answer Relevancy"]
    E --> H["Context Precision"]
    E --> I["Context Recall"]
    E --> J["Answer Correctness"]
    F --> K["Composite Score"]
    G --> K
    H --> K
    I --> K
    J --> K
```

RAGAS is an explicit developer workflow---normal Chat does **not** run
evaluation. The evaluator defaults to local **Ollama `qwen2.5:7b`**,
with **Groq `openai/gpt-oss-20b`** available as an optional provider.

# How retrieval works

Production retrieval uses a **hybrid dense + sparse pipeline** rather
than relying on a single retrieval signal.

``` mermaid
flowchart LR
    Q["Question"] --> D["Dense Search<br/>Gemini Embedding 2 + ChromaDB"]
    Q --> S["Sparse Search<br/>BM25"]
    D --> U["Candidate Union"]
    S --> U
    U --> R["RRF Fusion"]
    R --> C["Top Candidates"]
    C -. "optional" .-> X["BGE Cross-Encoder"]
    X -. "reranked candidates" .-> F["Final Context"]
    C --> F
```

1.  The question is embedded with Gemini Embedding 2 using the search-query format.
2.  **ChromaDB** retrieves semantically similar dense candidates.
3.  **BM25** independently retrieves sparse lexical candidates from the same indexed chunk corpus.
4.  Dense and sparse results are combined by `chunk_id`.
5.  **Reciprocal Rank Fusion (RRF)** combines the two ranked lists without directly mixing incompatible vector and BM25 score scales.
6.  An optional local **`BAAI/bge-reranker-base` Cross-Encoder** can jointly score query–chunk pairs and rerank the fused candidate set.
7.  The caller-visible result count is returned: **Chat top 5**, **CLI top 8 by default**, and **Evaluation top 8**.

The production configuration keeps Hybrid retrieval enabled and the Cross-Encoder disabled by default:

``` python
enable_hybrid = True
enable_cross_encoder = False
```

The trace layer preserves the underlying retrieval signals for developer inspection, including raw vector similarity, BM25 score, RRF score, and Cross-Encoder score when that stage is enabled.

## Retrieval benchmark

Retrieval quality is evaluated separately from generated-answer quality. The benchmark compares three controlled retrieval modes over **25 ground-truth questions with 25/25 verified chunk-level relevance judgments**.

``` mermaid
flowchart TD
    Q["25 Verified Questions"] --> B["Baseline<br/>ChromaDB + lexical reranking"]
    Q --> H["Hybrid<br/>ChromaDB + BM25 → RRF"]
    Q --> C["Hybrid + Cross-Encoder<br/>ChromaDB + BM25 → RRF → BGE"]
    B --> M["Recall@k · Precision@k<br/>MRR · nDCG@k · Latency"]
    H --> M
    C --> M
    M --> D["Production Decision<br/>Hybrid"]
```

| Metric | Baseline | Hybrid | Hybrid + Cross-Encoder |
|---|---:|---:|---:|
| Recall@3 | 0.390 | **0.427** | 0.367 |
| Recall@5 | 0.480 | **0.527** | 0.483 |
| Recall@8 | 0.520 | **0.611** | 0.605 |
| Precision@3 | 0.293 | **0.307** | 0.267 |
| Precision@5 | **0.216** | **0.216** | 0.208 |
| Precision@8 | 0.145 | 0.165 | **0.170** |
| MRR | 0.490 | 0.474 | **0.539** |
| nDCG@3 | 0.367 | **0.372** | **0.372** |
| nDCG@5 | 0.399 | 0.409 | **0.420** |
| nDCG@8 | 0.416 | 0.446 | **0.474** |
| Avg retrieval latency | 302.161 ms | **58.645 ms** | 19,694.866 ms |

Hybrid retrieval was selected as the production default because it delivered the strongest retrieval coverage, increasing **Recall@8 from 0.520 to 0.611**, while keeping practical retrieval latency low. The Cross-Encoder improved ranking-oriented metrics such as **MRR** and **nDCG@8**, but introduced substantial additional inference latency in this benchmark.

The Cross-Encoder therefore remains implemented and available for experimentation, while **ChromaDB + BM25 → RRF** is the production quality/latency trade-off.

Full benchmark results are recorded in:

``` text
runtime-data/evaluation/retrieval/retrieval_benchmark_results.md
```

Run the retrieval-only benchmark with:

``` powershell
python -m multimodal_rag.evaluation.retrieval_benchmark
```

# Quickstart

The project requires **Python 3.10+**. The verified development
environment uses Python 3.11.

## 1. Clone and create the environment

``` powershell
git clone https://github.com/Cosmos-Krishna/multimodal-rag.git
Set-Location multimodal-rag

py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

Once `(.venv)` is visible in the terminal, the shorter `python -m ...`
commands below are enough.

## 2. Install the application

``` powershell
python -m pip install -r requirements.txt
python -m pip install --no-deps --no-build-isolation -e .
```

The evaluation stack is installed separately in the currently verified
environment:

``` powershell
python -m pip install `
  ragas==0.3.5 `
  langchain==0.3.30 `
  langchain-core==0.3.86 `
  langchain-community==0.3.31 `
  langchain-text-splitters==0.3.11 `
  langchain-groq==0.2.4 `
  langchain-ollama==0.2.3 `
  langchain-huggingface==0.3.1 `
  datasets==5.0.0 `
  pandas==3.0.3 `
  tqdm==4.68.4 `
  tenacity==9.1.4 `
  python-dotenv==1.2.2
```

## 3. Configure Gemini

``` powershell
$env:GEMINI_API_KEY = "your-gemini-key"
```

Gemini is used for answer generation and for ingestion regions that
require Vision escalation.

## 4. Configure the RAGAS evaluator

### Local Ollama

``` powershell
ollama pull qwen2.5:7b
ollama serve
$env:EVALUATOR_PROVIDER = "ollama"
```

### Optional Groq

``` powershell
$env:EVALUATOR_PROVIDER = "groq"
$env:GROQ_API_KEY = "your-groq-key"
```

The default Groq evaluator model is `openai/gpt-oss-20b`.

## 5. Configure Gemini Embeddings

Gemini Embedding 2 is a hosted model. Set the same key used for answer
generation before indexing or querying:

``` powershell
$env:GOOGLE_API_KEY = "your-gemini-key"  # GEMINI_API_KEY is also supported
$env:GEMINI_EMBEDDING_DIMENSION = "768"
```

Changing the embedding model or dimension requires re-embedding all
documents and rebuilding the ChromaDB collection; old and new vector spaces are
not comparable.

# From PDF to RAG answer

## 1. Add a PDF

Place the PDF in:

``` text
runtime-data/input/
```

Example:

``` text
runtime-data/input/my-document.pdf
```

## 2. Ingest and extract

``` powershell
python -m multimodal_rag.cli.ingest "runtime-data/input/my-document.pdf"
```

This performs layout analysis, extraction, validation, cleaning,
structure-aware chunking, and artifact generation.

## 3. Build embeddings and ChromaDB index

``` powershell
python -m multimodal_rag.cli.build_index
```

Document chunks are embedded with Gemini Embedding 2 and stored in the ChromaDB
index.

## 4. Ask one RAG question

``` powershell
python -m multimodal_rag.cli.ask "What are the key elements of robust AI governance?"
```

The CLI prints the retrieved evidence and then the Gemini-generated
answer.

# FastAPI service for agents

The API is the integration boundary for the CMO platform. Manager,
market-trend, competitor, strategy, meeting-preparation, and briefing
agents should call these endpoints rather than importing RAG modules.
The API reuses the existing ingestion, embedding, ChromaDB, retrieval,
reranking, prompt, citation, and generation components.

## User-isolated corpora

Each request is restricted to the index and ingestion metadata belonging
to its `user_id`:

``` text
runtime-data/users/<user_id>/artifacts/
├── ingestion/    # existing per-document chunks and metadata
└── index/        # ChromaDB persistent collection and manifest
```

Create a user's artifacts with the existing commands, passing scoped
output directories:

``` powershell
$userId = "alice"
$artifacts = "runtime-data/users/$userId/artifacts"

python -m multimodal_rag.cli.ingest "C:\documents\marketing-report.pdf" `
  --output-dir "$artifacts/ingestion"
python -m multimodal_rag.cli.build_index `
  --output-dir "$artifacts/ingestion" `
  --index-dir "$artifacts/index"
```

Set `RAG_USER_DATA_ROOT` to place these user directories elsewhere; it
defaults to `runtime-data/users`. `RAG_API_LOG_LEVEL` controls API log level and
defaults to `INFO`.

For user sign-up, sign-in, and saved conversations, configure PostgreSQL:

```text
RAG_DATABASE_URL=postgresql://user:password@host:5432/cmo_intelligence
```

`RAG_MEMORY_DATABASE_URL` remains accepted as a compatibility fallback. On
startup the API creates the account, session, chat, and memory tables. New
passwords are stored only as salted scrypt hashes; sessions use random bearer
tokens whose hashes are stored server-side. The frontend then offers **Create
account** and **Sign in**, and restores only the signed-in user's past chats.

Without PostgreSQL, the legacy single-account mode remains available: set
`RAG_API_AUTH_TOKEN`, `RAG_API_USERNAME`, and `RAG_API_PASSWORD`. Login returns
the configured bearer token; every `/retrieve` and `/answer` request must include:

```text
Authorization: Bearer <RAG_API_AUTH_TOKEN>
```

The web-search client reads `TAVILY_API_KEY` when a Tavily search is requested.
Market Intelligence also validates every external result before it becomes
agent evidence. Configure the following secrets outside source control:

```text
WEB_SEARCH_SECURITY_ENABLED=true
VIRUSTOTAL_API_KEY=...
VIRUSTOTAL_MAX_MALICIOUS=0             # optional
VIRUSTOTAL_MAX_SUSPICIOUS=1            # optional
LAKERA_GUARD_API_KEY=...
LAKERA_PROJECT_ID=...                 # optional, when your Guardrails project requires it
WEB_SEARCH_SECURITY_TIMEOUT_SECONDS=10 # optional
```

The Source Guard fails closed: malformed URLs, URLhaus malicious-URL matches, Check
Point AI Guardrails detections, missing security credentials, and invalid or
unavailable security responses are excluded from Market Intelligence input.

## Chat-scoped PPT generation

Completed Meeting Preparation conversations can be exported as editable PPTX
files through Presenton. The frontend first loads available templates from
`GET /presentations/templates`, then sends only the authenticated `chat_id`,
selected `template_id`, and optional `slide_count` to
`POST /presentations/generate`. The backend reconstructs the source context
from the user-owned PostgreSQL chat: the latest completed Meeting Preparation
payload plus eligible Market Strategy replies that follow it. Browser-supplied
assistant content is never accepted.

PPT generation does not call RAG, Market Intelligence, Market Strategy,
Meeting Preparation, the Knowledge Graph, or web search. Presenton receives a
deterministic Markdown transformation of the selected conversation payloads
with `web_search=false` and instructions to preserve the supplied business
facts. The API validates and downloads the generated PPTX from the configured
Presenton origin, then streams it to the browser so Presenton credentials and
untrusted provider URLs remain server-side.

The CMO API requires these server-side variables:

```text
PRESENTON_BASE_URL=http://127.0.0.1:5001
PRESENTON_API_KEY=sk-presenton-...
PRESENTON_TIMEOUT_SECONDS=120
```

For a local deployment, Presenton can be started with Docker. See
[`docs/presenton.md`](docs/presenton.md) for the full deployment and runtime
contract:

``` powershell
docker run -it --name presenton -p 5001:80 `
  -e LLM=google `
  -e GOOGLE_API_KEY=your-gemini-key `
  -e GOOGLE_MODEL=models/gemini-2.0-flash `
  -e WEB_GROUNDING=false `
  -e CAN_CHANGE_KEYS=false `
  -v "${PWD}\presenton-data:/app_data" `
  ghcr.io/presenton/presenton:latest
```

Keep `GOOGLE_API_KEY` inside the Presenton deployment and never put it in
frontend configuration. Presenton template IDs are loaded dynamically, so
custom company templates can be added to Presenton later without changing the
CMO API contract.

Invalid credentials or missing tokens return `401`. The API returns `503` if
the authentication settings are not configured. The internal scope model also accepts an optional
`project_id`, reserving a `projects/<project_id>` partition for a future
API scope for the current endpoints.

## Run the service

``` powershell
python run_backend.py
```

The startup script asks for the bearer token without echoing it. The
username and password continue to come from the project `.env` file.

`POST /retrieve` performs user-scoped retrieval only:

``` powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/retrieve `
  -ContentType "application/json" `
  -Body '{"question":"What are the key market trends?","user_id":"alice","top_k":8}'
```

The same request with `curl`:

``` bash
curl -X POST http://127.0.0.1:8000/retrieve \
  -H "Content-Type: application/json" \
  -d '{"question":"What are the key market trends?","user_id":"alice","top_k":8}'
```

`POST /answer` performs the same retrieval followed by the existing
grounded Gemini generation path. It requires `GEMINI_API_KEY`:

``` powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/answer `
  -ContentType "application/json" `
  -Body '{"question":"What are the key market trends?","user_id":"alice","top_k":8}'
```

The same request with `curl`:

``` bash
curl -X POST http://127.0.0.1:8000/answer \
  -H "Content-Type: application/json" \
  -d '{"question":"What are the key market trends?","user_id":"alice","top_k":8}'
```

`/retrieve` returns chunks with source, document, first page, raw score,
and full preserved metadata. Both endpoints return the request trace ID in
the `X-Trace-ID` response header; `/answer` also includes it as `trace_id`
in the JSON body. `/answer` additionally returns the answer and source
records. Unknown user indexes return `404`; unavailable generation
credentials return `503`; invalid payloads are rejected with `422`.

## Market Trend Agent

`POST /agents/market-trends` runs document-only market trend analysis. It
extracts signals, groups related signals, and returns a trend only when the
evidence supports it across at least two distinct documents. It preserves
chunk IDs, source metadata, scores, task IDs, and trace IDs for downstream
strategy and briefing agents.

``` powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/agents/market-trends `
  -Headers @{ Authorization = "Bearer <token>" } `
  -ContentType "application/json" `
  -Body '{"objective":"Identify demand trends for measurable marketing outcomes","user_id":"alice","industry":"B2B software","geography":"North America","time_range":"2025-2026","top_k":8}'
```

The frontend exposes this through the `Market Trend Agent` mode selector.
It keeps the existing `RAG Answer` mode available.

## React frontend

A standalone React/Vite client is available in `apps/frontend/`. It sends
questions to `/answer`, displays the generated answer, shows source cards,
and keeps the retrieved evidence available behind an expandable panel.

Start the API first, then run the frontend in a second terminal:

``` powershell
python -m uvicorn multimodal_rag.api.main:app --host 127.0.0.1 --port 8000
Set-Location apps/frontend
Copy-Item .env.example .env
npm install
npm run dev
```

Open <http://localhost:5173>. Enter the same `user_id` used when creating
the user's scoped ingestion and ChromaDB artifacts. The default API URL is
`http://127.0.0.1:8000`; change `VITE_API_URL` in `apps/frontend/.env` when the
API runs elsewhere. FastAPI enables the local Vite origins by default;
additional browser origins can be configured with
`RAG_API_CORS_ORIGINS`, as a comma-separated list.

# Evaluation

The project separates **retrieval evaluation** from **generated-answer evaluation**:

-   **Retrieval benchmark:** evaluates whether the retriever finds and ranks the correct evidence using Recall@k, Precision@k, MRR, nDCG@k, and latency.
-   **RAGAS:** evaluates the generated answer and retrieved context using Faithfulness, Answer Relevancy, Context Precision, Context Recall, and Answer Correctness.

The repository supports both **one-question RAGAS evaluation** for debugging and **batch RAGAS evaluation** for benchmarking.

## Evaluate one ground-truth question

By ID:

``` powershell
python -m multimodal_rag.evaluation.question_runner --id 1
```

By exact question:

``` powershell
python -m multimodal_rag.evaluation.question_runner --question "What are the five dimensions of AI readiness?"
```

Interactive selection:

``` powershell
python -m multimodal_rag.evaluation.question_runner --interactive
```

The question-wise evaluator runs one top-8 RAG trace and one RAGAS
evaluation, then reports the generated answer, retrieved chunks, scores,
latency, model information, and five RAGAS metrics.

## Run the batch evaluator

``` powershell
python -m multimodal_rag.evaluation.runner
```

Batch evaluation uses the ground-truth dataset in:

``` text
evaluation/datasets/ground_truth.json
```

and maintains provider-specific evaluation outputs under
`runtime-data/evaluation/`.

## Evaluate the CMO PDF corpus

The source-linked 30-question CMO dataset is at
`evaluation/datasets/cmo_intelligence_ground_truth.json`. Run its evaluation
with the production retrieval and answer pipeline, plus Gemini answer-quality
judging:

``` powershell
python -m multimodal_rag.evaluation.cmo_metrics
```

Use `--resume` after a rate-limit or interrupted run; completed questions are
preserved and only unfinished questions are retried. Outputs are written to
`evaluation/results/cmo_intelligence_metrics/`. Context precision and recall
use manually verified exact chunk labels; faithfulness, relevancy, and
correctness are 0-to-1 Gemini judge scores.

To compare the local BM25 keyword-only baseline with production hybrid
retrieval, run:

``` powershell
python -m multimodal_rag.evaluation.cmo_retrieval_comparison
```

It writes exact-label context precision, recall, MRR, and nDCG to
`evaluation/results/cmo_retrieval_comparison.json`.

# Launch the frontend

The maintained frontend is the React/Vite client under `apps/frontend/`:

``` powershell
cd apps/frontend
npm install
npm run dev
```

Use `npm run build` for a production bundle and `npm run preview` to serve it
locally. The client provides the document Q&A, market-trend, web-search, and
ETL workspaces through the FastAPI service.

# Tech stack

  ----------------------------------------------------------------------------------
  Layer                   Technology                         Role
  ----------------------- ---------------------------------- -----------------------
  Language                Python 3.10+                       Core application

  PDF / layout            Docling + PyMuPDF                  Document structure,
                                                             rendering, and
                                                             extraction

  OCR                     RapidOCR                           Local OCR fallback

  Vision                  Gemini Vision                      Complex visual-region
                                                             understanding when
                                                             needed

  Chunking                Structure-aware aggregation +      Context-preserving
                          `RecursiveCharacterTextSplitter`   chunks

  Embeddings              `gemini-embedding-2`               Hosted 768d dense
                                                             embeddings

  Vector index            ChromaDB                           Dense similarity
                                                             retrieval

  Sparse retrieval        BM25                               Lexical retrieval

  Fusion                  Reciprocal Rank Fusion (RRF)        Dense + sparse rank fusion

  Optional reranking      `BAAI/bge-reranker-base`            Cross-Encoder query–chunk
                                                             reranking

  Generation              `gemini-3.1-flash-lite`            Grounded answer
                                                             generation

  Evaluation              RAGAS                              Five-metric RAG
                                                             evaluation

  Evaluators              Ollama / Groq                      Local or external
                                                             evaluation models

  UI                      React + Vite                       Browser client
  ----------------------------------------------------------------------------------

# Project structure

``` text
CMO Intelligence Platform/
├── apps/frontend/          # React/Vite client
├── services/api/           # FastAPI service and launcher
├── packages/agents/        # market-trend and agent contracts
├── packages/web-search/    # Tavily search and research clients
├── packages/rag-core/      # retrieval, generation, indexing, CLI, evaluation
├── packages/ingestion/     # document, media, and extraction pipeline
├── packages/security/      # upload malware scanning
├── runtime-data/           # application artifacts and logs (local only)
├── Data/                   # user-provided source material
├── evaluation/             # datasets
├── docs/
├── tests/
├── requirements.txt
└── pyproject.toml
```

# Testing

Run the complete automated suite with:

``` powershell
python -m pytest
```

The latest verified working-tree baseline passed **68 tests**. Provider
interactions are mocked during automated tests, so normal test execution
does not require live Gemini, Groq, Ollama, or RAGAS calls.

# Processing boundaries

Most document processing stays local:

  Stage                               Processing
  ----------------------------------- ------------------------------
  PDF loading and layout inspection   Local
  Docling analysis                    Local
  RapidOCR                            Local
  Cleaning and chunking               Local
  Gemini Embedding 2 embeddings      Hosted API
  ChromaDB dense retrieval            Local
  BM25 sparse retrieval                Local
  RRF fusion                           Local
  BGE Cross-Encoder reranking          Local, optional
  Gemini Vision                       External, only when required
  Gemini answer generation            External
  Ollama evaluation                   Local
  Groq evaluation                     External, optional

# Deeper documentation

The GitHub README intentionally stays focused on the system and how to
run it.

For implementation-level details:

-   [`docs/ARCHITECTURE_REFERENCE.md`](docs/ARCHITECTURE_REFERENCE.md)
    --- detailed pipeline architecture, routing, trace boundaries,
    evaluator behavior, and artifacts.
-   [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md) --- current
    repository state and handoff context.
-   [`AGENTS.md`](AGENTS.md) --- concise context for coding agents
    working on the repository.

# Future improvements

The architecture is designed to support further experimentation with:

-   adaptive retrieval and structural ranking
-   table- and figure-aware retrieval
-   visual embeddings
-   GraphRAG for multi-hop or corpus-global questions
-   persistent conversation and evaluation history
-   richer document management and evidence navigation

# License

This project is licensed under the [MIT License](LICENSE).

## LangSmith observability

Backend tracing is optional and disabled by default. To enable it, configure the API process with:

LANGSMITH_TRACING=true
LANGSMITH_API_KEY=<server-side-key>
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
RAG_ENVIRONMENT=development
LANGSMITH_PROJECT=cmo-intelligence-development
LANGSMITH_TRACING_SAMPLING_RATE=1.0

If LANGSMITH_PROJECT is omitted, the API uses cmo-intelligence-{RAG_ENVIRONMENT}. Keep separate projects for development, staging, and production. LangSmith trace roots can be correlated with the existing X-Trace-ID response header and latency report.

The current policy records full prompts, generated answers, chat context, document evidence, retrieved chunks, search results, and agent outputs. API keys, authorization headers, passwords, session tokens, database URLs, and framework transport objects are omitted automatically. Because full-content tracing may contain customer or source-document data, enable it only in a LangSmith workspace with appropriate access and retention controls. LangSmith keys must never be exposed to the frontend.
