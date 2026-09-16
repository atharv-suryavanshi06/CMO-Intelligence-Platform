# RAG Answer Mode vs Market Intelligence Agent Mode

This guide explains the difference between the two modes in the CMO Intelligence frontend.

Both modes use information from the documents indexed in the existing RAG system. Neither mode uses web search or news APIs.

## Short answer

Use **RAG Answer mode** when you want an answer to a direct question.

Use **Market Intelligence Agent mode** when you want the system to study several pieces of document evidence and decide whether they show a real market or marketing trend.

The simple difference is:

```text
RAG Answer mode:
Question → Retrieve evidence → Generate an answer

Market Intelligence Agent mode:
Objective → Retrieve evidence → Extract signals → Group signals → Check for a real trend → Explain the trend
```

## Main difference

| Area | RAG Answer mode | Market Intelligence Agent mode |
|---|---|---|
| Main purpose | Answer a question | Find and analyze meaningful trends |
| Best for | Facts, explanations, summaries, document lookup | Market changes, customer behavior, industry movement, marketing patterns |
| Main output | A written answer | Structured trend findings |
| Evidence use | Supports the answer | Must support every identified trend |
| Reasoning level | Direct question answering | Signal extraction, grouping, comparison, and trend judgement |
| Evidence rule | Returns the most relevant retrieved chunks | A trend normally needs evidence from at least two different documents |
| Output style | Answer, sources, retrieved chunks, trace ID | Summary, findings, momentum, impact, relevance, confidence, evidence, sources, trace ID |
| Strategy advice | May answer a strategy question from the documents | Does not build a complete marketing strategy |
| Frontend mode | `RAG Answer` | `Market Intelligence Agent` |
| Backend endpoint | `POST /answer` | `POST /agents/market-intelligence` |

## RAG Answer mode

### What it does

RAG Answer mode is the normal document question-and-answer mode.

You ask a question, for example:

```text
What are the main challenges mentioned in the customer research report?
```

The system then:

1. Searches the correct user/project document index.
2. Retrieves the most relevant chunks.
3. Sends the retrieved evidence to the answer-generation model.
4. Returns an answer based on that evidence.
5. Shows the sources and retrieved evidence in the frontend.

### What it is good at

Use this mode for questions such as:

- What does the report say about customer retention?
- Summarize the findings from the Q3 report.
- Which risks are mentioned in the document?
- What are the key recommendations in this presentation?
- What was the reported change in customer demand?

These questions usually ask for a direct answer, explanation, or summary.

### What it returns

The backend returns:

```json
{
  "answer": "The documents describe...",
  "chunks": [],
  "sources": [],
  "trace_id": "..."
}
```

The `chunks` contain the retrieved document text. The `sources` identify where the information came from. The `trace_id` helps connect the answer to backend logs and debugging information.

### What it does not do automatically

RAG Answer mode does not automatically compare many signals and decide that they form a long-term trend. It answers the question using the retrieved context.

For example, if you ask:

```text
What are the most important market intelligences?
```

it may provide a useful answer, but it is not required to:

- extract separate signals,
- group related signals,
- compare signals across documents,
- reject isolated evidence,
- assign trend momentum,
- calculate confidence for each trend.

## Market Intelligence Agent mode

### What it does

Market Intelligence Agent mode is an analysis workflow. It does more than summarize retrieved text.

Its purpose is to answer questions such as:

```text
Identify important changes in customer demand and explain what they mean for a CMO.
```

The agent follows this process:

1. **Retrieve evidence** from the scoped RAG index.
2. **Extract signals** from the retrieved chunks.
3. **Group related signals** that talk about the same type of change.
4. **Check whether a group is a real trend.**
5. **Analyze the trend** and explain why it matters.

### What is a signal?

A signal is one individual observation from a document.

Example signals:

- One report says customers are asking for faster delivery.
- Another report says buyers are choosing vendors with shorter implementation times.
- A third report says sales teams are changing their messaging around speed.

Each of these is a signal. A single signal is not automatically a trend.

### What is a trend?

A trend is a meaningful pattern supported by related evidence.

For this agent, a trend should normally be supported by at least two different documents.

For example:

```text
Document A: Customers prefer faster delivery.
Document B: Competitors are promoting faster implementation.
Document C: Sales conversations increasingly mention speed.

Possible trend: Speed and time-to-value are becoming stronger buying factors.
```

The agent will not treat one isolated statement as a confirmed trend.

### What it is good at

Use this mode for questions such as:

- Which customer needs are becoming more important?
- What market changes appear across our reports?
- Which marketing themes are gaining momentum?
- What repeated changes should a CMO pay attention to?
- What trends in the documents may affect demand generation?
- Which shifts appear relevant to our current business objective?

### What it returns

The response uses a shared agent format:

```json
{
  "agent_name": "market_intelligence",
  "status": "completed",
  "summary": "Identified evidence-backed market intelligences.",
  "findings": [
    {
      "trend": "Time-to-value is becoming more important",
      "description": "Several documents describe stronger customer interest in faster results.",
      "facts": [
        "Document A reports customer demand for faster delivery.",
        "Document B describes competitors promoting faster implementation."
      ],
      "inference": "Buyers may be using time-to-value as a stronger selection factor.",
      "momentum": "rising",
      "impact": "high",
      "relevance": "This matters to a CMO because messaging may need to show value earlier.",
      "confidence": 0.86,
      "evidence": []
    }
  ],
  "sources": [],
  "task_id": "...",
  "trace_id": "...",
  "user_id": "...",
  "project_id": "..."
}
```

Important fields:

- `trend`: the short name of the trend.
- `description`: what the trend means.
- `facts`: statements directly supported by documents.
- `inference`: the agent's interpretation of those facts.
- `momentum`: whether the trend appears to be rising, stable, declining, or unclear.
- `impact`: possible business or market importance.
- `relevance`: why the trend matters to the user's objective and to a CMO.
- `confidence`: how strongly the available evidence supports the finding.
- `evidence`: the original chunks used for the finding.

## Facts versus inference

The agent tries to separate what the documents directly say from what the model concludes from those facts.

### Fact

```text
Two reports state that customers are asking for shorter implementation times.
```

### Inference

```text
Customers may be placing more value on fast time-to-value.
```

The fact must come from the retrieved documents. The inference is an interpretation and is labelled separately.

This separation helps prevent the agent from presenting an interpretation as if it were a documented fact.

## Context fields

The Market Intelligence Agent accepts more context than the normal RAG answer request.

| Field | Meaning | Required? |
|---|---|---|
| Objective | What you want the agent to investigate | Yes, and it should be specific |
| User ID | Selects the user's isolated document index | Yes |
| Project ID | Selects an optional project-specific index | No |
| Industry | Narrows the market area | No |
| Geography | Narrows the location or region | No |
| Time range | Tells the agent which period matters | No |
| Additional context | Extra information in JSON form | No |
| Top K | Number of chunks to retrieve | No; it has a default |

You can run the agent without industry, geography, time range, or project ID. In that case, it analyzes the available documents and reports those missing details as limitations when relevant.

Good objective:

```text
Identify changes in customer demand for enterprise analytics software in North America during 2025-2026.
```

Too broad:

```text
Market trends
```

For a very broad request, the agent returns `needs_input` instead of inventing trends.

## When to use which mode

### Choose RAG Answer mode when:

- You need one direct answer.
- You want a summary of a particular document.
- You want to find a specific fact.
- You want the answer quickly.
- You do not need trend classification or cross-document analysis.

Example:

```text
What percentage of customers reported onboarding problems?
```

### Choose Market Intelligence Agent mode when:

- You want to find repeated changes across documents.
- You want to know whether several signals form a meaningful trend.
- You want momentum, impact, and confidence.
- You want the result structured for a future strategy or briefing agent.
- You want every finding connected to its original evidence.

Example:

```text
Identify the most important changes in customer expectations that should matter to a CMO.
```

## Example comparison

### Same question in RAG Answer mode

Question:

```text
What changes in customer expectations are mentioned in the reports?
```

Likely result:

```text
The reports mention faster delivery, easier onboarding, and more measurable outcomes.
```

This is a direct answer with supporting chunks.

### Same question in Market Intelligence Agent mode

Objective:

```text
Identify meaningful changes in customer expectations and explain their importance to a CMO.
```

Likely result:

```text
Trend: Customers increasingly expect faster time-to-value.

Facts:
- Report A mentions demand for faster delivery.
- Report B mentions competitor messaging around quick implementation.

Inference:
Buyers may be using speed as an important vendor-selection factor.

Momentum: rising
Impact: high
Confidence: 0.86
```

This is an analysis of a pattern, not just a list of document statements.

## What happens when evidence is weak?

The Market Intelligence Agent is designed to avoid unsupported conclusions.

Possible statuses are:

- `completed`: at least one supported trend was identified.
- `partial`: retrieval worked, but the evidence was not strong enough to identify a confirmed trend.
- `needs_input`: the objective is too broad or necessary request information is missing.
- `failed`: retrieval or structured model generation failed.

For example, if only one document mentions a possible change, the agent may return `partial` and leave the findings empty. This is expected behavior.

## What each mode does with sources

Both modes preserve document provenance.

The retrieved information can include:

- source file,
- document ID,
- page number,
- chunk ID,
- retrieval score,
- section name,
- ingestion timestamp,
- publication or date metadata when present,
- other metadata stored with the chunk.

RAG Answer mode displays sources and retrieved evidence for the answer.

Market Intelligence Agent mode attaches evidence to each trend finding, so future agents can use the same original sources without losing provenance.

## How the modes fit into the future agent system

The current flow is:

```text
User
 ├─ RAG Answer mode → Direct grounded answer
 └─ Market Intelligence Agent → Evidence-backed trend findings
```

The planned multi-agent flow is:

```text
Market Intelligence Agent
        ↓
Market Strategy Agent
        ↓
Briefing Agent
```

The Market Intelligence Agent identifies and explains trends. It does not create a complete marketing plan. A future Market Strategy Agent can use the trend findings to create recommendations, and a future Briefing Agent can turn those results into an executive briefing.

## Simple rule to remember

```text
Need an answer?        Use RAG Answer mode.
Need trend analysis?   Use Market Intelligence Agent mode.
Need a strategy?       Wait for the future Market Strategy Agent.
```

Both modes are grounded in the documents available in the scoped RAG index. If the documents do not contain enough evidence, the correct result is a limitation or an insufficient-evidence status—not an invented answer or trend.
