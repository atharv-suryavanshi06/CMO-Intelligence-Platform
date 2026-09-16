# Project Status

Last verified: 2026-08-01 (Asia/Calcutta)

## Current status

- **Readiness:** Demo-hardened. The maintained React/Vite frontend, CLI modules, offline retrieval path, question-wise evaluator, and batch compatibility are verified.
- **Canonical environment:** `.venv` with Python 3.11.9; supported project Python is 3.10+.
- **Current test baseline:** Maintained offline tests pass after frontend consolidation.
- **Known-good commit:** `bbedb9d53e835971ccc5e4d165866b23672a5834` — `Harden RAG evaluation, improve telemetry, clean project structure, and fix CLI behavior`.
- **Remote state before this documentation pass:** local `main` matched `origin/main` at the known-good commit.
- **Working tree:** clean at the known-good commit before this pass; the current pass intentionally introduces uncommitted documentation changes (`README.md`, `AGENTS.md`, and files under `docs/`). No application or runtime file is part of this change set.
- **Embedding migration:** core RAG embeddings now use hosted Gemini Embedding 2 (`gemini-embedding-2`) with 768-dimensional vectors by default. Existing indexes must be rebuilt.

## Canonical commands

```powershell
# Ingest one PDF
.\.venv\Scripts\python.exe -m multimodal_rag.cli.ingest path\to\document.pdf

# Embed new chunks and rebuild the index
.\.venv\Scripts\python.exe -m multimodal_rag.cli.build_index

# Ask one normal question; CLI default top-k is 8
.\.venv\Scripts\python.exe -m multimodal_rag.cli.ask "What does Enterprise AI mean?"

# Evaluate exactly one ground-truth record without saving
.\.venv\Scripts\python.exe -m multimodal_rag.evaluation.question_runner --id 1

# Run/resume the provider-specific batch evaluation
.\.venv\Scripts\python.exe -m multimodal_rag.evaluation.runner

# Launch the maintained React/Vite frontend
cd apps/frontend
npm install
npm run dev

# Run the full deterministic test suite
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

## Recent completed work

- Migrated maintained application code into the component source roots under `packages/`, `services/`, and `apps/`, while preserving the `multimodal_rag.*` namespace.
- Removed obsolete root-level Python compatibility wrappers.
- Added canonical package CLIs for ingestion, indexing, and asking questions.
- Added a separate print-only question evaluator with exact ID/question selection and no batch-state access.
- Added `RAGTrace` and `RetrievedItemTrace` in `rag/trace.py` for question-evaluation execution.
- Replaced the core local MiniLM embedding path with Gemini Embedding 2 and preserved normalized FAISS inner-product retrieval.
- Exposed raw FAISS and combined lexical-rerank scores without changing ranking behavior.
- Captured Gemini prompt/completion/total tokens from existing response metadata without an extra request.
- Added Groq metric-specific direct model clones: Faithfulness `4096`, Answer Correctness `4096`.
- Set Groq Answer Relevancy to `strictness=1` to respect Groq's single-completion API constraint.
- Preserved Ollama metric behavior and provider selection.
- Removed inactive `retriever.py`, `legacy_app.py`, old `venv`, stale wrappers, and repository caches while preserving `.venv`, `comparison_env`, and runtime data.
- Fixed CLI `--top-k` so the default remains 8 and explicit values reach the retriever without extra calls.
- Completed a pre-demo live rehearsal, protected-artifact comparison, and offline suite.

## Latest verified live result

The latest controlled rehearsal evaluated ground-truth ID `1`:

> What does 'Enterprise AI' mean according to the playbook?

Execution and models:

- Exactly one RAG execution, one retrieval, one Gemini generation, and one one-record RAGAS invocation.
- Retrieved chunks: **8**.
- Embedding: Gemini Embedding 2 (`gemini-embedding-2`), loaded through the Gemini API.
- Generation: Gemini `gemini-3.1-flash-lite`.
- Evaluator: Groq `openai/gpt-oss-20b`.
- Faithfulness and Answer Correctness both used their `max_tokens=4096` direct clones.

| Metric | Score |
|---|---:|
| Faithfulness | 1.000 |
| Answer Relevancy | 0.794 |
| Context Precision | 0.367 |
| Context Recall | 1.000 |
| Answer Correctness | 0.407 |
| Composite | 0.713 |

| Telemetry | Value |
|---|---:|
| Retrieval | 13.97 s |
| Generation | 6.34 s |
| Complete RAG | 20.30 s |
| Evaluation | 183.14 s |
| Total | 203.46 s |
| Evaluator prompt tokens | 16,896 |
| Evaluator completion tokens | 12,377 |
| Evaluator total tokens | 29,273 |
| Estimated evaluator cost | $0.0049803 |

Groq returned temporary HTTP 429 responses during the evaluator phase. The configured Groq SDK and RAGAS retry/backoff behavior recovered, and all five metrics completed.

This was print/display-only. A 967-file protected manifest comparison found zero missing, added, hash-changed, size-changed, or timestamp-changed files. Batch CSVs/reports, ground truth, FAISS, and the ID map remained untouched.

## Latest offline verification

- Focused tests: 15 passed during the pre-demo rehearsal.
- Full suite: 47 passed.
- Import smoke: 38 maintained modules passed.
- Maintained CLI help checks: 7 passed.
- React/Vite production build: passed.
- Repository `__pycache__`: none after cleanup.
- Root-level Python wrappers: none.
- Protected artifacts: unchanged.

## Known issues

### Retrieval quality

- Context Recall was 1.000 for the verified ID 1 run while Context Precision was approximately 0.367. Required evidence was present, but top-8 also included less relevant chunks.
- Production reranking is a lightweight lexical overlap over the same dense top-k candidate set. It is not hybrid retrieval or a cross-encoder.
- No full judged benchmark currently reports Precision@k, Recall@k, MRR, or nDCG.
- Ground-truth `source_pages` annotations have known inconsistencies relative to extracted/indexed page metadata and should be audited before becoming relevance judgments.

### Answer and evaluation quality

- ID 1 Answer Correctness was 0.407. The generated answer was grounded but expanded beyond the deliberately narrow reference with additional Enterprise AI characteristics, which the correctness judge penalized.
- Groq rate limits can add long retry delays even when evaluation eventually succeeds.
- Evaluator tokens/cost are aggregate for the one `evaluate()` invocation; clean per-metric token accounting is not available.
- Generation cost is unavailable because only Gemini token metadata is captured; the project does not apply a Gemini pricing table.

### Performance

- Cold `sentence_transformers`/Transformers imports and first model construction dominate a fresh CLI process.
- Evaluation is intentionally much slower than Chat because RAGAS performs several external/local-LLM judge generations and may back off on rate limits.

### Dependencies and environments

- `requirements.txt` does not yet declare the complete RAGAS evaluator stack.
- `.venv` is functional for the verified project but `pip check` reports LangChain-family version conflicts and a `pdftext`/`pypdfium2` conflict.
- `comparison_env` is preserved for optional Marker/Unstructured experiments and is not used by the application. Its current `pip check` reports Pillow and `pypdfium2` conflicts with Marker/Surya/pdftext.
- Comments in the evaluator module mention a `langchain-ollama` version different from the actually verified `.venv` version (`0.2.3`); dependency documentation should be reconciled during a deliberate packaging pass.

### Ingestion and scale

- The orchestrator still has a temporary hard-coded whole-page Gemini POC for PDF pages 21, 28, 32, and 34.
- PDF reliability varies with scan quality, broken font mappings, complex diagrams, and provider availability.
- `IndexFlatIP` is appropriate for the current small corpus but performs exhaustive search and has not been benchmarked at large multi-document scale.

## Next recommended work

1. Create judged chunk-level relevance labels across all 25 questions and establish Precision@k, Recall@k, MRR, and nDCG before changing retrieval.
2. A/B test candidate-pool size, lexical weight, hybrid retrieval, and a cross-encoder reranker against that benchmark.
3. Audit `source_pages` annotations and define how human PDF labels map to extracted page numbers.
4. Replace the hard-coded page-level Vision POC with an explicit routing-policy signal.
5. Create a conflict-free dependency lock or optional `evaluation` extra; then reconcile `.venv` and evaluator comments.
6. Clean or rebuild `comparison_env` only if the extraction-comparison tools remain valuable.
7. Add FastAPI and a React client after core retrieval/evaluation interfaces are stable.
8. Add deployment, authentication, and production observability after the service boundary exists.

## LLM Handoff

For a new coding or LLM session, read in this order:

1. `AGENTS.md`
2. `docs/PROJECT_STATUS.md`
3. `docs/ARCHITECTURE_REFERENCE.md`
4. `README.md`
5. Inspect only the source modules relevant to the requested task

These documents are context accelerators, not a replacement for source-code verification before making changes. Re-check affected constants, call sites, tests, Git state, and runtime-artifact safety before implementation.
