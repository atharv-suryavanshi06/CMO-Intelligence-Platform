This project is a modular Python-based multimodal RAG application. Its main flow is:

`documents → ingestion → chunks/embeddings/index → API → retrieval → generated answer`

## `packages/`

This contains the reusable application logic, split into focused Python components. All packages contribute to the shared `multimodal_rag` namespace.

### `packages/rag-core/`

The central RAG engine.

Important areas:

- `rag/embedding/` — creates document and question embeddings.
- `rag/indexing/` — builds and loads the FAISS vector index.
- `rag/retrieval/` — searches documents using dense/sparse retrieval and ranking.
- `rag/generation/` — builds prompts, calls the language model, and creates citations.
- `rag/trace.py` — records retrieval results, scores, latency, tokens, and answer diagnostics.
- `cli/` — command-line commands such as asking questions, ingesting files, and building indexes.
- `evaluation/` — RAG quality and retrieval benchmark tools.
- `tools/` — comparison and diagnostic utilities.
- `paths.py` — defines canonical project paths such as `runtime-data/artifacts`.

### `packages/ingestion/`

Converts source documents into searchable content.

Important areas:

- `loaders/` — loads PDFs and other document types.
- `extractors/` — extracts text using OCR or vision models.
- `presentation/` — extracts PowerPoint content.
- `word/` — extracts Word document content.
- `analysis/` — analyzes page layouts and document structure.
- `processing/` — cleans, validates, and splits content into chunks.
- `routing/` — decides which extraction method should handle a document or page.
- `media/` — handles audio transcription, such as Deepgram.
- `output/` — writes extracted text, metadata, images, and ingestion results.
- `pipeline/` — orchestrates the complete ingestion process.

### `packages/agents/`

Contains higher-level business agents that use the RAG system.

- `market_intelligence.py` — market-trend analysis agent.
- `rag_client.py` — interface used by agents to call the RAG service.
- `models.py` — agent request/response data models.

### `packages/web-search/`

Adds external web research capabilities.

- `client.py` — general research client.
- `research.py` — research workflow.
- `providers/tavily.py` — Tavily search provider integration.
- `models.py` — search and research data structures.

### `packages/security/`

Security-related integrations.

- `clamav.py` — scans uploaded files for malware using ClamAV.

---

## `runtime-data/`

This contains generated and user-specific runtime data. It is not application source code and should generally not be committed or manually edited.

### `runtime-data/input/`

Temporary or shared input files used by ingestion workflows.

### `runtime-data/artifacts/`

The main generated-data directory.

Important subfolders:

- `ingestion/` — extracted documents, chunks, metadata, and processed document artifacts.
- `index/` — FAISS vector indexes and ID mappings used for retrieval.
- `figures/` — extracted images, charts, and other visual assets.

### `runtime-data/users/`

Stores data separately for each user or tenant.

For example, `users/demo-user/` contains:

- `uploads/` — files uploaded by that user.
- `artifacts/` — that user’s processed documents and indexes.
- `reingest_logs/` — logs related to reprocessing their files.

This separation allows different users or projects to have independent document collections.

### `runtime-data/logs/`

Application and ingestion logs.

In short:

> `runtime-data` is where the running application stores uploaded files, indexes, extracted content, images, logs, and user-specific state.

---

## `services/`

This contains deployable application services rather than reusable core logic.

### `services/api/`

The FastAPI backend service.

Important files:

- `run_backend.py` — starts the API server with Uvicorn and prepares Python import paths.
- `src/multimodal_rag/api/main.py` — creates the FastAPI application.
- `api/router.py` — defines HTTP endpoints.
- `api/service.py` — connects API requests to retrieval, RAG generation, indexes, and traces.
- `api/ingestion.py` — manages background ingestion jobs.
- `api/config.py` — loads environment configuration and user/project storage scopes.
- `api/schemas.py` — defines request and response models.

The API is the bridge between the frontend or external clients and the RAG engine.

Typical request flow:

`HTTP request → router → API service → RAG core → runtime-data → response`

---

## `src/`

`src/` is mostly a compatibility/import bridge, not the primary location of the application logic.

### `src/multimodal_rag/`

Its `__init__.py` extends the Python package path so code launched directly from the repository can import modules located in:

- `packages/rag-core/`
- `packages/ingestion/`
- `packages/agents/`
- `packages/web-search/`
- `packages/security/`
- `services/api/`

So, for this project:

> The real source code is in `packages/` and `services/api/`; `src/` helps Python find that code.

---

## `tests/`

This contains automated tests run by Pytest. The project configuration sets `tests/` as the test directory.

Important test groups include:

- `test_api.py` — API behavior and endpoints.
- `test_ingestion_jobs.py` — background ingestion jobs.
- `test_pdf_loader.py` — PDF loading.
- `test_presentation_ingestion.py` — PowerPoint processing.
- `test_word_ingestion.py` — Word document processing.
- `test_semantic_chunking.py` — document chunking.
- `test_offline_embeddings.py` — embedding behavior without external services.
- `test_rag_trace.py` — retrieval and generation trace data.
- `test_cli_ask.py` — command-line question answering.
- `test_cmo_metrics.py` — evaluation metrics.
- `test_clamav.py` — file-security scanning.
- `test_web_search_client.py` and `test_company_research.py` — web research functionality.
- `test_market_intelligence_agent.py` — market intelligence agent behavior.

The `__pycache__/` folders inside various directories are automatically generated Python bytecode caches and can be ignored.

## Simple distinction

| Folder | Purpose |
|---|---|
| `packages/` | Reusable application logic |
| `runtime-data/` | Generated, uploaded, indexed, and logged runtime data |
| `services/` | Runnable services, currently the FastAPI backend |
| `src/` | Import compatibility layer |
| `tests/` | Automated verification of the system |