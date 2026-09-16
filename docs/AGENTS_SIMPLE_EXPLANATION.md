# Agents in This Project — Simple Explanation

This document explains how the agents work in simple words.

It is written for understanding the project, not as a replacement for the source code. The source code is still the final authority.

## 1. What is an agent here?

An agent is a Python class that receives a task, uses the RAG system to find evidence, asks a generation model to analyze that evidence, and returns a structured result.

In the current project, the main implemented agent is the **Market Intelligence Agent**.

Its job is:

1. Receive a market-trend question.
2. Decide the analysis scope.
3. Search the correct user/project RAG index.
4. Preserve the retrieved document evidence.
5. Find individual market signals.
6. Group related signals.
7. Keep only patterns supported by at least two different documents.
8. Separate facts from inferences.
9. Explain why the result matters to a CMO.
10. Return a predictable JSON response.

Important: this agent is currently **RAG-only**. It does not search Google, news websites, or live research APIs. It can analyze only documents that have already been ingested and indexed in the project RAG system.

## 2. Where the agent is implemented

| Responsibility | File | Main class/function |
|---|---|---|
| Request and response data models | `packages/agents/src/multimodal_rag/agents/models.py` | `AgentRequest`, `AgentEvidence`, `AgentFinding`, `AgentResponse` |
| Market trend logic | `packages/agents/src/multimodal_rag/agents/market_intelligence.py` | `MarketIntelligenceAgent` |
| RAG connection used by agents | `packages/agents/src/multimodal_rag/agents/rag_client.py` | `RAGClient`, `InProcessRAGClient` |
| FastAPI route | `services/api/src/multimodal_rag/api/router.py` | `analyze_market_intelligences()` |
| FastAPI app setup | `services/api/src/multimodal_rag/api/main.py` | `create_app()` |
| React API call | `apps/frontend/src/api.js` | `analyzeMarketIntelligences()` |
| React result display | `apps/frontend/src/App.jsx` | `MarketIntelligenceMessage()` |
| Agent tests | `tests/test_market_intelligence_agent.py` | `MarketIntelligenceAgentTests`, `MarketIntelligenceAPITests` |

## 3. The complete flow in one picture

```text
User types a question
        |
        v
React submitQuestion()
        |
        v
apps/frontend API function: analyzeMarketIntelligences()
        |
        v
POST /agents/market-intelligence
        |
        v
FastAPI analyze_market_intelligences()
        |
        v
MarketIntelligenceAgent.run()
        |
        +--> Resolve missing scope values
        |
        +--> Build the RAG search question
        |
        +--> InProcessRAGClient.retrieve()
        |          |
        |          v
        |      RAGService.retrieve()
        |          |
        |          +--> Load user/project index
        |          +--> Load chunk metadata
        |          +--> Run active retriever
        |          +--> Return ranked chunks
        |
        +--> Convert chunks into AgentEvidence
        |
        +--> Mark source recency when possible
        |
        +--> Send evidence to the generation model
        |
        +--> Parse structured JSON
        |
        +--> Keep only multi-document trends
        |
        v
AgentResponse JSON
        |
        v
React MarketIntelligenceMessage()
        |
        v
Show trends, facts, inferences, evidence, scope, and notes
```

## 4. Step 1 — The user sends a question

The user can type a question such as:

```text
What are the trending market strategies right now?
```

In the React application, `frontend/src/App.jsx::submitQuestion()` checks which mode is selected.

When Market Intelligence Agent mode is selected, it sends these values:

- `objective`: the question typed by the user.
- `user_id`: identifies the user's RAG index.
- `project_id`: optionally identifies a project-specific index.
- `industry`: optional industry.
- `geography`: optional region.
- `time_range`: optional time period.
- `additional_context`: optional JSON context.
- `top_k`: how many chunks to retrieve.
- `accessToken`: bearer token used by the API.

The React API helper is `frontend/src/api.js::analyzeMarketIntelligences()`.

It sends a `POST` request to:

```text
/agents/market-intelligence
```

## 5. Step 2 — FastAPI receives the request

The FastAPI handler is:

```text
src/multimodal_rag/api/router.py::analyze_market_intelligences()
```

This route does the following:

1. Receives and validates the JSON body as an `AgentRequest`.
2. Creates a unique `trace_id`.
3. Adds the trace ID to the response header as `X-Trace-ID`.
4. Gets the configured Market Intelligence Agent.
5. Calls `MarketIntelligenceAgent.run()`.
6. Returns the agent's structured response.
7. Converts specific failures into HTTP status codes.

The route is part of the FastAPI app created by:

```text
src/multimodal_rag/api/main.py::create_app()
```

`create_app()` creates the `MarketIntelligenceAgent` with:

```text
MarketIntelligenceAgent(InProcessRAGClient(app.state.rag_service))
```

This means the agent and the RAG service run inside the same Python backend process. The agent does not make an HTTP request to another RAG server.

## 6. Step 3 — Request validation

The request model is `AgentRequest` in:

```text
src/multimodal_rag/agents/models.py::AgentRequest
```

It accepts:

```json
{
  "objective": "What are the trending market strategies right now?",
  "user_id": "alice",
  "project_id": "launch-2026",
  "industry": "B2B software",
  "geography": "North America",
  "time_range": "2026",
  "additional_context": {},
  "top_k": 8
}
```

Validation rules include:

- `objective` must not be empty.
- `user_id` must be a safe scope identifier.
- `project_id`, when present, must be a safe scope identifier.
- `top_k` must be between 1 and 50.
- Blank optional fields are converted to `None`.
- `task_id` is generated automatically when the caller does not provide one.

## 7. Step 4 — The agent resolves the scope

The main agent class is:

```text
src/multimodal_rag/agents/market_intelligence.py::MarketIntelligenceAgent
```

The main method is:

```text
MarketIntelligenceAgent.run()
```

The agent uses `_resolve_scope()` before retrieval.

If the user provides a value, that value is used. If the user does not provide a value, the agent uses these defaults:

| Field | User value missing | Default |
|---|---|---|
| Industry | Yes | `cross-industry` |
| Geography | Yes | `global` |
| Time range | Yes | `last 30 days` |

The response records whether each value came from the user or from a default:

```json
"resolved_scope": {
  "industry": {
    "value": "cross-industry",
    "origin": "default"
  },
  "geography": {
    "value": "global",
    "origin": "default"
  },
  "time_range": {
    "value": "last 30 days",
    "origin": "default"
  }
}
```

This is why a broad question can be answered without asking the user to fill in all three fields.

## 8. Step 5 — The agent builds a RAG query

`MarketIntelligenceAgent._build_query()` creates a larger search question from the user's objective and resolved scope.

For example, the internal query can look like:

```text
Market trend objective: What are the trending market strategies right now?
Industry: cross-industry (default)
Geography: global (default)
Time range: last 30 days (default)
```

If `additional_context` was provided, it is added as JSON to the query.

This query is used to search the existing RAG index. It does not create a new index and does not search the live internet.

## 9. Step 6 — The agent retrieves evidence from RAG

The agent uses the `RAGClient` interface in:

```text
src/multimodal_rag/agents/rag_client.py::RAGClient
```

The interface requires a `retrieve()` method that receives:

- the search question,
- `user_id`,
- `project_id`, and
- `top_k`.

The real implementation is:

```text
src/multimodal_rag/agents/rag_client.py::InProcessRAGClient.retrieve()
```

It calls:

```text
RAGService.retrieve()
```

The RAG service then:

1. Resolves the storage scope from `user_id` and `project_id`.
2. Loads the correct FAISS index.
3. Loads the chunk metadata files.
4. Calls the active retriever.
5. Returns ranked chunks with their text, source, page, score, and metadata.

The active retriever is in:

```text
src/multimodal_rag/rag/retrieval/retriever_2.py::retrieve()
```

The retriever can use the configured dense, lexical, hybrid, and reranking behavior from the project. The agent does not directly manipulate FAISS. It uses the RAG service boundary.

## 10. Step 7 — Retrieved chunks become AgentEvidence

The agent needs a standard evidence object. That object is `AgentEvidence` in:

```text
src/multimodal_rag/agents/models.py::AgentEvidence
```

Each evidence item can contain:

| Field | Meaning |
|---|---|
| `chunk_id` | Unique ID of the RAG chunk |
| `source` | Source file name |
| `document` | Document ID |
| `page` | Main page number, when available |
| `pages` | All page numbers for the chunk |
| `score` | Retrieval score |
| `text_excerpt` | Retrieved chunk text |
| `metadata` | Original metadata dictionary |
| `publication_date` | Date found in metadata, or `null` |
| `url` | URL found in metadata, or `null` |
| `recency` | `recent`, `not_recent`, or `unknown` |

The `AgentEvidence.populate_provenance_fields()` method checks common metadata names:

- Date names: `publication_date`, `published_at`, `published`, `date`.
- URL names: `url`, `source_url`, `source_uri`.

If a date or URL is not present, the agent does not invent one.

## 11. Step 8 — Recency is calculated carefully

The agent uses `MarketIntelligenceAgent._with_recency()` to classify source dates.

The rules are:

- A dated source inside the requested time window is `recent`.
- A dated source outside the requested time window is `not_recent`.
- A missing or invalid date is `unknown`.
- A document's file name is never treated as its publication date.
- An ingestion timestamp is not automatically treated as a publication date.

For the default `last 30 days` scope, the agent compares the source publication date with the current date.

Because this implementation uses indexed documents only, “recent” means “recent according to the date stored in the indexed metadata.” It does not mean that the agent checked the internet today.

## 12. Step 9 — The generation model analyzes the evidence

The method responsible for this is:

```text
MarketIntelligenceAgent._analyze()
```

It builds a prompt containing:

- the user objective,
- resolved industry,
- resolved geography,
- resolved time range,
- chunk IDs,
- document IDs,
- source names,
- page numbers,
- publication dates,
- URLs,
- recency values,
- complete metadata, and
- retrieved text.

The generation model is called through the existing generation function. The maintained project configuration uses Gemini generation for normal answer generation.

The prompt tells the model to:

1. Extract small individual signals.
2. Attach every signal to one or more exact chunk IDs.
3. Group related signals.
4. Mark a group as a trend only when it has evidence from at least two different documents.
5. Separate facts from inference.
6. Use `unclear` when the evidence does not prove momentum or impact.
7. Never invent dates, URLs, sources, or unsupported claims.
8. Return JSON only.

The model produces an internal draft, not the final public response.

## 13. Internal analysis objects

The agent uses three internal Pydantic models.

### `SignalDraft`

Represents one observation found in the evidence.

```json
{
  "signal_id": "s1",
  "statement": "Customers increasingly ask for measurable outcomes.",
  "chunk_ids": ["c1"]
}
```

### `TrendDraft`

Represents a possible group of related signals.

It contains:

- `group_id`
- `signal_ids`
- `is_trend`
- `trend`
- `description`
- `facts`
- `inference`
- `momentum`
- `impact`
- `relevance`
- `confidence`

### `TrendAnalysisDraft`

Contains the complete internal model result:

```json
{
  "signals": [],
  "trend_groups": []
}
```

## 14. Step 10 — The agent removes unsupported trends

The method responsible for this is:

```text
MarketIntelligenceAgent._build_findings()
```

It connects the model's signal IDs back to the real retrieved evidence.

Then it applies the most important safety rule:

```text
A confirmed trend must have evidence from at least two different documents.
```

Examples:

```text
Document A says something once
        |
        +--> Isolated signal, not a confirmed trend
```

```text
Document A shows signal X
Document B shows related signal X
        |
        +--> Possible evidence-backed trend
```

If the model says `is_trend=true` but the attached evidence comes from only one document, `_build_findings()` removes that finding.

This prevents one unusual sentence from becoming a broad market claim.

## 15. What a finding contains

Each public `AgentFinding` contains:

| Field | Simple meaning |
|---|---|
| `trend` | Short name of the trend |
| `description` | Explanation of the trend |
| `facts` | Statements directly supported by evidence |
| `inference` | Interpretation made from those facts |
| `momentum` | `rising`, `stable`, `declining`, or `unclear` |
| `impact` | `low`, `medium`, `high`, or `unclear` |
| `relevance` | Why the trend matters to a CMO |
| `confidence` | Model confidence between 0 and 1 |
| `evidence` | Exact retrieved chunks supporting this finding |

Facts and inferences are deliberately separate.

Example:

```text
Fact:
Two indexed reports mention stronger customer demand for faster implementation.

Inference:
Buyers may be placing more value on time-to-value when comparing vendors.
```

The inference is not presented as a direct fact from the documents.

## 16. Step 11 — The final response is created

The public response model is:

```text
src/multimodal_rag/agents/models.py::AgentResponse
```

A typical response looks like this:

```json
{
  "agent_name": "market_intelligence",
  "status": "completed",
  "summary": "Identified 1 evidence-backed market intelligence(s) from 2 document(s).",
  "findings": [],
  "sources": [],
  "task_id": "task-id",
  "trace_id": "trace-id",
  "user_id": "alice",
  "project_id": "launch-2026",
  "resolved_scope": {
    "industry": {"value": "cross-industry", "origin": "default"},
    "geography": {"value": "global", "origin": "default"},
    "time_range": {"value": "last 30 days", "origin": "default"}
  },
  "missing_context": [],
  "limitations": [],
  "errors": [],
  "error_code": null
}
```

The real response includes the full finding and source objects in the `findings` and `sources` arrays.

There are two different evidence collections:

- `sources`: all retrieved evidence returned by RAG.
- `findings[].evidence`: only the evidence connected to a confirmed finding.

## 17. Status values

The agent can return four statuses.

### `completed`

Retrieval worked and at least one finding was supported by at least two documents.

### `partial`

The request was processed, but no confirmed multi-document trend was found. This can happen when:

- no chunks were retrieved,
- only one document supports a signal, or
- the evidence is not strong enough for a confirmed trend.

### `needs_input`

This status exists in the shared agent contract. The request model already rejects a blank objective, and normal non-blank broad questions now receive automatic defaults. Therefore, ordinary broad questions should be processed instead of stopping because industry, geography, or time range are missing.

### `failed`

Something required for processing failed. Examples include:

- RAG retrieval error,
- missing or unavailable generation model,
- invalid JSON from the generation model, or
- another unexpected backend error.

## 18. Errors and HTTP responses

The FastAPI route maps agent failures to HTTP responses:

| Situation | HTTP behavior |
|---|---|
| Missing RAG index | `404 Not Found` |
| Generation model unavailable | `503 Service Unavailable` |
| Retrieval or generation error | `502 Bad Gateway` |
| Unexpected route failure | `500 Internal Server Error` |
| Missing/invalid authentication | `401 Unauthorized` |
| Invalid request body | `422 Unprocessable Entity` |

The `trace_id` helps connect the frontend response to backend logs.

## 19. What the frontend displays

The React component is:

```text
frontend/src/App.jsx::MarketIntelligenceMessage()
```

It displays:

- agent name and status,
- summary,
- analysis scope,
- whether scope values were requested or defaulted,
- trend name,
- confidence,
- momentum,
- impact,
- evidence facts,
- inference,
- CMO relevance,
- supporting evidence,
- source name,
- page number,
- chunk ID,
- retrieval score,
- recency,
- publication date,
- source URL, when available,
- raw metadata, and
- analysis notes.

The analysis notes are informational. They are not automatically errors. For example, this is a normal note:

```text
Some sources have no indexed publication date; recency is shown as unknown.
```

It means the source metadata is incomplete, not that the agent failed.

## 20. Why dates and URLs may be unavailable

The agent can only show a publication date or URL if that information exists in the indexed chunk metadata.

The ingestion metadata currently focuses on fields such as:

- chunk ID,
- document ID,
- source file,
- page numbers,
- section title,
- layout type,
- extraction method,
- OCR confidence,
- validation status,
- ingestion timestamp, and
- pipeline version.

An ingestion timestamp tells us when the project processed a document. It does not necessarily tell us when the document was published.

Therefore:

- Missing publication date becomes `null`.
- Missing URL becomes `null`.
- Missing date produces `recency: "unknown"`.
- The agent does not guess these values.

To have reliable publication dates and URLs in future results, those fields must be added to document metadata before ingestion/indexing.

## 21. What this agent does and does not do

### It does

- Search the correct scoped RAG index.
- Use default scope for broad questions.
- Preserve retrieved evidence.
- Keep retrieval scores and metadata.
- Extract signals.
- Group related signals.
- Require at least two documents for a confirmed trend.
- Separate facts from inference.
- Explain CMO impact.
- Return structured JSON.
- Return trace IDs and structured status values.

### It does not do

- Search live news websites.
- Call a news search API.
- Browse the internet.
- Guarantee that every indexed document is recent.
- Create a complete marketing strategy.
- Treat one isolated document statement as a confirmed trend.
- Invent missing dates, URLs, sources, or facts.

The current agent is a **trend analysis agent**, not a full strategy-planning agent.

## 22. Simple example from start to finish

User question:

```text
What are the trending market strategies right now?
```

The system does this:

1. Accepts the question as the objective.
2. Uses `cross-industry`, `global`, and `last 30 days` because the user did not provide those fields.
3. Searches the user's RAG index.
4. Finds eight chunks.
5. Preserves each chunk's source, score, text, page, and metadata.
6. Checks whether dates and URLs exist.
7. Sends the evidence to the generation model.
8. The model extracts several signals.
9. The agent groups related signals.
10. A group is kept only if its evidence comes from at least two documents.
11. The final answer shows the trend, facts, inference, CMO impact, confidence, and evidence.
12. Missing source dates or URLs appear as informational analysis notes.

## 23. Testing the agent

The focused tests are in:

```text
tests/test_market_intelligence_agent.py
```

They check:

- successful structured analysis,
- provenance preservation,
- request and default scope,
- isolated signals not becoming trends,
- empty retrieval results,
- retrieval failures,
- invalid generation JSON,
- recency classification,
- missing publication dates and URLs,
- RAG adapter scope forwarding, and
- authenticated FastAPI route behavior.

The tests use fake RAG clients and fake generation output. They do not need live news, live Gemini generation, or paid external calls.

## 24. Short summary

```text
Question
  -> FastAPI request validation
  -> Resolve scope defaults
  -> Search scoped RAG index
  -> Preserve evidence and metadata
  -> Analyze evidence with the generation model
  -> Require two independent documents
  -> Separate facts and inference
  -> Return structured trend response
  -> Display the result in React
```

The most important rule is:

```text
The agent may interpret the retrieved evidence, but it must not invent evidence.
```

## LLM Thinking

This section explains the agent's observable decision process in simple words.

It does not expose private hidden chain-of-thought. Instead, it documents the real prompt, the real functions, the expected model output, and the checks that the Python code performs.

### The important idea

The LLM does not control the whole application.

Python controls the workflow:

```text
Python receives the request
    -> Python decides the scope
    -> Python retrieves RAG evidence
    -> Python builds the prompt
    -> LLM analyzes the supplied evidence
    -> LLM returns JSON
    -> Python validates the JSON
    -> Python removes unsupported findings
    -> Python returns the final response
```

The LLM does not directly call `retrieve()`, does not directly access FAISS, and does not directly decide whether the HTTP request succeeds. Those actions are controlled by the Python application.

### Exact function order

For a normal Market Intelligence Agent request, the main order is:

```text
1. api/router.py::analyze_market_intelligences()
2. agents/market_intelligence.py::MarketIntelligenceAgent.run()
3. MarketIntelligenceAgent._resolve_scope()
4. MarketIntelligenceAgent._missing_context()
5. MarketIntelligenceAgent._build_query()
6. agents/rag_client.py::InProcessRAGClient.retrieve()
7. api/service.py::RAGService.retrieve()
8. rag/retrieval/retriever_2.py::retrieve()
9. MarketIntelligenceAgent._with_recency()
10. MarketIntelligenceAgent._analyze()
11. The configured generation function / Gemini model
12. MarketIntelligenceAgent._parse_analysis()
13. MarketIntelligenceAgent._build_findings()
14. MarketIntelligenceAgent._response()
15. frontend/src/App.jsx::MarketIntelligenceMessage()
```

The exact runtime can stop earlier if the request is invalid, the RAG index is missing, retrieval fails, or the generation model fails.

### 1. Python first resolves the scope

Function:

```text
MarketIntelligenceAgent._resolve_scope()
```

The agent checks the three optional fields:

```text
Industry
Geography
Time range
```

If the user supplied them, Python keeps them. If not, Python inserts:

```text
Industry   -> cross-industry
Geography  -> global
Time range -> last 30 days
```

Python also records the origin of each value:

```text
origin = request
```

or:

```text
origin = default
```

This decision is made by Python, not by the LLM.

### 2. Python creates the retrieval question

Function:

```text
MarketIntelligenceAgent._build_query()
```

For this user question:

```text
What are the trending market strategies right now?
```

Python builds a retrieval question similar to:

```text
Market trend objective: What are the trending market strategies right now?
Industry: cross-industry (default)
Geography: global (default)
Time range: last 30 days (default)
```

If the user supplied extra JSON context, it is added at the end:

```text
Additional context: { ... }
```

This text is used to search the RAG index. It is not yet the LLM analysis prompt.

### 3. Python retrieves the evidence

The agent calls:

```python
self.rag_client.retrieve(
    question=query,
    user_id=request.user_id,
    project_id=request.project_id,
    top_k=request.top_k,
)
```

The in-process RAG adapter forwards this to `RAGService.retrieve()`.

The RAG service:

1. Finds the correct user/project storage scope.
2. Loads the correct index.
3. Loads metadata for the chunks.
4. Calls the active retriever.
5. Returns the best matching chunks.

Each chunk is converted into an `AgentEvidence` object before it reaches the LLM.

The evidence includes text such as:

```text
CHUNK_ID: c_123
DOCUMENT: research-report-1
SOURCE: research-report-1.pdf
PAGE: 4
SCORE: 0.82
PUBLICATION_DATE: 2026-08-01
URL: https://example.com/report
RECENCY: recent
METADATA: { ... }
TEXT: Customers increasingly expect measurable outcomes...
```

The real prompt uses the same information with names such as `CHUNK_ID`, `DOCUMENT`, `SOURCE`, `PAGE`, `PUBLICATION_DATE`, `URL`, `RECENCY`, `METADATA`, and `TEXT`.

### 4. Python calculates recency before the LLM call

Function:

```text
MarketIntelligenceAgent._with_recency()
```

For every retrieved item, Python starts with:

```text
recency = unknown
```

Then:

- If the publication date is valid and inside the time window, it becomes `recent`.
- If the publication date is valid but outside the window, it becomes `not_recent`.
- If the date is missing or invalid, it stays `unknown`.

The LLM is told to trust this value. It is not allowed to guess recency from the file name or from unsupported assumptions.

### 5. The actual analysis prompt

Function:

```text
MarketIntelligenceAgent._analyze()
```

The prompt sent to the generation model follows this structure:

```text
You are a market-trend analyst operating only on the supplied RAG evidence.

Do not use outside knowledge.
Do not invent facts, dates, sources, momentum, or impacts.

The user objective is:
<the user's objective>

Industry:
<resolved industry>

Geography:
<resolved geography>

Time range:
<resolved time range>

Perform these steps in order:
1. Extract atomic signals from the evidence.
2. Cite exact CHUNK_ID values for every signal.
3. Group related signals.
4. Mark a group as a trend only when its signals use at least two distinct DOCUMENT values.
5. Separate evidence facts from inference.
6. Use momentum="unclear" when the evidence does not prove direction over time.
7. Treat a source as recent only when RECENCY="recent".
8. Never infer dates or URLs from document names or text.

Return only valid JSON in the required shape.

Evidence:
<all retrieved evidence items>
```

The prompt is created in Python using an f-string. The evidence text is inserted into the prompt before it is sent to the generation function.

### 6. What the LLM is asked to do

The LLM receives the user's goal plus the retrieved evidence. It is asked to perform four main reasoning tasks:

#### A. Extract signals

A signal is one small observation from one or more chunks.

Example:

```text
Signal: Buyers are asking for faster implementation.
Evidence: c_123
```

The model must include the exact chunk ID so the application can trace the statement back to the source.

#### B. Group related signals

The model compares the signals and groups signals that describe the same type of change.

Example:

```text
Signal 1: Customers ask for faster implementation.
Signal 2: Competitors advertise faster time-to-value.
Signal 3: Sales teams emphasize quick deployment.

Possible group: Time-to-value is becoming more important.
```

#### C. Decide whether the group is a trend

The model is instructed to set `is_trend=true` only when the group is supported by at least two different document IDs.

This is a model instruction, but it is also checked again by Python afterward.

#### D. Explain the meaning of the trend

For a supported group, the model provides:

- a short trend name,
- a description,
- direct facts,
- an inference,
- momentum,
- business impact,
- CMO relevance, and
- confidence.

The prompt tells the model to use `unclear` instead of guessing when the evidence does not support momentum or impact.

### 7. The JSON the LLM must return

The LLM is required to return an object with this structure:

```json
{
  "signals": [
    {
      "signal_id": "s1",
      "statement": "Customers ask for faster implementation.",
      "chunk_ids": ["c_123"]
    }
  ],
  "trend_groups": [
    {
      "group_id": "g1",
      "signal_ids": ["s1", "s2"],
      "is_trend": true,
      "trend": "Time-to-value is becoming more important",
      "description": "Several documents describe stronger demand for faster results.",
      "facts": [
        "Document A reports demand for faster implementation.",
        "Document B describes faster time-to-value messaging."
      ],
      "inference": "Buyers may be giving more weight to speed when selecting vendors.",
      "momentum": "rising",
      "impact": "high",
      "relevance": "CMO messaging may need to show value earlier.",
      "confidence": 0.86
    }
  ]
}
```

The LLM does not return the final `AgentResponse` directly. It returns this internal analysis draft first.

### 8. Python validates the LLM output

Function:

```text
MarketIntelligenceAgent._parse_analysis()
```

The function:

1. Removes leading/trailing whitespace.
2. Removes a Markdown JSON fence if the model returned one.
3. Finds the JSON object.
4. Validates it with the Pydantic `TrendAnalysisDraft` model.

If the model returns invalid JSON or misses required fields, Python raises a structured generation error. The agent then returns:

```text
status = failed
error_code = generation_error
```

This prevents malformed model output from being silently shown as a correct trend.

### 9. Python performs the final safety check

Function:

```text
MarketIntelligenceAgent._build_findings()
```

This is a second safety layer after the LLM's own instructions.

Python performs these checks:

1. Match every `signal_id` to a signal returned by the model.
2. Collect the chunk IDs referenced by those signals.
3. Match those chunk IDs to real retrieved evidence.
4. Remove duplicate chunk IDs.
5. Count distinct document IDs.
6. Ignore any group that is not marked `is_trend=true`.
7. Ignore any group supported by fewer than two distinct documents.
8. Build an `AgentFinding` only for the remaining groups.

The important rule is:

```python
if len({item.document for item in group_evidence}) < 2:
    continue
```

In simple words: if a trend is supported by only one document, the application throws that finding away.

### 10. How the final status is decided

After `_build_findings()` returns:

```text
If one or more findings remain:
    status = completed

If no findings remain:
    status = partial
```

This means the LLM can suggest a trend, but the final Python filtering decides whether it is allowed into the public response.

### 11. How facts and inference are handled

The LLM receives separate fields for `facts` and `inference`.

The application preserves those fields separately in `AgentFinding`.

```text
facts
  = what the documents directly support

inference
  = what the model concludes from those facts
```

Example:

```text
Fact:
Two reports mention increased demand for faster delivery.

Inference:
Speed may be becoming a stronger buying factor.
```

The application does not convert the inference into a fact.

### 12. What happens when evidence is weak

There are several weak-evidence cases.

#### No chunks retrieved

The agent returns:

```text
status = partial
findings = []
```

#### Only one document supports the group

The group is removed by `_build_findings()` and the final status becomes `partial`.

#### Missing publication date

The source remains available, but:

```text
publication_date = null
recency = unknown
```

#### Missing URL

The source remains available, but:

```text
url = null
```

#### Invalid model JSON

The agent returns a structured generation error instead of displaying unreliable output.

### 13. Example decision walkthrough

User asks:

```text
What are the trending market strategies right now?
```

Python first creates this scope:

```text
industry: cross-industry
geography: global
time_range: last 30 days
```

RAG returns:

```text
Chunk c1 from document A: Customers want measurable outcomes.
Chunk c2 from document B: Buyers compare vendors by measurable outcomes.
Chunk c3 from document C: One campaign used a new social channel once.
```

The LLM may create two groups:

```text
Group 1: measurable outcomes
  Evidence: document A and document B
  is_trend: true

Group 2: new social channel
  Evidence: document C only
  is_trend: true in the model output, but only one document supports it
```

Python then keeps Group 1 and removes Group 2.

The user receives one confirmed finding, not two.

### 14. What “thinking” is visible to the user

The user can see the result of the process:

- resolved scope,
- source chunks,
- retrieval scores,
- publication date,
- URL,
- recency status,
- facts,
- inference,
- momentum,
- impact,
- relevance,
- confidence, and
- analysis notes.

The user does not see hidden model reasoning. The reliable audit trail is the evidence, the model's structured fields, the trace ID, and the deterministic Python checks.

### 15. Final simplified formula

```text
Final market intelligence
=
retrieved RAG evidence
+
LLM signal extraction and grouping
+
LLM facts/inference classification
+
Python JSON validation
+
Python two-document evidence check
+
structured AgentResponse
```

The agent's core safety principle is:

```text
The LLM proposes an analysis.
Python verifies the structure and evidence.
Only verified findings are returned.
```
# Current physical layout

The Python import names remain unchanged, but implementation files now live
under the component folders described in the README: API under
`services/api/`, agents under `packages/agents/`, web search under
`packages/web-search/`, RAG under `packages/rag-core/`, ingestion under
`packages/ingestion/`, and the UIs under `apps/`.
