# CMO Intelligence Platform

The CMO Intelligence Platform is an AI workspace for marketing leaders. It turns a company’s own documents, safe live web research, and meeting context into clear, evidence-backed marketing intelligence.

In simple words: instead of searching through reports, competitor pages, market research, and old meeting notes one by one, a Chief Marketing Officer (CMO) can ask a business question and receive a structured answer with the evidence behind it.

> The platform is designed to help with judgement and preparation. It does not replace the CMO’s decision-making. Answers should be reviewed alongside the original cited sources.

## Contents

- [What problem it solves](#what-problem-it-solves)
- [Who it is for](#who-it-is-for)
- [What the platform can do](#what-the-platform-can-do)
- [The agents](#the-agents)
- [How the platform works](#how-the-platform-works)
- [How RAG is used](#how-rag-is-used)
- [Technology stack](#technology-stack)
- [Security, safety, and privacy](#security-safety-and-privacy)
- [Project layout](#project-layout)
- [Local setup](#local-setup)
- [Configuration](#configuration)
- [Running the application](#running-the-application)
- [Useful API capabilities](#useful-api-capabilities)
- [Testing and evaluation](#testing-and-evaluation)
- [Important limitations](#important-limitations)

## What problem it solves

CMOs often need to make decisions quickly, while their information is spread across market reports, sales decks, customer documents, competitive research, meeting notes, and the web. This creates three common problems:

1. Finding the right information takes too long.
2. It is hard to tell whether a conclusion is supported by evidence.
3. Turning research into a strategy, a meeting brief, or an executive presentation requires repeated manual work.

This platform brings those steps into one place. It can search a user’s approved document collection, add fresh web research where needed, show sources, produce a marketing strategy, prepare a meeting brief, and create an editable presentation from the completed conversation.

## Who it is for

The primary user is a **Chief Marketing Officer** or senior marketing leader. It is also useful for:

- Marketing strategy and market-intelligence teams
- Product marketing and competitive-intelligence teams
- Account or revenue leaders preparing for important meetings
- Executives who need a concise, source-backed view of a market or competitor

### How it helps CMOs

| CMO need | How the platform helps |
| --- | --- |
| Understand a market quickly | Combines internal documents with current web research and summarizes trends, competitors, opportunities, and risks. |
| Avoid unsupported claims | Keeps source information with the analysis and separates direct facts from reasonable inference. |
| Build a marketing response | Converts approved intelligence and stored business context into an actionable strategy. |
| Prepare for a high-stakes meeting | Produces key talking points, questions to ask, risks, and next actions. |
| Communicate the story | Exports a completed meeting-preparation conversation as an editable PowerPoint deck. |
| Keep work organized | Supports signed-in users, saved chats, and user-scoped document collections. |

## What the platform can do

- Upload and process business documents, including PDFs, Word documents, PowerPoint files, and supported media.
- Read document text, tables, layouts, scanned pages, and relevant diagrams.
- Answer questions from the user’s own document collection using citations.
- Research markets and competitors using the Tavily web-search service.
- Check external web results before they are passed to an AI model.
- Generate market intelligence, market strategies, and meeting-preparation briefs.
- Store user and business context to make later strategy work more relevant.
- Save conversations and reuse relevant earlier exchanges instead of needlessly repeating work.
- Generate an editable `.pptx` presentation from trusted chat content through Presenton.
- Give developers retrieval traces, latency and token information, plus quality-evaluation tools.

## The agents

An *agent* here is a focused AI workflow. It does not freely browse or make decisions on its own; the Python application controls which evidence it receives, the structure it must return, and the checks performed before a result is shown.

| Agent | Simple description | Main output |
| --- | --- | --- |
| **Market Intelligence Agent** | The researcher. It looks for market trends, competitor moves, opportunities, and risks using the user’s indexed documents and, when useful, recent web research. | Executive summary, trends, competitor intelligence, opportunities, risks, sources, and limitations. |
| **Market Strategy Agent** | The advisor. It turns market intelligence and known business context into a practical marketing strategy. | Strategic recommendations, priorities, assumptions, and source-backed rationale. |
| **Meeting Preparation Agent** | The meeting coach. It combines intelligence and strategy into a briefing for a specific meeting. | Executive brief, talking points, questions to ask, risks to watch, and recommended actions. |
| **Meeting Follow-up workflow** | A continuation of the meeting-preparation experience. It decides whether a new message should revise the full briefing or expand the strategy around it. | An updated briefing or a deeper strategic response. |

There are also supporting services rather than standalone user-facing agents:

- **RAG service:** finds relevant passages in the user’s own documents and creates grounded answers.
- **Company research service:** produces a citation-backed research report for up to ten companies.
- **Memory service:** extracts and stores useful user and business context for later strategy conversations.
- **Presentation service:** turns a completed meeting conversation into a presentation; it does not trust presentation text sent directly by the browser.

## How the platform works

There are two main knowledge sources:

1. **Private, indexed documents** supplied for a user or project.
2. **Fresh public-web research** retrieved through Tavily and allowed into the system only after security checks.

### Overall flow

```mermaid
flowchart TD
    A[CMO signs in and uploads documents or asks a question] --> B{What does the request need?}
    B -->|Document question| C[Document ingestion and RAG]
    B -->|Market or competitor question| D[Market Intelligence Agent]
    B -->|Strategy request| E[Market Strategy Agent]
    B -->|Meeting request| F[Meeting Preparation Agent]

    C --> G[Evidence-backed answer with citations]
    D --> H[Search the user's RAG index]
    D --> I[Plan fresh Tavily web searches]
    I --> J[VirusTotal URL check and Lakera content check]
    H --> K[Validated evidence set]
    J --> K
    K --> L[Gemini analysis with structured output]
    L --> M[Market trends, competitors, opportunities, risks]
    M --> E
    E --> N[Marketing strategy]
    M --> F
    N --> F
    F --> O[Meeting brief]
    O --> P[Optional Presenton editable PPTX]
```

### 1. Document ingestion flow

When a user uploads a document, the backend processes it before it becomes searchable.

```mermaid
flowchart LR
    A[Uploaded file] --> B[ClamAV malware scan]
    B --> C[Load and inspect document]
    C --> D[Extract text, layout, and tables]
    D --> E{Is extra recognition needed?}
    E -->|Scanned text| F[RapidOCR]
    E -->|Meaningful chart or diagram| G[Gemini Vision description]
    E -->|No| H[Clean and validate content]
    F --> H
    G --> H
    H --> I[Structure-aware chunks]
    I --> J[Gemini Embedding 2 vectors]
    J --> K[ChromaDB index and metadata]
```

The pipeline preserves useful information such as document name, page number, section, chunk ID, extraction method, and validation details. That information makes citations and later troubleshooting possible.

### 2. Question-and-answer flow

For a document question, the platform does the following:

1. Confirms the signed-in user and finds only that user’s scoped index.
2. Converts the question into an embedding (a numeric representation of meaning).
3. Retrieves relevant passages using both semantic and keyword search.
4. Combines and ranks the results.
5. Gives the selected passages to Gemini with instructions to stay grounded in those passages.
6. Returns the answer with source and retrieval-trace information.

### 3. Market-intelligence flow

For a market or competitor request, the Market Intelligence Agent:

1. Works out the requested scope, such as industry, geography, and time range. Reasonable defaults are used for broad requests.
2. Builds a small research plan and up to four web-search queries.
3. Searches the user’s private RAG collection.
4. Searches the web through Tavily when fresh research is needed.
5. Deduplicates web results and checks every result with the source-security layer.
6. Combines approved web evidence, private document evidence, approved attached-document text, and relevant prior sources.
7. Asks Gemini to produce structured findings.
8. Validates the structured response in Python before returning it.

The output clearly includes limitations when evidence is unavailable, blocked, old, missing dates, or otherwise insufficient.

### 4. Strategy and meeting flow

The Market Strategy Agent uses the intelligence result plus stored user/business context. It is prompted not to invent business facts such as budget, revenue, target audience, KPIs, or competitors when those are not in the available context.

The Meeting Preparation Agent then combines the market intelligence and strategy into a meeting-ready result. A user can ask a follow-up: the system classifies it as either a request to change the briefing or a request for further strategic detail.

### 5. Presentation flow

Only a completed, authenticated meeting-preparation chat can be exported. The backend reconstructs the approved context from the stored chat, may include eligible later strategy responses, and sends that content to Presenton. Presenton generates an editable PowerPoint file; the backend safely downloads it and makes it available to the signed-in user.

This design prevents a browser client from injecting arbitrary assistant content into a presentation request.

## How RAG is used

**RAG** means *Retrieval-Augmented Generation*. It is a way of making AI answers use specific source material instead of relying only on the model’s general training.

In this project, RAG works like a well-organized research assistant:

1. Documents are split into meaningful chunks instead of being treated as one large file.
2. Every chunk is converted into a Gemini Embedding 2 vector and stored in ChromaDB.
3. For a new question, the system finds passages that are similar in meaning (**dense search**) and passages with matching important words (**BM25 keyword search**).
4. It merges the two rankings using **Reciprocal Rank Fusion (RRF)**. This gives a fair result without pretending that two different score types are directly comparable.
5. An optional local BGE cross-encoder can rerank the candidates more precisely. It is disabled by default because it adds noticeable latency.
6. The final passages are put into a grounded Gemini prompt, and the answer retains citations and trace details.

The normal production retrieval path is:

```text
Question
  -> Gemini Embedding 2 + ChromaDB semantic search
  -> BM25 keyword search
  -> Reciprocal Rank Fusion
  -> optional BGE cross-encoder reranking
  -> selected source passages
  -> Gemini answer
```

This combination is useful because a CMO question may need both meaning-based matching (for example, “time to value” versus “fast implementation”) and exact business terms (for example, a competitor name or product name).

## Technology stack

| Area | Technology | Role in this project |
| --- | --- | --- |
| Web application | React 18, Vite | The maintained browser-based CMO workspace. |
| API backend | Python, FastAPI, Uvicorn, Pydantic | Provides validated HTTP APIs, authentication flow, agent workflows, and typed request/response models. |
| Core LLM | Google Gemini `gemini-3.1-flash-lite` | Generates grounded RAG answers and structured agent analysis. It is used with a low temperature so factual evidence is favored over creativity. |
| Embeddings | Google Gemini `gemini-embedding-2` | Converts document chunks and questions into vectors for semantic retrieval. The default vector size is 768 dimensions. |
| Vision support | Gemini Vision | Describes meaningful diagrams/charts during ingestion when local extraction alone is not enough. |
| Document understanding | Docling, PyMuPDF, pdfplumber | Loads PDFs, identifies layout, extracts native text and tables, and provides fallbacks for difficult documents. |
| OCR | RapidOCR, OpenCV | Reads text from scanned pages or images. |
| Office files | `python-docx`, `python-pptx` | Extracts Word and PowerPoint source material. |
| Media transcription | Deepgram Nova-3 | Transcribes supported audio/video media when configured. |
| Vector database | ChromaDB | Stores embeddings and metadata for each user/project’s document collection. |
| Keyword retrieval | In-project BM25 implementation | Finds exact or important term matches alongside semantic retrieval. |
| Optional reranking | `BAAI/bge-reranker-base` cross-encoder | Can refine the final retrieval order when its model is provisioned locally. |
| Web research | Tavily | Performs live web search and citation-backed company research. |
| URL reputation | VirusTotal | Checks a web result’s URL or domain reputation before it becomes AI evidence. Results with unsafe, unavailable, or rate-limited verdicts are blocked when protection is enabled. |
| Prompt/content guardrail | Lakera Guard | Screens external web content for unsafe or malicious prompt-like content before it reaches an agent’s prompt. |
| File malware protection | ClamAV | Scans uploaded files before ingestion. |
| Observability | LangSmith | Optional trace view for supported API requests and important steps such as agent planning, retrieval, web search, source guarding, and Gemini calls. Secrets and binary data are redacted; a tracing failure does not stop the application. |
| Persistent user data | PostgreSQL with `psycopg` | Stores accounts, sessions, chats, and user memory when `RAG_DATABASE_URL` is configured. |
| Presentation generation | Presenton | Uses trusted completed-chat context to create editable PowerPoint files and previews. Presenton owns its own model credentials. |
| Testing and evaluation | pytest, RAGAS, datasets | Tests individual flows and measures retrieval/answer quality. |

### Which LLM is used?

The main application model is **Google Gemini `gemini-3.1-flash-lite`**. It generates document answers and the structured output used by the agents. **Gemini Embedding 2** is used for semantic search; an embedding model is not a chat model, but it helps the system find relevant source passages.

For optional quality evaluation, the project supports a local **Ollama `qwen2.5:7b`** evaluator by default and an optional **Groq `openai/gpt-oss-20b`** evaluator. These evaluation models are separate from the model that answers user questions.

Presenton is configured separately and its example local deployment uses **Gemini 2.0 Flash** for generating presentations. That credential stays inside the Presenton service, not in the browser.

### What each external service does

| Service | What it does | What it does not do |
| --- | --- | --- |
| Tavily | Finds current web pages and company research sources. | It does not bypass the platform’s security checks. |
| VirusTotal | Checks the reputation of a searched URL/domain. | It does not decide whether a marketing claim is true. |
| Lakera Guard | Checks untrusted web content for unsafe/prompt-injection-like content. | It does not replace source evaluation by a human. |
| LangSmith | Provides optional observability and debugging traces. | It does not generate answers or hold the application’s source of truth. |
| Presenton | Makes editable slide decks from trusted chat content. | It is not given arbitrary browser-supplied content or the CMO API’s secrets. |

## Security, safety, and privacy

The platform has several practical safeguards:

- **User-scoped corpora:** retrieval and document artifacts are stored under a user scope. One user’s RAG index is not used for another user’s request.
- **Authentication:** PostgreSQL-backed accounts and hashed bearer-session tokens are available. A legacy single-account token mode is also available for simpler deployments.
- **Password handling:** new account passwords are stored as salted `scrypt` hashes, not as plain text.
- **Upload scanning:** ClamAV can scan uploaded files before they are ingested.
- **Safe external research:** the Source Guard can fail closed. When enabled, malformed URLs, unsafe or unavailable VirusTotal verdicts, and Lakera-flagged content are excluded from agent evidence.
- **Evidence grounding:** agents receive selected evidence and are required to return structured outputs. The application checks those outputs and shows sources/limitations.
- **Presentation context control:** presentations are generated only from backend-reconstructed, authenticated chat content.
- **Trace redaction:** LangSmith serialization excludes secrets such as API keys, passwords, database URLs, authorization values, tokens, cookies, and binary data.

Security tooling reduces risk; it does not guarantee that every web page or generated conclusion is correct. Users should verify decisions against cited evidence.

## Project layout

```text
apps/frontend/                         React + Vite CMO workspace
services/api/                          FastAPI launcher and API package
packages/agents/                       Intelligence, strategy, meeting, and memory workflows
packages/web-search/                   Tavily integration and external-source guard
packages/rag-core/                     Embeddings, ChromaDB, retrieval, prompts, generation, evaluation
packages/ingestion/                    File loading, layout analysis, OCR, vision, chunking, output
packages/security/                     ClamAV upload scanning
runtime-data/                          Generated runtime artifacts (indexes, ingestion output, etc.)
Data/                                  User-provided source material
docs/                                  Architecture, flow, decisions, and focused guides
tests/                                 Automated tests
```

All Python components share the `multimodal_rag.*` namespace package.

## Local setup

### Prerequisites

- Windows PowerShell (the documented local workflow)
- Python 3.10 or later; the verified development environment uses Python 3.11
- Node.js and npm for the frontend
- A Google Gemini API key for embeddings and answer generation
- PostgreSQL if you want persistent accounts, chats, and memory
- Optional service keys: Tavily, VirusTotal, Lakera Guard, Deepgram, LangSmith, and Presenton

If you plan to use GPU acceleration for Docling/PyTorch, install the correct PyTorch build for your CUDA version first. Otherwise, the dependency installation can use a CPU build.

### Install Python dependencies

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install --no-deps --no-build-isolation -e .
```

### Install the frontend

```powershell
Set-Location apps/frontend
npm install
Set-Location ../..
```

### Configure environment variables

Copy the values you need into a local `.env` file or set them in your shell. Do not commit real secrets.

At minimum, configure Gemini:

```powershell
$env:GEMINI_API_KEY = "your-gemini-key"
# GOOGLE_API_KEY is also accepted by the embedding and generation code.
```

For PostgreSQL-backed accounts, chats, and memory:

```powershell
$env:RAG_DATABASE_URL = "postgresql://user:password@host:5432/cmo_intelligence"
```

Without PostgreSQL, the backend uses its legacy single-account path and asks for a non-empty API token when it starts.

### Ingest documents and build an index

For command-line use, place source documents in an input folder and run the ingestion and index steps:

```powershell
python -m multimodal_rag.cli.ingest "runtime-data/input/my-document.pdf"
python -m multimodal_rag.cli.build_index
```

For a user-scoped collection, use that user’s artifact paths:

```powershell
$userId = "alice"
$artifacts = "runtime-data/users/$userId/artifacts"

python -m multimodal_rag.cli.ingest "C:\documents\marketing-report.pdf" `
  --output-dir "$artifacts/ingestion"
python -m multimodal_rag.cli.build_index `
  --output-dir "$artifacts/ingestion" `
  --index-dir "$artifacts/index"
```

Changing the embedding model or its vector dimension requires documents to be embedded again and the ChromaDB index to be rebuilt.

## Configuration

The following are the most important optional settings. Keep values secret and configure them outside source control.

| Setting | Why it is needed |
| --- | --- |
| `GEMINI_API_KEY` or `GOOGLE_API_KEY` | Required for main Gemini generation and embeddings. |
| `GEMINI_EMBEDDING_DIMENSION` | Embedding vector dimension; defaults to `768`. Rebuild indexes after changing it. |
| `RAG_DATABASE_URL` | Enables PostgreSQL-backed accounts, sessions, chats, and memory. |
| `TAVILY_API_KEY` | Enables fresh web search and company research. |
| `WEB_SEARCH_SECURITY_ENABLED` | Enables/disables the external Source Guard. Use `true` for protected deployments. |
| `VIRUSTOTAL_API_KEY` | Enables URL/domain reputation checks for web-search results. |
| `LAKERA_GUARD_API_KEY` | Enables external-content guardrail checks for web-search results. |
| `LANGSMITH_TRACING` | Set to `true` to enable optional LangSmith traces. |
| `LANGSMITH_API_KEY` | Required when LangSmith tracing is enabled. |
| `PRESENTON_BASE_URL`, `PRESENTON_API_KEY` | Connect the backend to a Presenton presentation service. |
| `DEEPGRAM_API_KEY` | Enables Deepgram transcription for supported media. |

An example Presenton connection is included in [`.env.example`](.env.example). See [docs/presenton.md](docs/presenton.md) for the local Presenton Docker configuration and its security model.

## Running the application

Start the API from the repository root:

```powershell
.\.venv\Scripts\python.exe services/api/run_backend.py
```

The API runs on `http://127.0.0.1:8000` by default.

In a second PowerShell window, start the React frontend:

```powershell
Set-Location apps/frontend
npm run dev
```

Vite normally serves the frontend at `http://127.0.0.1:5173`. To point the frontend at a different backend, set `VITE_API_URL` before starting Vite.

### Quick command-line question

After ingestion and indexing, ask a grounded question directly:

```powershell
python -m multimodal_rag.cli.ask "What are the strongest opportunities described in these reports?"
```

## Useful API capabilities

The FastAPI backend exposes these main capabilities:

| Capability | Route |
| --- | --- |
| Create account / sign in | `/auth/signup`, `/auth/login` |
| Upload and ingest a file | `POST /ingest` |
| Check ingestion status | `GET /ingest/{job_id}` |
| Retrieve matching document chunks | `POST /retrieve` |
| Ask a document-grounded question | `POST /answer` |
| Run market and competitor intelligence | `POST /agents/market-intelligence` |
| Create a market strategy | `POST /agents/market-strategy` |
| Prepare a meeting | `POST /agents/meeting-preparation` |
| Continue a meeting-preparation conversation | `POST /agents/meeting-follow-up` |
| Run company research | `POST /web-search/research` |
| Get presentation templates / generate a deck | `/presentations/*` |

Requests that access user data require the appropriate bearer token. The frontend is the preferred interface for normal users; these routes are the integration boundary for other approved clients.

## Testing and evaluation

Run the automated test suite with the project virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

The test suite covers document ingestion, retrieval, RAG traces, API behavior, agent behavior, web-search security, memory, presentations, and more.

The project also separates two quality questions:

- **Retrieval quality:** did the system find the right source chunks? Metrics include Recall@k, Precision@k, MRR, nDCG@k, and latency.
- **Answer quality:** does the generated answer stay relevant, faithful to evidence, and correct? The evaluation workflow uses RAGAS.

Run the retrieval benchmark with:

```powershell
python -m multimodal_rag.evaluation.retrieval_benchmark
```

Evaluation is a developer workflow; normal CMO chat does not run evaluation for every answer.

## Important limitations

- The quality of an answer depends on the quality, coverage, and freshness of its documents and web sources.
- A source date may be unknown when it was not available in the document or web-result metadata.
- RAG retrieves the best available passages; it cannot guarantee that the collection contains every relevant fact.
- Web research depends on configured services and can be limited by provider availability, source safety checks, or rate limits.
- Security checks deliberately block uncertain external results when the protected configuration is enabled. This can reduce available web evidence during an outage or rate limit.
- Generated strategies and presentations are decision-support materials. They require human review before external use.
- The optional cross-encoder reranker improves some ranking situations but may add substantial latency, so it is disabled by default.

## Documentation

For deeper technical detail, see:

- [Architecture reference](docs/ARCHITECTURE_REFERENCE.md)
- [Execution flow](docs/flow.md)
- [Engineering decisions](docs/decisions.md)
- [Agent prompts guide](docs/agent_prompts_guide.md)
- [Presenton deployment guide](docs/presenton.md)
- [Project status](docs/PROJECT_STATUS.md)

## License

This project is licensed under the [MIT License](LICENSE).
