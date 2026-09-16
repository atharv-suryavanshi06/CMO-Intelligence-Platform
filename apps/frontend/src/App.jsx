import { useEffect, useRef, useState } from "react";
import { analyzeMarketIntelligence, askQuestion, createMarketStrategy, deleteChat, downloadPresentation, extractChatDocument, followUpMeeting, forkChat, generatePresentation, getChat, getIngestionStatus, getPresentationStatus, listChats, listPresentationTemplates, login, prepareMeeting, previewPresentation, signup, uploadDocument } from "./api.js";


const suggestions = [
  "What was the scope of the Bain and Meta Conversational Commerce Survey in India?",
  "What was the sample and response rate for The CMO Survey 2025?",
  "What must technology enable in an effective Marketing Operating Model (MOM)?",
];

function makeId() {
  return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
}

function templatePreviewStyle(templateId) {
  const palettes = [
    ["#0f172a", "#38bdf8", "#f8fafc"], ["#312e81", "#c4b5fd", "#f5f3ff"],
    ["#134e4a", "#5eead4", "#f0fdfa"], ["#7c2d12", "#fdba74", "#fff7ed"],
  ];
  const value = [...templateId].reduce((sum, char) => sum + char.charCodeAt(0), 0);
  const [ink, accent, canvas] = palettes[value % palettes.length];
  return { "--template-ink": ink, "--template-accent": accent, "--template-canvas": canvas };
}

function formatPages(pages = []) {
  if (!pages.length) return "Page not available";
  return `Page${pages.length > 1 ? "s" : ""} ${pages.join(", ")}`;
}

function SourceCard({ source }) {
  const isWebSource = Boolean(source.url);
  const label = source.title || source.document || source.source || "Source unavailable";
  const pages = source.pages || (source.page ? [source.page] : []);
  return (
    <article className="source-card">
      <div className="source-icon">↗</div>
      <div>
        {isWebSource ? <a href={source.url} target="_blank" rel="noreferrer"><strong>{label}</strong></a> : <strong>{label}</strong>}
        <span>{isWebSource ? source.url : formatPages(pages)}</span>
        {source.section_title && <small>{source.section_title}</small>}
      </div>
    </article>
  );
}

function formatTelemetry(value, digits = 2) {
  return typeof value === "number" ? value.toFixed(digits) : "Unavailable";
}

const ETL_STAGES = [
  { key: "upload", label: "Upload", detail: "File received in the workspace" },
  { key: "extract", label: "Extraction", detail: "Reading pages, text, tables, and figures" },
  { key: "chunk", label: "Chunking", detail: "Building sentence-safe retrieval chunks" },
  { key: "embed", label: "Embedding", detail: "Creating vectors for retrieval" },
  { key: "store", label: "Store document", detail: "Saving the document artifact" },
  { key: "index", label: "Build index", detail: "Refreshing the scoped ChromaDB index" },
];

const INGESTION_FILE_EXTENSIONS = [".pdf", ".mp4", ".mp3", ".doc", ".docx", ".ppt", ".pptx"];
const ACTIVE_INGESTION_FILE_EXTENSIONS = [".pdf", ".mp4", ".mp3", ".doc", ".docx", ".ppt", ".pptx"];
const INGESTION_ACCEPT = ".pdf,.mp4,.mp3,.doc,.docx,.ppt,.pptx,application/pdf,video/mp4,audio/mpeg,audio/mp3,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.ms-powerpoint,application/vnd.openxmlformats-officedocument.presentationml.presentation";

function formatFileSize(bytes = 0) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function ETLWorkspace({ accessToken, userId }) {
  const [selectedFile, setSelectedFile] = useState(null);
  const [runState, setRunState] = useState("idle");
  const [activeStage, setActiveStage] = useState(-1);
  const [extractionProgress, setExtractionProgress] = useState(null);
  const [isDragging, setIsDragging] = useState(false);
  const [storedDocs, setStoredDocs] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem("cmo-etl-documents") || "[]");
    } catch {
      return [];
    }
  });
  const [error, setError] = useState("");
  const cancelledRef = useRef(false);

  useEffect(() => () => { cancelledRef.current = true; }, []);

  function acceptFile(file) {
    if (!file) return;
    const extension = `.${file.name.split(".").pop()?.toLowerCase() || ""}`;
    if (!INGESTION_FILE_EXTENSIONS.includes(extension)) {
      setError("Unsupported file format. Allowed formats: PDF, MP4, MP3, DOC, DOCX, PPT, PPTX.");
      return;
    }
    if (!ACTIVE_INGESTION_FILE_EXTENSIONS.includes(extension)) {
      setError(`${extension.slice(1).toUpperCase()} is an allowed format, but its ingestion pipeline is not available yet.`);
      return;
    }
    if (file.size === 0) {
      setError("Empty files are not accepted.");
      return;
    }
    if (file.size > 50 * 1024 * 1024) {
      setError("Uploads must be smaller than 50 MB for this workspace.");
      return;
    }
    setSelectedFile(file);
    setRunState("idle");
    setActiveStage(-1);
    setExtractionProgress(null);
    setError("");
  }

  function handleDrop(event) {
    event.preventDefault();
    setIsDragging(false);
    acceptFile(event.dataTransfer.files?.[0]);
  }

  async function runPipeline() {
    if (!selectedFile || runState === "running") return;
    cancelledRef.current = false;
    setError("");
    setRunState("running");
    setActiveStage(0);
    try {
      const created = await uploadDocument({ file: selectedFile, userId: userId.trim(), accessToken });
      let current = created;
      const stageIndex = (stage) => Math.max(0, ETL_STAGES.findIndex((item) => item.key === stage));
      while (!cancelledRef.current && current.status !== "completed" && current.status !== "failed") {
        setActiveStage(stageIndex(current.stage));
        if (current.stage === "extract" && current.extraction_total) {
          setExtractionProgress({ completed: current.extraction_completed, total: current.extraction_total, percent: current.extraction_percent });
        }
        await new Promise((resolve) => setTimeout(resolve, 900));
        if (!cancelledRef.current) current = await getIngestionStatus({ jobId: current.job_id, accessToken });
      }
      if (cancelledRef.current) return;
      if (current.status === "failed") throw new Error(current.error || "The ingestion pipeline failed.");
      const documentRecord = {
        id: current.job_id,
        name: current.filename,
        size: selectedFile.size,
        createdAt: new Date().toISOString(),
        status: "stored",
        chunkCount: current.chunk_count,
        embeddedCount: current.embedded_count,
      };
      setStoredDocs((stored) => {
        const next = [documentRecord, ...stored].slice(0, 6);
        localStorage.setItem("cmo-etl-documents", JSON.stringify(next));
        return next;
      });
      setRunState("stored");
      setActiveStage(ETL_STAGES.length);
      setExtractionProgress({ completed: current.extraction_total, total: current.extraction_total, percent: 100 });
    } catch (pipelineError) {
      if (!cancelledRef.current) {
        setRunState("error");
        setError(pipelineError.message || "Unable to start document ingestion.");
      }
    }
  }

  const progress = runState === "stored" ? 100 : runState === "running" && activeStage === 1 && extractionProgress ? extractionProgress.percent : runState === "running" ? ((activeStage + 0.5) / ETL_STAGES.length) * 100 : 0;
  const activeLabel = ETL_STAGES[activeStage]?.label || "the pipeline";

  return (
    <section className="etl-workspace">
      <div className="etl-hero">
        <div className="eyebrow">DOCUMENT ETL / LOCAL WORKSPACE</div>
        <h2>Turn a document into retrieval-ready evidence</h2>
        <p>Choose PDF, MP4, MP3, DOC, DOCX, PPT, or PPTX. All listed formats are processed by the authenticated ingestion pipeline.</p>
      </div>

      <div className="etl-grid">
        <article className="etl-upload-card">
          <div className="etl-card-kicker">01 / Source document</div>
          <div className={`etl-upload-zone ${isDragging ? "dragging" : ""}`} onDrop={handleDrop} onDragOver={(event) => { event.preventDefault(); setIsDragging(true); }} onDragLeave={() => setIsDragging(false)}>
            <div>
              <div className="etl-upload-mark">FILES</div>
              <h3>Drop a supported file here</h3>
              <p>Formats: PDF, MP4, MP3, DOC, DOCX, PPT, PPTX.</p>
              <label className="etl-browse-button">Browse files<input type="file" accept={INGESTION_ACCEPT} onChange={(event) => acceptFile(event.target.files?.[0])} /></label>
            </div>
          </div>
          {selectedFile && (
            <div className="etl-file-row">
              <span className="etl-file-icon">PDF</span>
              <div className="etl-file-meta"><strong title={selectedFile.name}>{selectedFile.name}</strong><span>{formatFileSize(selectedFile.size)} selected</span></div>
              <button className="etl-remove-button" type="button" onClick={() => { setSelectedFile(null); setRunState("idle"); setActiveStage(-1); setError(""); }} aria-label="Remove selected file">x</button>
            </div>
          )}
          <button className="etl-run-button" type="button" onClick={runPipeline} disabled={!selectedFile || runState === "running"}>
            {runState === "running" ? "Pipeline running..." : runState === "stored" ? "Run another document" : "Run ETL pipeline"}
          </button>
        </article>

        <article className="etl-pipeline-card">
          <div className="etl-card-kicker">02 / Pipeline activity</div>
          <h3>From file to stored artifact</h3>
          <div className="etl-stage-list">
            {ETL_STAGES.map((stage, index) => {
              const done = runState === "stored" || activeStage > index;
              const active = runState === "running" && activeStage === index;
              return <div className={`etl-stage ${done ? "done" : active ? "active" : "pending"}`} key={stage.key}>
                <span className="etl-stage-marker">{done ? "OK" : index + 1}</span>
                <div className="etl-stage-copy"><strong>{stage.label}</strong><span>{stage.detail}</span></div>
                <span className="etl-stage-status">{done ? "Complete" : active ? "In progress" : "Queued"}</span>
              </div>;
            })}
          </div>
          <div className="etl-progress" aria-label={`ETL progress ${Math.round(progress)} percent`}><i style={{ width: `${progress}%` }} /></div>
          {runState === "running" && activeStage === 1 && extractionProgress && <p>Extraction: {extractionProgress.completed}/{extractionProgress.total} regions ({extractionProgress.percent}%)</p>}
        </article>
      </div>

      <div className="etl-status-banner">
        {error ? <><strong>Upload needs attention</strong>{error}</> : runState === "running" ? <><strong>{activeLabel} in progress</strong>Processing the selected file and moving it to the next handoff.</> : runState === "stored" ? <><strong>Document stored and indexed</strong>The document was chunked, embedded, and added to the scoped retrieval index.</> : <><strong>Ready when you are</strong>Select a supported file to run the authenticated ingestion pipeline.</>}
      </div>

      <section className="etl-history">
        <div className="etl-history-header"><div><div className="eyebrow">03 / Stored artifacts</div><h3>Recently stored</h3></div><span>{storedDocs.length} document{storedDocs.length === 1 ? "" : "s"}</span></div>
        {storedDocs.length ? <div className="etl-history-list">{storedDocs.map((documentRecord) => <article className="etl-history-item" key={documentRecord.id}><span className="etl-history-icon">+</span><div className="etl-history-meta"><strong title={documentRecord.name}>{documentRecord.name}</strong><span>{formatFileSize(documentRecord.size)} - {documentRecord.chunkCount ?? "?"} chunks - {documentRecord.embeddedCount ?? "?"} embeddings - {new Date(documentRecord.createdAt).toLocaleString()}</span></div><span className="etl-history-status">Stored</span></article>)}</div> : <div className="etl-empty">No documents stored in this browser yet.</div>}
      </section>
      <p className="etl-footnote">Uploads are processed by the authenticated ingestion job, then embedded and added to the selected user's scoped ChromaDB collection.</p>
    </section>
  );
}

function approximateTokens(text = "") {
  return Math.ceil(text.trim().length / 4);
}

function hasSentenceBoundary(text = "") {
  return /[.!?…]["')\]]?$/.test(text.trim());
}

function ScoreBar({ label, value, max }) {
  const width = typeof value === "number" && max > 0 ? Math.max(3, Math.min(100, (value / max) * 100)) : 0;
  return <div className="score-row"><span>{label}</span><div className="score-track"><i style={{ width: `${width}%` }} /></div><strong>{formatTelemetry(value, 3)}</strong></div>;
}

function RAGTraceWorkspace({ messages, selectedTraceId, onSelectTrace }) {
  const traceMessages = messages.filter((message) => message.ragTrace);
  const selected = traceMessages.find((message) => message.id === selectedTraceId) || traceMessages.at(-1);
  if (!selected) return <section className="trace-workspace trace-empty"><div className="eyebrow">RAG TRACE</div><h2>Run a RAG question to inspect its evidence</h2><p>Each normal RAG answer will appear here with the exact chunks, scores, and diagnostics used for that answer.</p></section>;

  const trace = selected.ragTrace;
  const items = trace.retrieved_items || [];
  const maximum = (field) => Math.max(0, ...items.map((item) => item[field] || 0));
  const citedIds = new Set((trace.citations || []).map((citation) => citation.chunk_id));

  return <section className="trace-workspace">
    <div className="trace-workspace-header">
      <div><div className="eyebrow">QUERY-TIME RAG OBSERVABILITY</div><h2>RAG Trace</h2><p>{trace.question || "Selected answer"}</p></div>
      <label className="trace-selector">Answer trace<select value={selected.id} onChange={(event) => onSelectTrace(event.target.value)}>{traceMessages.map((message, index) => <option key={message.id} value={message.id}>Question {index + 1}: {(message.ragTrace.question || "Untitled").slice(0, 70)}</option>)}</select></label>
    </div>
    <div className="trace-metrics">
      <article><span>Retrieved</span><strong>{trace.actual_retrieved_count ?? items.length}</strong><small>of top {trace.configured_top_k ?? "?"}</small></article>
      <article><span>Retrieval</span><strong>{formatTelemetry(trace.retrieval_latency_ms, 0)} ms</strong><small>vector and rerank time</small></article>
      <article><span>Generation</span><strong>{formatTelemetry(trace.generation_latency_ms, 0)} ms</strong><small>{trace.generation_total_tokens ?? "?"} total tokens</small></article>
      <article><span>Citations</span><strong>{(trace.citations || []).length}</strong><small>{(trace.uncited_sources || []).length} retrieved but uncited</small></article>
    </div>
    <section className="trace-pipeline"><div><strong>Question</strong><span>{trace.question || "Unavailable"}</span></div><b>→</b><div><strong>Retrieve</strong><span>{items.length} ranked chunks</span></div><b>→</b><div><strong>Prompt</strong><span>{trace.generation_prompt_tokens ?? "?"} tokens</span></div><b>→</b><div><strong>Answer</strong><span>{(trace.citations || []).length} cited sources</span></div></section>
    <section className="trace-section">
      <div className="trace-section-heading"><div><div className="eyebrow">RETRIEVED EVIDENCE</div><h3>Chunks used for this answer</h3></div><p>Character and sentence-boundary checks are diagnostics; they do not prove source completeness.</p></div>
      <div className="trace-chunk-list">{items.map((item) => {
        const isCited = citedIds.has(item.chunk_id);
        return <article className="trace-chunk" key={item.chunk_id}>
          <header><div><span className="rank-badge">#{item.rank}</span><strong>{item.document_name}</strong><small>{formatPages(item.page_numbers)} · {item.section_title || "Section unavailable"}</small></div><span className={isCited ? "citation-status cited" : "citation-status"}>{isCited ? "Cited" : "Retrieved only"}</span></header>
          <div className="chunk-diagnostics"><span>{item.chunk_text?.length || 0} characters</span><span>~{approximateTokens(item.chunk_text)} tokens</span><span>{hasSentenceBoundary(item.chunk_text) ? "Ends at a sentence boundary" : "May continue into another chunk"}</span><span>{item.metadata?.validation_status || "Validation unavailable"}</span></div>
          <div className="score-bars"><ScoreBar label="Combined rerank" value={item.combined_rerank_score} max={maximum("combined_rerank_score")} /><ScoreBar label="Vector similarity" value={item.raw_vector_score} max={maximum("raw_vector_score")} /><ScoreBar label="BM25" value={item.bm25_score} max={maximum("bm25_score")} /><ScoreBar label="RRF" value={item.rrf_score} max={maximum("rrf_score")} /></div>
          <details><summary>Inspect chunk text and metadata</summary><pre className="chunk-text">{item.chunk_text}</pre><div className="metadata-grid"><span>Chunk ID <strong>{item.chunk_id}</strong></span><span>Layout <strong>{item.metadata?.layout_type || "Unavailable"}</strong></span><span>OCR confidence <strong>{formatTelemetry(item.metadata?.ocr_confidence, 2)}</strong></span><span>Extraction <strong>{item.metadata?.extraction_method || "Unavailable"}</strong></span></div>{item.metadata_note && <p className="metadata-note">{item.metadata_note}</p>}</details>
        </article>;
      })}</div>
    </section>
  </section>;
}

function AnswerMessage({ message, onInspectTrace }) {
  return (
    <div className="message assistant-message">
      <div className="avatar assistant-avatar">CMO</div>
      <div className="message-content">
        <div className="message-label">Intelligence assistant{message.reused_from_message_id && <span className="reused-badge">Reused earlier answer</span>}</div>
        <div className="answer-copy">{message.answer}</div>
        {message.sources?.length > 0 && (
          <div className="sources-block">
            <div className="section-label">Sources</div>
            <div className="source-grid">
              {message.sources.map((source) => (
                <SourceCard key={`${source.chunk_id}-${source.source}`} source={source} />
              ))}
            </div>
          </div>
        )}
        {message.chunks?.length > 0 && (
          <details className="evidence-details">
            <summary>View retrieved evidence ({message.chunks.length})</summary>
            <div className="evidence-list">
              {message.chunks.map((chunk) => (
                <div className="evidence-item" key={chunk.metadata?.chunk_id || chunk.text}>
                  <div className="evidence-meta">
                    <span>{chunk.source}</span>
                    <span>{chunk.page ? `Page ${chunk.page}` : "Page unavailable"}</span>
                  </div>
                  <p>{chunk.text}</p>
                </div>
              ))}
            </div>
          </details>
        )}
        {message.traceId && <div className="trace-id">Trace {message.traceId}</div>}
        {message.ragTrace && <button className="inspect-trace-button" onClick={() => onInspectTrace(message.id)}>Open RAG Trace</button>}
      </div>
    </div>
  );
}

function MarketIntelligenceMessage({ message }) {
  return (
    <div className="message assistant-message">
      <div className="avatar assistant-avatar">MI</div>
      <div className="message-content">
        <div className="message-label">Market Intelligence Agent · {message.status}{message.reused_from_message_id && <span className="reused-badge">Reused earlier answer</span>}</div>
        <div className="answer-copy">{message.executive_summary}</div>
        {message.sources?.length > 0 && (
          <div className="sources-block">
            <div className="section-label">References</div>
            <div className="source-grid">
              {message.sources.map((source) => (
                <SourceCard key={`${source.chunk_id}-${source.source}`} source={source} />
              ))}
            </div>
          </div>
        )}
        {message.resolved_scope && (
          <div className="trend-section">
            <strong>Analysis scope</strong>
            <p>
              Industry: {message.resolved_scope.industry.value} ({message.resolved_scope.industry.origin});{" "}
              Geography: {message.resolved_scope.geography.value} ({message.resolved_scope.geography.origin});{" "}
              Time range: {message.resolved_scope.time_range.value} ({message.resolved_scope.time_range.origin})
            </p>
          </div>
        )}
        {message.market_trends?.length > 0 && <div className="trend-section"><strong>Market Trends</strong></div>}
        {message.market_trends?.map((finding) => (
          <article className="trend-card" key={`${finding.trend}-${finding.confidence}`}>
            <div className="trend-card-header">
              <h3>{finding.trend}</h3>
              <span className="confidence-badge">{Math.round(finding.confidence * 100)}% confidence</span>
            </div>
            <p className="trend-description">{finding.what_is_happening}</p>
            <div className="trend-tags"><span>Momentum: {finding.momentum}</span><span>Significance: {finding.significance}</span></div>
            {finding.evidence_facts?.length > 0 && <div className="trend-section"><strong>Evidence facts</strong><ul>{finding.evidence_facts.map((fact) => <li key={fact}>{fact}</li>)}</ul></div>}
            {finding.inference && <div className="trend-inference"><strong>Inference</strong><p>{finding.inference}</p></div>}
            <div className="trend-section"><strong>Importance</strong><p>{finding.importance}</p></div>
            <details className="evidence-details">
              <summary>View supporting evidence ({finding.evidence?.length || 0})</summary>
              <div className="evidence-list">{finding.evidence?.map((evidence) => <div className="evidence-item" key={evidence.chunk_id}><div className="evidence-meta"><span>{evidence.source}</span><span>{evidence.page ? `Page ${evidence.page}` : "Page unavailable"}</span><span>{evidence.chunk_id}</span><span>Score: {evidence.score ?? "Unavailable"}</span><span>Recency: {evidence.recency}</span></div><p>{evidence.text_excerpt}</p><p>Publication date: {evidence.publication_date || "Unavailable"}</p>{evidence.url ? <p><a href={evidence.url} target="_blank" rel="noreferrer">Source URL</a></p> : <p>Source URL: Unavailable</p>}<details><summary>Metadata</summary><pre>{JSON.stringify(evidence.metadata, null, 2)}</pre></details></div>)}</div>
            </details>
          </article>
        ))}
        {message.competitor_intelligence?.length > 0 && <div className="trend-section"><strong>Competitor Intelligence</strong></div>}
        {message.competitor_intelligence?.map((finding) => <article className="trend-card" key={`${finding.competitor}-${finding.activity_change}`}><div className="trend-card-header"><h3>{finding.competitor}</h3><span className="confidence-badge">{Math.round(finding.confidence * 100)}% confidence</span></div><p className="trend-description">{finding.activity_change}</p>{finding.evidence_facts?.length > 0 && <div className="trend-section"><strong>Evidence facts</strong><ul>{finding.evidence_facts.map((fact) => <li key={fact}>{fact}</li>)}</ul></div>}{finding.inference && <div className="trend-inference"><strong>Inference</strong><p>{finding.inference}</p></div>}<div className="trend-section"><strong>Importance</strong><p>{finding.importance}</p></div><p>Sources: {finding.evidence?.map((item) => item.url || item.document).join(", ")}</p></article>)}
        {[["Market Opportunities", message.market_opportunities], ["Market Risks", message.market_risks]].map(([title, findings]) => findings?.length > 0 && <div key={title}><div className="trend-section"><strong>{title}</strong></div>{findings.map((finding) => <article className="trend-card" key={`${title}-${finding.title}`}><h3>{finding.title}</h3><p className="trend-description">{finding.description}</p><p><strong>Importance:</strong> {finding.importance}</p><ul>{finding.evidence_facts?.map((fact) => <li key={fact}>{fact}</li>)}</ul></article>)}</div>)}
        {message.key_intelligence_takeaways?.length > 0 && <div className="trend-section"><strong>Key Intelligence Takeaways</strong><ul>{message.key_intelligence_takeaways.map((takeaway) => <li key={takeaway}>{takeaway}</li>)}</ul></div>}
        {message.limitations?.length > 0 && (
          <details className="analysis-notes">
            <summary>Analysis notes ({message.limitations.length})</summary>
            <ul>{message.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul>
          </details>
        )}
        {message.trace_id && <div className="trace-id">Trace {message.trace_id}</div>}
      </div>
    </div>
  );
}

function MarketStrategyMessage({ message }) {
  const renderFindings = (title, findings, includeHorizon = false) => findings?.length > 0 && (
    <div className="trend-section">
      <strong>{title}</strong>
      {findings.map((finding) => (
        <article className="trend-card" key={`${title}-${finding.title}`}>
          <div className="trend-card-header"><h3>{finding.title}</h3><span className="confidence-badge">{finding.priority} priority</span></div>
          {includeHorizon && <div className="trend-tags"><span>{finding.horizon?.replace("_", " ")}</span><span>Impact: {finding.expected_impact}</span></div>}
          <div className="trend-section"><strong>Observation</strong><p>{finding.observation}</p></div>
          <div className="trend-inference"><strong>Implication</strong><p>{finding.implication}</p></div>
          <div className="trend-section"><strong>Recommendation</strong><p>{finding.recommendation}</p></div>
          {finding.evidence?.length > 0 && <p>Supporting evidence: {finding.evidence.map((item) => item.title || item.document).join(", ")}</p>}
        </article>
      ))}
    </div>
  );
  const intelligenceSections = [
    ["Market Opportunities", message.market_opportunities, (item) => item.title, (item) => item.importance || item.description],
    ["Market Risks", message.market_risks, (item) => item.title, (item) => item.importance || item.description],
  ];
  const hasIntelligenceSnapshot = intelligenceSections.some(([, items]) => items?.length > 0) || message.key_intelligence_takeaways?.length > 0;
  const hasDetailedBreakdown = (message.recommended_priorities?.length > 0) || (message.top_opportunities?.length > 0) || (message.key_risks?.length > 0) || hasIntelligenceSnapshot || message.positioning_messaging_direction?.length > 0 || message.marketing_channel_direction?.length > 0 || message.recommended_next_actions?.length > 0 || Boolean(message.strategic_situation) || message.meeting_questions?.length > 0;

  return (
    <div className="message assistant-message market-strategy-message">
      <div className="avatar assistant-avatar">MS</div>
      <div className="message-content">
        <div className="message-label">Market Strategy Agent · {message.status}{message.reused_from_message_id && <span className="reused-badge">Reused earlier answer</span>}</div>
        <div className="answer-copy">{message.executive_summary}</div>
        {message.meeting_questions?.length > 0 && (
          <div className="trend-section" style={{ marginTop: "16px" }}>
            <strong>Additional Questions to Ask in the Meeting</strong>
            <ol>{message.meeting_questions.map((question) => <li key={question}>{question}</li>)}</ol>
          </div>
        )}
        {message.clarification_question && (
          <div className="trend-card">
            <h3>One question before I recommend a strategy</h3>
            <p className="trend-description">{message.clarification_question}</p>
            <p className="api-caption">Reply in this conversation to continue with the same market intelligence. Start a new conversation if you want different research.</p>
          </div>
        )}
        {message.sources?.length > 0 && (
          <div className="sources-block">
            <div className="section-label">Supporting Evidence / Sources</div>
            <div className="source-grid">{message.sources.map((source) => <SourceCard key={`${source.chunk_id}-${source.source}`} source={source} />)}</div>
          </div>
        )}
        {hasDetailedBreakdown && (
          <details className="evidence-details" style={{ marginTop: "16px" }}>
            <summary>View detailed strategic breakdown & additional intelligence</summary>
            <div className="strategy-detailed-content" style={{ marginTop: "12px" }}>
              {message.strategic_situation && <div className="trend-section"><strong>Strategic Situation</strong><p>{message.strategic_situation}</p></div>}
              {renderFindings("Recommended Strategic Priorities", message.recommended_priorities, true)}
              {renderFindings("Top Strategic Opportunities", message.top_opportunities)}
              {renderFindings("Key Strategic Risks", message.key_risks)}
              {[["Positioning / Messaging Direction", message.positioning_messaging_direction], ["Marketing / Channel Direction", message.marketing_channel_direction], ["Recommended Next Actions", message.recommended_next_actions], ["Key Assumptions / Uncertainties", message.assumptions_uncertainties]].map(([title, items]) => items?.length > 0 && <div className="trend-section" key={title}><strong>{title}</strong><ul>{items.map((item) => <li key={item}>{item}</li>)}</ul></div>)}
              {hasIntelligenceSnapshot && (
                <div className="trend-section" style={{ marginTop: "20px", paddingTop: "14px", borderTop: "1px solid #233e56" }}>
                  <strong>Additional Intelligence Snapshot</strong>
                  {message.market_intelligence_scope && <p className="api-caption">{message.market_intelligence_scope.industry.value} · {message.market_intelligence_scope.geography.value} · {message.market_intelligence_scope.time_range.value}</p>}
                  {intelligenceSections.map(([title, items, label, detail]) => items?.length > 0 && <article className="trend-card" key={title}><h3>{title}</h3><ul>{items.map((item, index) => <li key={`${title}-${label(item)}-${index}`}><strong>{label(item)}</strong>{detail(item) ? ` — ${detail(item)}` : ""}</li>)}</ul></article>)}
                  {message.key_intelligence_takeaways?.length > 0 && <article className="trend-card"><h3>Key Intelligence Takeaways</h3><ul>{message.key_intelligence_takeaways.map((item) => <li key={item}>{item}</li>)}</ul></article>}
                  {message.market_intelligence_summary && <details className="analysis-notes"><summary>Full Market Intelligence summary</summary><p>{message.market_intelligence_summary}</p></details>}
                </div>
              )}
            </div>
          </details>
        )}

        {/* What's Trending in the Market */}
        {message.market_trends?.length > 0 && (
          <div className="trend-section" style={{ marginTop: "22px" }}>
            <strong style={{ fontSize: "15px", letterSpacing: "0.2px" }}>What's Trending in the Market</strong>
            {message.market_trends.map((finding) => (
              <article className="trend-card" key={`${finding.trend}-${finding.confidence}`}>
                <div className="trend-card-header">
                  <h3>{finding.trend}</h3>
                  {finding.confidence !== undefined && (
                    <span className="confidence-badge">{Math.round(finding.confidence * 100)}% confidence</span>
                  )}
                </div>
                <p className="trend-description">{finding.what_is_happening}</p>
                {(finding.momentum || finding.significance) && (
                  <div className="trend-tags">
                    {finding.momentum && <span>Momentum: {finding.momentum}</span>}
                    {finding.significance && <span>Significance: {finding.significance}</span>}
                  </div>
                )}
                {finding.evidence_facts?.length > 0 && (
                  <div className="trend-section">
                    <strong>Evidence facts</strong>
                    <ul>
                      {finding.evidence_facts.map((fact) => (
                        <li key={fact}>{fact}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {finding.inference && (
                  <div className="trend-inference">
                    <strong>Inference</strong>
                    <p>{finding.inference}</p>
                  </div>
                )}
                {finding.importance && (
                  <div className="trend-section">
                    <strong>Importance</strong>
                    <p>{finding.importance}</p>
                  </div>
                )}
                {finding.evidence?.length > 0 && (
                  <p className="api-caption" style={{ marginTop: "6px" }}>
                    Sources: {finding.evidence.map((item) => item.title || item.source || item.document).join(", ")}
                  </p>
                )}
              </article>
            ))}
          </div>
        )}

        {/* Competitor Insights */}
        {message.competitor_intelligence?.length > 0 && (
          <div className="trend-section" style={{ marginTop: "22px" }}>
            <strong style={{ fontSize: "15px", letterSpacing: "0.2px" }}>Competitor Insights</strong>
            {message.competitor_intelligence.map((finding) => (
              <article className="trend-card" key={`${finding.competitor}-${finding.activity_change}`}>
                <div className="trend-card-header">
                  <h3>{finding.competitor}</h3>
                  {finding.confidence !== undefined && (
                    <span className="confidence-badge">{Math.round(finding.confidence * 100)}% confidence</span>
                  )}
                </div>
                <p className="trend-description">{finding.activity_change}</p>
                {finding.evidence_facts?.length > 0 && (
                  <div className="trend-section">
                    <strong>Evidence facts</strong>
                    <ul>
                      {finding.evidence_facts.map((fact) => (
                        <li key={fact}>{fact}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {finding.inference && (
                  <div className="trend-inference">
                    <strong>Inference</strong>
                    <p>{finding.inference}</p>
                  </div>
                )}
                {finding.importance && (
                  <div className="trend-section">
                    <strong>Strategic Importance</strong>
                    <p>{finding.importance}</p>
                  </div>
                )}
                {finding.evidence?.length > 0 && (
                  <p className="api-caption" style={{ marginTop: "6px" }}>
                    Sources: {finding.evidence.map((item) => item.title || item.source || item.document).join(", ")}
                  </p>
                )}
              </article>
            ))}
          </div>
        )}

        {message.limitations?.length > 0 && <details className="analysis-notes"><summary>Strategy notes ({message.limitations.length})</summary><ul>{message.limitations.map((item) => <li key={item}>{item}</li>)}</ul></details>}
        {message.trace_id && <div className="trace-id">Trace {message.trace_id}</div>}
      </div>
    </div>
  );
}

function MeetingPreparationMessage({ message, canCreatePresentation = false, onCreatePresentation }) {
  return (
    <div className="message assistant-message meeting-prep-message">
      <div className="avatar assistant-avatar" style={{ background: "linear-gradient(135deg, #2563eb, #7c3aed)", color: "#fff" }}>MP</div>
      <div className="message-content">
        <div className="message-label">
          Meeting Preparation Agent · {message.status}
          {message.reused_from_message_id && <span className="reused-badge">Reused earlier answer</span>}
        </div>

        {/* Meeting Header Banner */}
        <div className="meeting-prep-header-banner">
          <div className="meeting-prep-title-row">
            <span className="meeting-badge">📋 Meeting Briefing</span>
            <h2 className="meeting-title">{message.meeting_title}</h2>
            {canCreatePresentation && message.status === "completed" && (
              <button
                type="button"
                className="presentation-action-button"
                onClick={() => onCreatePresentation?.()}
              >
                Create PPT
              </button>
            )}
          </div>
          <div className="meeting-meta-row">
            <div className="meeting-meta-item">
              <strong>🎯 Objective:</strong> {message.meeting_objective}
            </div>
            {message.attendee_context && (
              <div className="meeting-meta-item">
                <strong>👥 Attendees / Counterparts:</strong> {message.attendee_context}
              </div>
            )}
            {(message.product || message.industry || message.geography || message.budget || message.key_competitors || message.timeline) && (
              <div className="meeting-meta-chips">
                {message.product && <span className="meeting-chip">📦 <strong>Product:</strong> {message.product}</span>}
                {message.industry && <span className="meeting-chip">🏢 <strong>Industry:</strong> {message.industry}</span>}
                {message.geography && <span className="meeting-chip">🌍 <strong>Geo:</strong> {message.geography}</span>}
                {message.budget && <span className="meeting-chip">💰 <strong>Budget:</strong> {message.budget}</span>}
                {message.key_competitors && <span className="meeting-chip">⚔️ <strong>Competitors:</strong> {message.key_competitors}</span>}
                {message.timeline && <span className="meeting-chip">⏱️ <strong>Timeline:</strong> {message.timeline}</span>}
              </div>
            )}
          </div>
        </div>

        {/* Executive Brief */}
        <div className="meeting-exec-brief">
          <div className="section-label">Executive Briefing Summary</div>
          <div className="answer-copy">{message.executive_brief}</div>
        </div>

        {/* Key Facts to Remember */}
        {message.key_facts_to_remember?.length > 0 && (
          <div className="trend-section meeting-facts-section">
            <strong style={{ fontSize: "15px", color: "#60a5fa" }}>💡 Key Facts to Remember</strong>
            <div className="meeting-facts-grid">
              {message.key_facts_to_remember.map((fact, index) => (
                <div className="meeting-fact-card" key={index}>
                  <span className="fact-number">0{index + 1}</span>
                  <p>{fact}</p>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Strategic Talking Points */}
        {message.strategic_talking_points?.length > 0 && (
          <div className="trend-section" style={{ marginTop: "22px" }}>
            <strong style={{ fontSize: "15px", letterSpacing: "0.2px" }}>🗣️ Strategic Talking Points</strong>
            <div className="meeting-points-list">
              {message.strategic_talking_points.map((point, index) => (
                <article className="trend-card meeting-point-card" key={index}>
                  <div className="trend-card-header">
                    <h3>{point.topic}</h3>
                    <span className="confidence-badge">Talking Point #{index + 1}</span>
                  </div>
                  <blockquote className="meeting-quote">"{point.talking_point}"</blockquote>
                  <div className="trend-inference" style={{ marginTop: "8px" }}>
                    <strong>Rationale / Strategic Backing:</strong>
                    <p>{point.rationale_or_evidence}</p>
                  </div>
                </article>
              ))}
            </div>
          </div>
        )}

        {/* Questions the CMO Should Ask */}
        {message.questions_to_ask?.length > 0 && (
          <div className="trend-section" style={{ marginTop: "22px" }}>
            <strong style={{ fontSize: "15px", color: "#34d399" }}>❓ Questions the CMO Should Ask</strong>
            <div className="meeting-questions-list">
              {message.questions_to_ask.map((q, index) => (
                <article className="trend-card" key={index}>
                  <div className="trend-card-header">
                    <span className="attendee-target-badge">Target: {q.target_attendee}</span>
                    <span className="confidence-badge" style={{ background: "#065f46", color: "#a7f3d0" }}>Probing Question</span>
                  </div>
                  <p className="meeting-question-text" style={{ fontSize: "15px", fontWeight: "500", marginTop: "6px" }}>{q.question}</p>
                  <div className="trend-section" style={{ marginTop: "6px" }}>
                    <small style={{ color: "#94a3b8" }}><strong>Strategic Intent:</strong> {q.strategic_intent}</small>
                  </div>
                </article>
              ))}
            </div>
          </div>
        )}

        {/* Risks & Things to Watch */}
        {message.risks_to_watch?.length > 0 && (
          <div className="trend-section" style={{ marginTop: "22px" }}>
            <strong style={{ fontSize: "15px", color: "#f87171" }}>⚠️ Risks & Watchouts (Attendee Skepticism / Traps)</strong>
            <div className="meeting-risks-list">
              {message.risks_to_watch.map((r, index) => (
                <article className="trend-card" key={index} style={{ borderLeft: "3px solid #ef4444" }}>
                  <div className="trend-card-header">
                    <h3 style={{ color: "#fca5a5" }}>{r.risk}</h3>
                    <span className={`confidence-badge ${r.severity === "high" ? "priority-high" : "priority-medium"}`}>
                      {r.severity} severity
                    </span>
                  </div>
                  <div className="trend-section">
                    <strong>Countermeasure / How to Respond:</strong>
                    <p>{r.countermeasure_or_watchout}</p>
                  </div>
                </article>
              ))}
            </div>
          </div>
        )}

        {/* Recommended Responses & Actions */}
        {message.recommended_actions?.length > 0 && (
          <div className="trend-section" style={{ marginTop: "22px" }}>
            <strong style={{ fontSize: "15px", color: "#fbbf24" }}>✅ Recommended Next Actions & Commitments to Secure</strong>
            <div className="meeting-actions-list">
              {message.recommended_actions.map((act, index) => (
                <div className="meeting-action-item" key={index}>
                  <div className="action-checkbox">✓</div>
                  <div className="action-content">
                    <p className="action-title" style={{ margin: "0 0 4px 0", fontWeight: "500" }}>{act.action}</p>
                    <div className="action-meta" style={{ display: "flex", gap: "14px", fontSize: "12px", color: "#94a3b8" }}>
                      <span>👤 {act.owner_or_role}</span>
                      <span>⏱️ Timing: {act.timing}</span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Relevant Market Trends */}
        {message.relevant_market_trends?.length > 0 && (
          <div className="trend-section" style={{ marginTop: "22px" }}>
            <strong style={{ fontSize: "15px", letterSpacing: "0.2px" }}>📈 Relevant Market Trends</strong>
            {message.relevant_market_trends.map((finding) => (
              <article className="trend-card" key={`${finding.trend}-${finding.confidence}`}>
                <div className="trend-card-header">
                  <h3>{finding.trend}</h3>
                  {finding.confidence !== undefined && (
                    <span className="confidence-badge">{Math.round(finding.confidence * 100)}% confidence</span>
                  )}
                </div>
                <p className="trend-description">{finding.what_is_happening}</p>
                {finding.evidence_facts?.length > 0 && (
                  <div className="trend-section">
                    <strong>Evidence Facts</strong>
                    <ul>{finding.evidence_facts.map((fact) => <li key={fact}>{fact}</li>)}</ul>
                  </div>
                )}
              </article>
            ))}
          </div>
        )}

        {/* Relevant Competitor Intelligence */}
        {message.relevant_competitor_intelligence?.length > 0 && (
          <div className="trend-section" style={{ marginTop: "22px" }}>
            <strong style={{ fontSize: "15px", letterSpacing: "0.2px" }}>🔍 Relevant Competitor Intelligence</strong>
            {message.relevant_competitor_intelligence.map((finding) => (
              <article className="trend-card" key={`${finding.competitor}-${finding.activity_change}`}>
                <div className="trend-card-header">
                  <h3>{finding.competitor}</h3>
                  {finding.confidence !== undefined && (
                    <span className="confidence-badge">{Math.round(finding.confidence * 100)}% confidence</span>
                  )}
                </div>
                <p className="trend-description">{finding.activity_change}</p>
                {finding.evidence_facts?.length > 0 && (
                  <div className="trend-section">
                    <strong>Evidence Facts</strong>
                    <ul>{finding.evidence_facts.map((fact) => <li key={fact}>{fact}</li>)}</ul>
                  </div>
                )}
              </article>
            ))}
          </div>
        )}

        {/* Supporting Evidence / Sources */}
        {message.sources?.length > 0 && (
          <div className="sources-block" style={{ marginTop: "22px" }}>
            <div className="section-label">Supporting Evidence / Sources</div>
            <div className="source-grid">
              {message.sources.map((source) => (
                <SourceCard key={`${source.chunk_id}-${source.source}`} source={source} />
              ))}
            </div>
          </div>
        )}

        {message.limitations?.length > 0 && (
          <details className="analysis-notes" style={{ marginTop: "14px" }}>
            <summary>Briefing notes ({message.limitations.length})</summary>
            <ul>{message.limitations.map((item) => <li key={item}>{item}</li>)}</ul>
          </details>
        )}
        {message.trace_id && <div className="trace-id">Trace {message.trace_id}</div>}
      </div>
    </div>
  );
}

export default function App() {
  const [accessToken, setAccessToken] = useState(() => localStorage.getItem("cmo-api-token") || "");
  const [loginUsername, setLoginUsername] = useState(() => localStorage.getItem("cmo-api-username") || "");
  const [loginPassword, setLoginPassword] = useState("");
  const [authMode, setAuthMode] = useState("signin");
  const [isLoggingIn, setIsLoggingIn] = useState(false);
  const [userId, setUserId] = useState(() => localStorage.getItem("cmo-user-id") || "");
  const [agentMode, setAgentMode] = useState("rag");
  const [isMeetingModalOpen, setIsMeetingModalOpen] = useState(false);
  const [modalTitle, setModalTitle] = useState("");
  const [modalObjective, setModalObjective] = useState("");
  const [modalAttendees, setModalAttendees] = useState("");
  const [modalProduct, setModalProduct] = useState("");
  const [modalIndustry, setModalIndustry] = useState("");
  const [modalGeography, setModalGeography] = useState("");
  const [modalBudget, setModalBudget] = useState("");
  const [modalKeyCompetitors, setModalKeyCompetitors] = useState("");
  const [modalTimeline, setModalTimeline] = useState("");
  const [attachedDoc, setAttachedDoc] = useState(null);
  const [isExtractingDoc, setIsExtractingDoc] = useState(false);
  const docInputRef = useRef(null);
  const [companyName, setCompanyName] = useState("");
  const [companyUrl, setCompanyUrl] = useState("");
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState([]);
  const [currentChatId, setCurrentChatId] = useState(() => makeId());
  const [pastChats, setPastChats] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");
  const [isPresentationModalOpen, setIsPresentationModalOpen] = useState(false);
  const [presentationTemplates, setPresentationTemplates] = useState([]);
  const [presentationTemplateId, setPresentationTemplateId] = useState("");
  const [presentationSlideCount, setPresentationSlideCount] = useState("auto");
  const [presentationStatus, setPresentationStatus] = useState("idle");
  const [presentationError, setPresentationError] = useState("");
  const [presentationTaskId, setPresentationTaskId] = useState("");
  const [presentationStage, setPresentationStage] = useState("");
  const [presentationFile, setPresentationFile] = useState(null);
  const [presentationPreviewUrl, setPresentationPreviewUrl] = useState("");
  const [presentationPreviewStatus, setPresentationPreviewStatus] = useState("idle");
  const [workspaceView, setWorkspaceView] = useState("chat");
  const [selectedTraceId, setSelectedTraceId] = useState("");
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [deletingChatId, setDeletingChatId] = useState("");
  const [showPastChatsPicker, setShowPastChatsPicker] = useState(false);
  const [branchedFromTitle, setBranchedFromTitle] = useState("");
  const [isForking, setIsForking] = useState(false);
  const [selectionPopup, setSelectionPopup] = useState(null);
  const textareaRef = useRef(null);
  const selectionButtonRef = useRef(null);

  const hasConversation = messages.length > 0;
  const canAsk = question.trim().length > 0 && userId.trim().length > 0;
  const latestCompletedMeetingId = [...messages].reverse().find(
    (message) => message.role === "assistant" && message.agent === "meeting_preparation" && message.status === "completed",
  )?.id || "";

  function handleAgentModeChange(nextMode) {
    setAgentMode(nextMode);
    if (nextMode === "meeting_preparation") {
      setIsMeetingModalOpen(true);
    }
  }

  function handleMessageMouseUp() {
    const selection = window.getSelection();
    const text = selection?.toString().trim();
    if (!text) { setSelectionPopup(null); return; }
    const anchorElement = selection.anchorNode?.nodeType === 3 ? selection.anchorNode.parentElement : selection.anchorNode;
    if (!anchorElement?.closest?.(".assistant-message")) { setSelectionPopup(null); return; }
    const rect = selection.getRangeAt(0).getBoundingClientRect();
    setSelectionPopup({ text, top: rect.top, left: rect.left + rect.width / 2 });
  }

  function replyToSelection() {
    if (!selectionPopup) return;
    const quote = selectionPopup.text.split("\n").map((line) => `> ${line}`).join("\n");
    setQuestion((current) => (current.trim() ? `${quote}\n\n${current}` : `${quote}\n\n`));
    setSelectionPopup(null);
    window.getSelection()?.removeAllRanges();
    textareaRef.current?.focus();
  }

  useEffect(() => {
    if (!selectionPopup) return undefined;
    function handleDocumentMouseDown(event) {
      if (selectionButtonRef.current?.contains(event.target)) return;
      setSelectionPopup(null);
    }
    document.addEventListener("mousedown", handleDocumentMouseDown);
    return () => document.removeEventListener("mousedown", handleDocumentMouseDown);
  }, [selectionPopup]);

  async function loadPastChats(token) {
    const chats = await listChats({ accessToken: token });
    setPastChats(chats);
    return chats;
  }

  async function openPresentationConfig() {
    if (!latestCompletedMeetingId || presentationStatus === "generating" || (isPresentationModalOpen && presentationStatus !== "error")) return;
    setIsPresentationModalOpen(true);
    setPresentationStatus("loading");
    setPresentationError("");
    setPresentationTaskId("");
    setPresentationStage("");
    setPresentationFile(null);
    if (presentationPreviewUrl) URL.revokeObjectURL(presentationPreviewUrl);
    setPresentationPreviewUrl("");
    setPresentationPreviewStatus("idle");
    try {
      const templates = await listPresentationTemplates({ accessToken });
      setPresentationTemplates(templates);
      setPresentationTemplateId((current) => (
        templates.some((template) => template.id === current) ? current : templates[0]?.id || ""
      ));
      setPresentationStatus(templates.length ? "idle" : "error");
      if (!templates.length) setPresentationError("Presenton did not return any presentation templates.");
    } catch (templateError) {
      setPresentationStatus("error");
      setPresentationError(templateError.message || "Unable to load presentation templates.");
    }
  }

  async function handleGeneratePresentation(event) {
    event.preventDefault();
    if (!presentationTemplateId || presentationStatus === "generating") return;
    setPresentationStatus("generating");
    setPresentationStage("Submitting your deck request to Presenton");
    setPresentationError("");
    try {
      const task = await generatePresentation({
        chatId: currentChatId,
        templateId: presentationTemplateId,
        slideCount: presentationSlideCount === "auto" ? null : Number(presentationSlideCount),
        accessToken,
      });
      setPresentationTaskId(task.id);
      setPresentationStage(task.message || "Presenton is building your slides");
    } catch (generationError) {
      setPresentationStatus("error");
      setPresentationError(generationError.message || "Unable to create the presentation.");
    }
  }

  useEffect(() => {
    if (presentationStatus !== "generating" || !presentationTaskId) return undefined;
    let cancelled = false;
    async function pollTask() {
      try {
        const task = await getPresentationStatus({ taskId: presentationTaskId, accessToken });
        if (cancelled) return;
        if (task.status === "error") throw new Error(task.message || "Presenton could not create this presentation.");
        if (task.status === "completed") {
          setPresentationStage("Downloading the completed PPTX");
          const file = await downloadPresentation({ taskId: presentationTaskId, accessToken });
          if (cancelled) return;
          setPresentationFile(file);
          setPresentationStatus("success");
          setPresentationStage("Presentation ready");
          return;
        }
        setPresentationStage(task.message || "Presenton is building your slides");
        window.setTimeout(pollTask, 1400);
      } catch (taskError) {
        if (!cancelled) {
          setPresentationStatus("error");
          setPresentationError(taskError.message || "Unable to create the presentation.");
        }
      }
    }
    pollTask();
    return () => { cancelled = true; };
  }, [presentationStatus, presentationTaskId, accessToken]);

  function downloadCompletedPresentation() {
    if (!presentationFile) return;
    const objectUrl = URL.createObjectURL(presentationFile.blob);
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = presentationFile.filename;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
  }

  async function openPresentationPreview() {
    if (!presentationTaskId || presentationPreviewStatus === "loading") return;
    setPresentationPreviewStatus("loading");
    try {
      const preview = await previewPresentation({ taskId: presentationTaskId, accessToken });
      if (presentationPreviewUrl) URL.revokeObjectURL(presentationPreviewUrl);
      setPresentationPreviewUrl(URL.createObjectURL(preview.blob));
      setPresentationPreviewStatus("ready");
    } catch (previewError) {
      setPresentationPreviewStatus("error");
      setPresentationError(previewError.message || "Unable to create the local preview.");
    }
  }

  function startNewChat() {
    setMessages([]);
    const nextChatId = makeId();
    setCurrentChatId(nextChatId);
    localStorage.setItem("cmo-current-chat-id", nextChatId);
    setError("");
    setQuestion("");
    setShowPastChatsPicker(false);
    setBranchedFromTitle("");
  }

  async function selectChat(chatId, token = accessToken) {
    if (!chatId || !token) return;
    try {
      const saved = await getChat({ chatId, accessToken: token });
      const loadedMessages = (saved.messages || []).map((message) =>
        message.role === "user"
          ? { id: message.id, role: "user", question: message.payload.question }
          : {
            id: message.id,
            role: "assistant",
            ...message.payload,
            traceId: message.payload.trace_id,
            ragTrace: message.payload.rag_trace,
          }
      );
      const lastAgentMessage = [...loadedMessages].reverse().find(
        (message) => message.role === "assistant" && message.agent
      );
      const hasMeetingBriefing = loadedMessages.some(
        (message) =>
          message.role === "assistant" &&
          message.agent === "meeting_preparation" &&
          ["completed", "partial"].includes(message.status),
      );
      setCurrentChatId(chatId);
      localStorage.setItem("cmo-current-chat-id", chatId);
      setMessages(loadedMessages);
      if (hasMeetingBriefing) {
        // A strategy coaching reply can follow a meeting briefing. Keep the
        // conversation anchored to meeting follow-ups when a saved chat is
        // reopened, even if that coaching reply is the latest agent message.
        setAgentMode("meeting_preparation");
      } else if (lastAgentMessage?.agent === "market_strategy" || lastAgentMessage?.agent === "market_intelligence") {
        setAgentMode("market_strategy");
      } else if (lastAgentMessage?.agent === "meeting_preparation") {
        setAgentMode("meeting_preparation");
      } else {
        setAgentMode("rag");
      }
      setWorkspaceView("chat");
      setShowPastChatsPicker(false);
      setBranchedFromTitle("");
      setError("");
    } catch (chatError) {
      setError(chatError.message || "Unable to load this conversation.");
      startNewChat();
    }
  }

  useEffect(() => {
    if (!accessToken || !userId) return;
    let isCancelled = false;

    async function initSession() {
      try {
        const chats = await loadPastChats(accessToken);
        if (isCancelled) return;
        const storedChatId = localStorage.getItem("cmo-current-chat-id");
        if (storedChatId && chats.some((c) => c.id === storedChatId)) {
          await selectChat(storedChatId, accessToken);
        } else {
          startNewChat();
        }
      } catch {
        if (!isCancelled) {
          setPastChats([]);
          startNewChat();
        }
      }
    }

    initSession();
    return () => {
      isCancelled = true;
    };
  }, [accessToken, userId]);

  async function submitLogin(event) {
    event.preventDefault();
    if (!loginUsername.trim() || !loginPassword || isLoggingIn) return;

    setError("");
    setIsLoggingIn(true);
    try {
      const result = authMode === "signup"
        ? await signup({ username: loginUsername.trim(), password: loginPassword })
        : await login({ username: loginUsername.trim(), password: loginPassword });
      setAccessToken(result.access_token);
      const authenticatedUserId = result.user_id || localStorage.getItem("cmo-user-id") || "demo-user";
      setUserId(authenticatedUserId);
      localStorage.setItem("cmo-api-token", result.access_token);
      localStorage.setItem("cmo-api-username", loginUsername.trim());
      localStorage.setItem("cmo-user-id", authenticatedUserId);
      setLoginPassword("");
    } catch (loginError) {
      setError(loginError.message || "Unable to authenticate.");
    } finally {
      setIsLoggingIn(false);
    }
  }

  function signOut() {
    setAccessToken("");
    setMessages([]);
    setPastChats([]);
    setUserId("");
    setShowPastChatsPicker(false);
    setBranchedFromTitle("");
    localStorage.removeItem("cmo-api-token");
    localStorage.removeItem("cmo-user-id");
    localStorage.removeItem("cmo-current-chat-id");
  }

  async function handleDocUpload(file) {
    if (!file) return;
    setIsExtractingDoc(true);
    setError("");
    try {
      const extracted = await extractChatDocument({ file, accessToken });
      setAttachedDoc({
        filename: extracted.filename,
        pageCount: extracted.page_count,
        extractedPage: extracted.extracted_page,
        text: extracted.text,
        chunks: extracted.chunks,
      });
    } catch (err) {
      setError(err.message || "Failed to extract text from document.");
    } finally {
      setIsExtractingDoc(false);
      if (docInputRef.current) docInputRef.current.value = "";
    }
  }

  function removeAttachedDoc() {
    setAttachedDoc(null);
    if (docInputRef.current) docInputRef.current.value = "";
  }

  async function submitQuestion(event) {
    event.preventDefault();
    const trimmedQuestion = question.trim();
    const trimmedUserId = userId.trim();
    if (!trimmedQuestion || !trimmedUserId || isLoading) return;

    setError("");
    setQuestion("");
    setMessages((current) => [
      ...current,
      { id: makeId(), role: "user", question: trimmedQuestion },
    ]);
    setIsLoading(true);

    try {
      let result;
      if (agentMode === "market_intelligence") {
        result = await analyzeMarketIntelligence({
          objective: trimmedQuestion,
          userId: trimmedUserId,
          companyName: companyName.trim(),
          companyUrl: companyUrl.trim(),
          chatId: currentChatId,
          accessToken,
          documentContext: attachedDoc?.text,
        });
      } else if (agentMode === "market_strategy") {
        result = await createMarketStrategy({
          objective: trimmedQuestion,
          userId: trimmedUserId,
          chatId: currentChatId,
          accessToken,
          documentContext: attachedDoc?.text,
        });
      } else if (agentMode === "meeting_preparation") {
        const hasMeetingBriefing = messages.some(
          (message) =>
            message.role === "assistant" &&
            message.agent === "meeting_preparation" &&
            ["completed", "partial"].includes(message.status),
        );
        if (hasMeetingBriefing) {
          result = await followUpMeeting({
            objective: trimmedQuestion,
            userId: trimmedUserId,
            chatId: currentChatId,
            accessToken,
            documentContext: attachedDoc?.text,
          });
        } else {
          result = await prepareMeeting({
            title: modalTitle.trim() || trimmedQuestion.slice(0, 80),
            objective: trimmedQuestion,
            attendeeContext: modalAttendees.trim() || undefined,
            product: modalProduct.trim() || undefined,
            industry: modalIndustry.trim() || undefined,
            geography: modalGeography.trim() || undefined,
            budget: modalBudget.trim() || undefined,
            keyCompetitors: modalKeyCompetitors.trim() || undefined,
            timeline: modalTimeline.trim() || undefined,
            companyName: companyName.trim(),
            companyUrl: companyUrl.trim(),
            userId: trimmedUserId,
            chatId: currentChatId,
            accessToken,
            documentContext: attachedDoc?.text,
          });
        }
      } else {
        result = await askQuestion({
          question: trimmedQuestion,
          userId: trimmedUserId,
          chatId: currentChatId,
          accessToken,
        });
      }
      const routedResult = result.response || result;
      const routedAgent = result.agent || routedResult.agent_name || agentMode;
      const answerMessage = agentMode === "market_intelligence" || agentMode === "market_strategy" || agentMode === "meeting_preparation"
        ? { id: makeId(), role: "assistant", agent: routedAgent, ...routedResult }
        : { id: makeId(), role: "assistant", answer: result.answer, chunks: result.chunks || [], sources: result.sources || [], traceId: result.trace_id, ragTrace: result.rag_trace, reused_from_message_id: result.reused_from_message_id };
      setMessages((current) => [...current, answerMessage]);
      if (answerMessage.ragTrace) setSelectedTraceId(answerMessage.id);
      await loadPastChats(accessToken);
    } catch (requestError) {
      setError(requestError.message || "Unable to reach the RAG API.");
    } finally {
      setIsLoading(false);
    }
  }

  async function handleMeetingFormSubmit(event) {
    event.preventDefault();
    const trimmedTitle = modalTitle.trim();
    const trimmedObjective = modalObjective.trim();
    const trimmedAttendees = modalAttendees.trim();
    const trimmedUserId = userId.trim();
    if (!trimmedTitle || !trimmedObjective || !trimmedUserId || isLoading) return;

    setIsMeetingModalOpen(false);
    setError("");
    const displayQuestion = `Meeting Prep: ${trimmedTitle} — ${trimmedObjective}`;
    setMessages((current) => [
      ...current,
      { id: makeId(), role: "user", question: displayQuestion },
    ]);
    setIsLoading(true);

    try {
      const result = await prepareMeeting({
        title: trimmedTitle,
        objective: trimmedObjective,
        attendeeContext: trimmedAttendees || undefined,
        product: modalProduct.trim() || undefined,
        industry: modalIndustry.trim() || undefined,
        geography: modalGeography.trim() || undefined,
        budget: modalBudget.trim() || undefined,
        keyCompetitors: modalKeyCompetitors.trim() || undefined,
        timeline: modalTimeline.trim() || undefined,
        companyName: companyName.trim(),
        companyUrl: companyUrl.trim(),
        userId: trimmedUserId,
        chatId: currentChatId,
        accessToken,
        documentContext: attachedDoc?.text,
      });
      const answerMessage = { id: makeId(), role: "assistant", agent: "meeting_preparation", ...result };
      setMessages((current) => [...current, answerMessage]);
      await loadPastChats(accessToken);
    } catch (requestError) {
      setError(requestError.message || "Unable to generate meeting preparation briefing.");
    } finally {
      setIsLoading(false);
    }
  }

  if (!accessToken) {
    return (
      <div className="auth-shell">
        <form className="login-card" onSubmit={submitLogin}>
          <div className="brand-mark">✦</div>
          <div className="eyebrow">CMO PLATFORM / SECURE ACCESS</div>
          <h1>{authMode === "signup" ? "Create your CMO Intelligence account" : "Sign in to CMO Intelligence"}</h1>
          <p>{authMode === "signup" ? "Choose a username and password to securely save your conversations." : "Sign in to continue with your saved conversations."}</p>
          {error && <div className="error-banner"><strong>{authMode === "signup" ? "Sign-up failed" : "Sign-in failed"}</strong><span>{error}</span></div>}
          <label>Username<input value={loginUsername} onChange={(event) => setLoginUsername(event.target.value)} autoComplete="username" /></label>
          <label>Password<input type="password" value={loginPassword} onChange={(event) => setLoginPassword(event.target.value)} autoComplete={authMode === "signup" ? "new-password" : "current-password"} minLength="8" /></label>
          <button className="send-button login-button" disabled={!loginUsername.trim() || loginPassword.length < 8 || isLoggingIn} type="submit">
            {isLoggingIn ? "Working…" : authMode === "signup" ? "Create account" : "Sign in"}
          </button>
          <button className="auth-switch" type="button" onClick={() => { setAuthMode(authMode === "signup" ? "signin" : "signup"); setError(""); }}>
            {authMode === "signup" ? "Already have an account? Sign in" : "New here? Create an account"}
          </button>
        </form>
      </div>
    );
  }

  async function handleDeleteChat(event, chatId) {
    event.stopPropagation();
    if (deletingChatId || !window.confirm("Delete this conversation? This permanently removes it and all its messages.")) return;

    setDeletingChatId(chatId);
    setError("");
    try {
      await deleteChat({ chatId, accessToken });
      setPastChats((current) => current.filter((chat) => chat.id !== chatId));
      if (chatId === currentChatId) startNewChat();
    } catch (deleteError) {
      setError(deleteError.message || "Unable to delete this conversation.");
    } finally {
      setDeletingChatId("");
    }
  }

  async function handleContinueWithChat(chat) {
    if (!chat?.id || isForking) return;
    setIsForking(true);
    setError("");
    try {
      const branched = await forkChat({ chatId: chat.id, accessToken });
      setMessages([]);
      setCurrentChatId(branched.id);
      localStorage.setItem("cmo-current-chat-id", branched.id);
      setQuestion("");
      setBranchedFromTitle(chat.title || "Previous conversation");
      setShowPastChatsPicker(false);
      await loadPastChats(accessToken);
    } catch (forkError) {
      setError(forkError.message || "Unable to continue conversation with context.");
    } finally {
      setIsForking(false);
    }
  }

  return (
    <div className={`app-shell ${isSidebarOpen ? "drawer-open" : ""}`}>
      {selectionPopup && (
        <button
          ref={selectionButtonRef}
          className="selection-reply-button"
          style={{ top: selectionPopup.top, left: selectionPopup.left }}
          onMouseDown={(event) => event.preventDefault()}
          onClick={replyToSelection}
        >
          Reply
        </button>
      )}
      <aside className={`sidebar ${isSidebarOpen ? "sidebar-open" : ""}`}>
        <div className="brand">
          <div className="brand-mark">✦</div>
          <div>
            <strong>CMO Intelligence</strong>
            <span>RAG workspace</span>
          </div>
        </div>

        <button className="new-chat-button" onClick={startNewChat}>
          <span>＋</span> New conversation
        </button>

        <div className="sidebar-section chat-history">
          <div className="sidebar-label">Past chats</div>
          {pastChats.length === 0 ? <div className="api-caption">Your saved conversations will appear here.</div> : pastChats.map((chat) => (
            <div className={`history-row ${chat.id === currentChatId ? "active" : ""}`} key={chat.id}>
              <button
                className={`nav-item history-item ${chat.id === currentChatId ? "active" : ""}`}
                onClick={() => selectChat(chat.id)}
              >
                {chat.title}
              </button>
              <button
                className="history-delete-button"
                type="button"
                title="Delete conversation"
                aria-label={`Delete conversation "${chat.title}"`}
                disabled={deletingChatId === chat.id}
                onClick={(event) => handleDeleteChat(event, chat.id)}
              >
                {deletingChatId === chat.id ? "…" : "×"}
              </button>
            </div>
          ))}
        </div>

        <div className="sidebar-section">
          <div className="sidebar-label">Workspace</div>
          <button className={`nav-item ${workspaceView === "etl" ? "active" : ""}`} onClick={() => setWorkspaceView("etl")}><span>⇧</span> Ingest documents</button>
          <button className={`nav-item ${workspaceView === "chat" ? "active" : ""}`} onClick={() => setWorkspaceView("chat")}><span>◈</span> Ask the intelligence base</button>
          <button className={`nav-item ${workspaceView === "trace" ? "active" : ""}`} onClick={() => setWorkspaceView("trace")}><span>⌁</span> RAG Trace</button>
        </div>

        <div className="sidebar-footer">
          <div className="connection-status"><span className="status-dot" /> FastAPI connected</div>
          <div className="api-caption">Answers use the existing grounded RAG pipeline.</div>
        </div>
      </aside>

      <main className="main-panel">
        <header className="topbar">
          <div>
            <div className="eyebrow">CMO PLATFORM / SHARED RAG TOOL</div>
            <h1>{workspaceView === "trace" ? "Inspect your RAG answer" : workspaceView === "etl" ? "Ingest documents" : "Ask your intelligence base"}</h1>
          </div>
          <div className="topbar-actions"><button className="menu-button" type="button" aria-label="Open saved chats" aria-expanded={isSidebarOpen} onClick={() => setIsSidebarOpen((open) => !open)}><span /><span /><span /></button><div className="topbar-badge">Agent-ready API</div><button className="sign-out-button" onClick={signOut}>Sign out</button></div>
        </header>

        {workspaceView === "trace" ? <RAGTraceWorkspace messages={messages} selectedTraceId={selectedTraceId} onSelectTrace={setSelectedTraceId} /> : workspaceView === "etl" ? <ETLWorkspace accessToken={accessToken} userId={userId} /> : <>
          <section className={`conversation ${hasConversation ? "has-messages" : "empty-conversation"}`}>
            {!hasConversation ? (
              <div className="welcome-panel">
                <div className="welcome-orbit"><span>✦</span></div>
                <div className="eyebrow">GROUNDED ANSWERS, TRACEABLE EVIDENCE</div>
                <h2>What would you like to understand?</h2>
                <p>Ask a question about your indexed CMO documents and receive a concise answer with the supporting sources used by the RAG system.</p>

                {branchedFromTitle ? (
                  <div className="context-seeded-banner">
                    <div className="context-seeded-info">
                      <span className="context-seeded-badge">↳ Context linked</span>
                      <span className="context-seeded-text">
                        Continuing with memory and context from <strong>"{branchedFromTitle}"</strong>
                      </span>
                    </div>
                    <button
                      type="button"
                      className="context-clear-button"
                      onClick={startNewChat}
                      title="Clear linked context and start fresh"
                    >
                      Start fresh
                    </button>
                  </div>
                ) : pastChats.length > 0 ? (
                  <div className="continue-context-section">
                    {!showPastChatsPicker ? (
                      <button
                        type="button"
                        className="continue-context-toggle"
                        onClick={() => setShowPastChatsPicker(true)}
                      >
                        <span>↳</span> Continue with a previous conversation
                      </button>
                    ) : (
                      <div className="past-chats-picker">
                        <div className="past-chats-picker-header">
                          <div>
                            <strong>Continue from a previous conversation</strong>
                            <span>Select one of your past 5 chats to carry its memory into this new chat</span>
                          </div>
                          <button
                            type="button"
                            className="picker-close-button"
                            onClick={() => setShowPastChatsPicker(false)}
                            aria-label="Close conversation selector"
                          >
                            ×
                          </button>
                        </div>
                        <div className="past-chats-grid">
                          {pastChats.slice(0, 5).map((chat) => (
                            <button
                              key={chat.id}
                              type="button"
                              className="past-chat-card"
                              disabled={isForking}
                              onClick={() => handleContinueWithChat(chat)}
                            >
                              <div className="past-chat-card-title">{chat.title || "Untitled conversation"}</div>
                              <div className="past-chat-card-meta">
                                <span>{chat.message_count || 0} messages</span>
                                {chat.updated_at && <span>{new Date(chat.updated_at).toLocaleDateString()}</span>}
                              </div>
                            </button>
                          ))}
                        </div>
                        {isForking && <div className="picker-loading">Cloning chat memory and context…</div>}
                      </div>
                    )}
                  </div>
                ) : null}

                <div className="suggestion-grid">
                  {suggestions.map((suggestion) => (
                    <button key={suggestion} onClick={() => setQuestion(suggestion)}>{suggestion}<span>→</span></button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="message-list" onMouseUp={handleMessageMouseUp}>
                {messages.map((message) => message.role === "user" ? (
                  <div className="message user-message" key={message.id}>
                    <div className="avatar user-avatar">You</div>
                    <div className="message-content"><div className="message-label">Your question</div><div className="question-copy">{message.question}</div></div>
                  </div>
                ) : message.agent === "market_intelligence" ? <MarketIntelligenceMessage key={message.id} message={message} /> : message.agent === "market_strategy" ? <MarketStrategyMessage key={message.id} message={message} /> : message.agent === "meeting_preparation" ? <MeetingPreparationMessage key={message.id} message={message} canCreatePresentation={message.id === latestCompletedMeetingId} onCreatePresentation={openPresentationConfig} /> : <AnswerMessage key={message.id} message={message} onInspectTrace={(messageId) => { setSelectedTraceId(messageId); setWorkspaceView("trace"); }} />)}
                {isLoading && (
                  <div className="message assistant-message">
                    <div className="avatar assistant-avatar">{agentMode === "meeting_preparation" ? "MP" : "CMO"}</div>
                    <div className="message-content"><div className="message-label">{agentMode === "meeting_preparation" ? "Meeting Preparation Agent" : "Intelligence assistant"}</div><div className="loading-line"><span /><span /><span /></div><div className="loading-caption">{agentMode === "meeting_preparation" ? "Synthesizing market research & strategic intelligence into your executive meeting brief…" : "Searching your scoped index and generating a grounded answer…"}</div></div>
                  </div>
                )}
              </div>
            )}
          </section>

          <section className="composer-area">
            {error && <div className="error-banner"><strong>Request failed</strong><span>{error}</span></div>}
            <form className="composer" onSubmit={submitQuestion}>
              {attachedDoc && (
                <div className="attached-doc-badge">
                  <span className="doc-badge-icon">📄</span>
                  <span className="doc-badge-name">{attachedDoc.filename}</span>
                  <span className="doc-badge-info">(Page 1 of {attachedDoc.pageCount} extracted)</span>
                  <button type="button" className="doc-badge-remove" onClick={removeAttachedDoc} title="Remove attached document">×</button>
                </div>
              )}
              {isExtractingDoc && (
                <div className="doc-extracting-banner">
                  <span>Extracting text from Page 1...</span>
                </div>
              )}
              <textarea
                ref={textareaRef}
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); submitQuestion(event); } }}
                placeholder={
                  agentMode === "meeting_preparation"
                    ? "Ask or update a meeting objective, or click 'Open Meeting Form' below…"
                    : agentMode === "market_strategy" && messages.at(-1)?.clarification_question
                      ? "Answer the strategy clarification to continue…"
                      : "Ask a question about your CMO documents…"
                }
                rows="2"
                disabled={isLoading}
              />
              <div className="composer-footer">
                <div className="composer-footer-actions">
                  <input
                    type="file"
                    ref={docInputRef}
                    style={{ display: 'none' }}
                    accept=".pdf,.docx,.txt,.md"
                    onChange={(e) => handleDocUpload(e.target.files?.[0])}
                  />
                  <button
                    type="button"
                    className="clip-button"
                    title="Attach document (Page 1 extracted for context)"
                    disabled={isExtractingDoc || isLoading}
                    onClick={() => docInputRef.current?.click()}
                  >
                    📎 {attachedDoc ? 'Change doc' : 'Attach doc'}
                  </button>
                  {agentMode === "meeting_preparation" && (
                    <button
                      type="button"
                      className="modal-trigger-btn"
                      onClick={() => setIsMeetingModalOpen(true)}
                      title="Open Meeting Parameters Form"
                    >
                      📋 Open Meeting Form
                    </button>
                  )}
                  <span>Enter to send · Shift + Enter for a new line</span>
                </div>
                <button className="send-button" disabled={!canAsk || isLoading || isExtractingDoc} type="submit">
                  {isLoading ? "Thinking…" : agentMode === "meeting_preparation" ? "Prepare meeting ↗" : "Ask intelligence ↗"}
                </button>
              </div>
            </form>
            <div className="agent-controls">
              <label>
                Mode{" "}
                <select value={agentMode} onChange={(event) => handleAgentModeChange(event.target.value)}>
                  <option value="rag">RAG Answer</option>
                  <option value="market_strategy">Market Strategy Agent</option>
                  <option value="meeting_preparation">Meeting Preparation Agent</option>
                </select>
              </label>
              <span className="disclaimer">
                {agentMode === "meeting_preparation"
                  ? "Meeting Preparation Agent orchestrates Market Intelligence & Strategy to build a tailored executive briefing with talking points and questions."
                  : agentMode === "market_strategy"
                    ? "Market Intelligence supplies all research and sources. Personalization uses only your saved business profile."
                    : "Answers are generated from the top 5 indexed-document results."}
              </span>
            </div>
          </section>
        </>}
      </main>

      {/* Meeting Preparation Modal Form */}
      {isMeetingModalOpen && (
        <div className="modal-overlay" onClick={() => setIsMeetingModalOpen(false)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <div className="modal-header-text">
                <div className="modal-badge">🎯 Meeting Preparation</div>
                <h2>Prepare for Executive Meeting</h2>
                <p>Generate a comprehensive CMO briefing with talking points, questions, watchouts, and market facts.</p>
              </div>
              <button
                type="button"
                className="modal-close-button"
                onClick={() => setIsMeetingModalOpen(false)}
                aria-label="Close meeting form"
              >
                ×
              </button>
            </div>

            <form onSubmit={handleMeetingFormSubmit} className="meeting-form">
              <div className="form-group">
                <label htmlFor="meeting-title">
                  Meeting Title <span className="required">*</span>
                </label>
                <input
                  id="meeting-title"
                  type="text"
                  required
                  placeholder="e.g. Executive Strategy Sync with Board & CEO"
                  value={modalTitle}
                  onChange={(e) => setModalTitle(e.target.value)}
                />
              </div>

              <div className="form-group">
                <label htmlFor="meeting-objective">
                  Meeting Objective <span className="required">*</span>
                </label>
                <textarea
                  id="meeting-objective"
                  rows="3"
                  required
                  placeholder="What is your primary goal or strategic outcome for this meeting? (e.g. Secure approval for AI marketing automation budget and omnichannel expansion)"
                  value={modalObjective}
                  onChange={(e) => setModalObjective(e.target.value)}
                />
              </div>

              <div className="form-group">
                <label htmlFor="meeting-attendees">
                  Attendee / Counterpart Context <span className="optional">(optional)</span>
                </label>
                <textarea
                  id="meeting-attendees"
                  rows="2"
                  placeholder="Who will be attending? What are their roles, known priorities, or likely objections? (e.g. CEO, CFO, VP Sales; CFO is skeptical of tech spend ROI)"
                  value={modalAttendees}
                  onChange={(e) => setModalAttendees(e.target.value)}
                />
              </div>

              <div className="form-section-divider">Optional Strategic Context</div>

              <div className="form-grid-2col">
                <div className="form-group">
                  <label htmlFor="meeting-product">
                    Product / Offering <span className="optional">(optional)</span>
                  </label>
                  <input
                    id="meeting-product"
                    type="text"
                    placeholder="e.g. Enterprise AI Suite, Cloud SaaS"
                    value={modalProduct}
                    onChange={(e) => setModalProduct(e.target.value)}
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="meeting-industry">
                    Industry / Vertical <span className="optional">(optional)</span>
                  </label>
                  <input
                    id="meeting-industry"
                    type="text"
                    placeholder="e.g. B2B SaaS, FinTech, HealthTech"
                    value={modalIndustry}
                    onChange={(e) => setModalIndustry(e.target.value)}
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="meeting-geography">
                    Geographic Location <span className="optional">(optional)</span>
                  </label>
                  <input
                    id="meeting-geography"
                    type="text"
                    placeholder="e.g. North America, EMEA, Global"
                    value={modalGeography}
                    onChange={(e) => setModalGeography(e.target.value)}
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="meeting-budget">
                    Budget / Financials <span className="optional">(optional)</span>
                  </label>
                  <input
                    id="meeting-budget"
                    type="text"
                    placeholder="e.g. $1.5M Q3 Allocation, 20% CAC drop"
                    value={modalBudget}
                    onChange={(e) => setModalBudget(e.target.value)}
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="meeting-competitors">
                    Key Competitors <span className="optional">(optional)</span>
                  </label>
                  <input
                    id="meeting-competitors"
                    type="text"
                    placeholder="e.g. Competitor A, Competitor B"
                    value={modalKeyCompetitors}
                    onChange={(e) => setModalKeyCompetitors(e.target.value)}
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="meeting-timeline">
                    Timeline / Horizon <span className="optional">(optional)</span>
                  </label>
                  <input
                    id="meeting-timeline"
                    type="text"
                    placeholder="e.g. Q4 2026, 90-Day Execution"
                    value={modalTimeline}
                    onChange={(e) => setModalTimeline(e.target.value)}
                  />
                </div>
              </div>

              {attachedDoc && (
                <div className="attached-doc-badge" style={{ marginBottom: "16px" }}>
                  <span className="doc-badge-icon">📄</span>
                  <span className="doc-badge-name">{attachedDoc.filename}</span>
                  <span className="doc-badge-info">Attached for meeting context</span>
                </div>
              )}

              <div className="modal-actions">
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() => setIsMeetingModalOpen(false)}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="send-button"
                  disabled={!modalTitle.trim() || !modalObjective.trim() || isLoading}
                >
                  {isLoading ? "Preparing brief…" : "Generate Meeting Prep ↗"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {isPresentationModalOpen && (
        <div className="modal-overlay" onClick={() => setIsPresentationModalOpen(false)}>
          <div className={`modal-card presentation-modal-card ${presentationPreviewUrl ? "presentation-preview-modal-card" : ""}`} onClick={(event) => event.stopPropagation()}>
            <div className="modal-header">
              <div className="modal-header-text">
                <div className="modal-badge">Presentation Composer</div>
                <h2>Create Presentation</h2>
                <p>Build an editable PPTX from this meeting conversation only.</p>
              </div>
              <button
                type="button"
                className="modal-close-button"
                onClick={() => setIsPresentationModalOpen(false)}
                aria-label="Close presentation form"
              >
                Ã—
              </button>
            </div>

            {presentationStatus === "loading" ? (
              <div className="presentation-status">Loading available Presenton templatesâ€¦</div>
            ) : presentationStatus === "success" ? (
              <div className="presentation-success">
                <strong>Presentation ready.</strong>
                <span>The editable PPTX was created from the current meeting conversation.</span>
                <div className="presentation-ready-actions">
                  <button type="button" className="secondary-button" onClick={downloadCompletedPresentation}>Download PPTX</button>
                  <button type="button" className="send-button" onClick={openPresentationPreview} disabled={presentationPreviewStatus === "loading"}>
                    {presentationPreviewStatus === "loading" ? "Preparing preview…" : "Preview locally"}
                  </button>
                </div>
                {presentationPreviewUrl && (
                  <iframe className="presentation-preview-frame" title="Generated presentation preview" src={`${presentationPreviewUrl}#zoom=page-width`} />
                )}
                {presentationPreviewStatus === "error" && <span className="presentation-preview-error">{presentationError}</span>}
                <button type="button" className="secondary-button" onClick={() => setIsPresentationModalOpen(false)}>Close</button>
              </div>
            ) : (
              <form onSubmit={handleGeneratePresentation} className="meeting-form presentation-form">
                <div className="form-group">
                  <label htmlFor="presentation-template">Theme / template</label>
                  <div className="template-picker" role="radiogroup" aria-label="Presentation template">
                    {presentationTemplates.map((template) => (
                      <button
                        type="button"
                        key={template.id}
                        role="radio"
                        aria-checked={presentationTemplateId === template.id}
                        className={`template-card ${presentationTemplateId === template.id ? "selected" : ""}`}
                        onClick={() => setPresentationTemplateId(template.id)}
                        disabled={presentationStatus === "generating"}
                      >
                        {template.preview_url ? <img src={template.preview_url} alt="" className="template-provider-preview" /> : (
                          <span className="template-layout-guide" style={templatePreviewStyle(template.id)}>
                            <span className="template-layout-kicker">Strategy review</span><span className="template-layout-title">Growth that compounds</span><span className="template-layout-stat">38%</span>
                          </span>
                        )}
                        <span className="template-card-name">{template.name}</span>
                        <span className="template-card-meta">{template.total_layouts ? `${template.total_layouts} layouts` : "Template"}</span>
                      </button>
                    ))}
                  </div>
                  <small className="template-preview-note">Provider thumbnails are shown when available; layout guides are illustrative.</small>
                </div>
                <div className="form-group">
                  <label htmlFor="presentation-slide-count">Slides</label>
                  <select
                    id="presentation-slide-count"
                    value={presentationSlideCount}
                    onChange={(event) => setPresentationSlideCount(event.target.value)}
                    disabled={presentationStatus === "generating"}
                  >
                    <option value="auto">Auto</option>
                    <option value="5">5</option>
                    <option value="7">7</option>
                    <option value="10">10</option>
                  </select>
                </div>
                {presentationError && (
                  <div className="error-banner presentation-error">
                    <strong>Presentation failed</strong>
                    <span>{presentationError}</span>
                  </div>
                )}
                {presentationStatus === "generating" && (
                  <div className="presentation-progress" aria-live="polite">
                    <div className="presentation-progress-heading"><strong>Creating presentation</strong><span>Stage 2 of 3</span></div>
                    <div className="presentation-progress-track"><span /></div>
                    <span>{presentationStage || "Presenton is building your slides"}</span>
                    <small>Presenton reports task stages, not a slide-by-slide percentage.</small>
                  </div>
                )}
                <div className="modal-actions">
                  {presentationStatus === "error" && presentationTemplates.length === 0 && (
                    <button type="button" className="secondary-button" onClick={openPresentationConfig}>Retry templates</button>
                  )}
                  <button type="button" className="secondary-button" onClick={() => setIsPresentationModalOpen(false)}>Cancel</button>
                  <button type="submit" className="send-button" disabled={!presentationTemplateId || presentationStatus === "generating"}>
                    {presentationStatus === "generating" ? "Generating PPTXâ€¦" : "Generate Presentation â†—"}
                  </button>
                </div>
              </form>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
