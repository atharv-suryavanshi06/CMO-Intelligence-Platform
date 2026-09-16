"""
Batch RAGAS evaluation runner
=================================

Offline RAGAS evaluation script for the Multimodal PDF RAG system.

Loads the hand-built ground truth dataset (evaluation/ground_truth.json),
runs each question through the existing RAG pipeline (via ask_rag),
scores every question with RAGAS, and writes two artifacts per evaluator
provider:

- evaluation/results_<provider>.csv           one row per question
- evaluation/evaluation_report_<provider>.md  human-readable summary report

Design notes:
- This script is intentionally a SEPARATE, OFFLINE process - it is not
  called from the live chatbot response path. Several of the metrics
  used here (context_recall, answer_correctness) require a ground truth
  reference answer, which does not exist for a live user query, so they
  are mathematically uncomputable outside an offline eval run like this
  one. Run this script after pipeline changes or on a schedule, not
  per-request.
- `ask_rag` is UNCHANGED and keeps its original interface:
  `answer, retrieved_context = ask_rag(question)`. Retrieval and
  generation logic are not modified anywhere in this file.
- `ask_rag_timed` is a separate wrapper added purely to measure
  wall-clock latency around the same retrieve()/generate_answer() calls
  that ask_rag() already makes. ask_rag() itself is left untouched.

EVALUATOR PROVIDER
- Controlled entirely by the EVALUATOR_PROVIDER environment variable in
  .env: "ollama" (default) or "groq". No code edits needed to switch -
  see resolve_evaluator_provider() and build_ragas_llm_and_embeddings().
- Output files are provider-suffixed (results_ollama.csv vs
  results_groq.csv, etc.) so switching providers can never overwrite or
  merge into the other provider's results - they're independent files
  meant to be compared side by side.
- Ollama requires a local server ('ollama serve') with qwen2.5:7b
  pulled. Groq requires GROQ_API_KEY in .env (free key:
  https://console.groq.com/keys). Both are validated with a fast
  connectivity check before the evaluation loop starts, so a bad
  key/unreachable server fails immediately with a clear message instead
  of RAGAS silently retrying every sample into NaN scores.

RESUMABILITY
- Evaluation runs one question at a time. Before the loop starts,
  the active provider's results CSV (if present) is read to find which
  question ids are already done; those are skipped. Each question's row
  is appended immediately after it's scored, and the report is
  regenerated from the cumulative CSV after every question - so an
  interrupted run leaves completed work safely on disk and a report
  reflecting exactly what's done so far.

Required evaluation dependencies are declared in ``requirements.txt``:
    ragas==0.3.5
    datasets==5.0.0

The live ingestion stack uses ``langchain-text-splitters`` 1.x. Let RAGAS
resolve compatible LangChain wrappers alongside that stack; do not force the
historical 0.3 LangChain pins, which conflict with the active dependency.
Provider adapters such as ``langchain-groq`` and ``langchain-ollama`` remain
optional and are required only when running their respective live evaluators.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from dotenv import load_dotenv
from tqdm import tqdm

# tenacity powers the exponential-backoff retry around ask_rag() calls.
# It is already a transitive dependency of ragas, so no new install is
# required, but it is listed explicitly in requirements for clarity.
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

# google-api-core ships as a dependency of the Gemini SDKs and gives us a
# precise exception type for HTTP 429 / RESOURCE_EXHAUSTED responses,
# instead of string-matching alone. This retry wrapper guards the RAG
# pipeline's own answer-generation LLM call (ask_rag_timed), which is
# unrelated to which RAGAS evaluator provider is active below.
from google.api_core.exceptions import ResourceExhausted, TooManyRequests

from multimodal_rag.paths import (
    EVALUATION_ARTIFACTS_DIR,
    GROUND_TRUTH_PATH as NEW_GROUND_TRUTH_PATH,
    INDEX_DIR,
    LEGACY_EVALUATION_DIR,
    LEGACY_GROUND_TRUTH_PATH,
    LEGACY_INDEX_DIR,
    prefer_new_path,
)

# --------------------------------------------------------------------------
# Environment / logging setup
# --------------------------------------------------------------------------

load_dotenv()  # picks up GEMINI_API_KEY, GROQ_API_KEY, EVALUATOR_PROVIDER, etc. from .env

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Evaluator provider selection
# --------------------------------------------------------------------------

SUPPORTED_EVALUATOR_PROVIDERS = {"ollama", "groq"}
DEFAULT_EVALUATOR_PROVIDER = "ollama"

OLLAMA_MODEL_NAME = "qwen2.5:7b"
GROQ_MODEL_NAME = os.getenv(
    "GROQ_MODEL",
    "openai/gpt-oss-20b"
)
GROQ_FAITHFULNESS_MAX_TOKENS = 4096
GROQ_ANSWER_CORRECTNESS_MAX_TOKENS = 4096


def resolve_evaluator_provider() -> str:
    """
    Read EVALUATOR_PROVIDER from the environment and validate it. Missing
    or unrecognized values fall back to Ollama with a warning rather than
    raising - a typo'd env var shouldn't block you from running an
    evaluation, and Ollama (local, no API key, no quota) is the safer
    default to fall back to.
    """
    raw = os.getenv("EVALUATOR_PROVIDER")
    if not raw or not raw.strip():
        logger.warning(
            "EVALUATOR_PROVIDER is not set in .env - defaulting to '%s'. "
            "Set EVALUATOR_PROVIDER=groq or EVALUATOR_PROVIDER=ollama to choose explicitly.",
            DEFAULT_EVALUATOR_PROVIDER,
        )
        return DEFAULT_EVALUATOR_PROVIDER

    provider = raw.strip().lower()
    if provider not in SUPPORTED_EVALUATOR_PROVIDERS:
        logger.warning(
            "EVALUATOR_PROVIDER=%r is not recognized (supported: %s) - defaulting to '%s'.",
            raw, sorted(SUPPORTED_EVALUATOR_PROVIDERS), DEFAULT_EVALUATOR_PROVIDER,
        )
        return DEFAULT_EVALUATOR_PROVIDER

    return provider


EVALUATOR_PROVIDER = resolve_evaluator_provider()

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

EVAL_DIR = prefer_new_path(EVALUATION_ARTIFACTS_DIR, LEGACY_EVALUATION_DIR)
GROUND_TRUTH_PATH = prefer_new_path(NEW_GROUND_TRUTH_PATH, LEGACY_GROUND_TRUTH_PATH)

# Provider-suffixed output paths: switching EVALUATOR_PROVIDER can never
# overwrite, resume-into, or merge with the other provider's results -
# each provider gets its own independent file, meant to be compared
# side by side.
RESULTS_CSV_PATH = prefer_new_path(
    EVALUATION_ARTIFACTS_DIR / f"results_{EVALUATOR_PROVIDER}.csv",
    LEGACY_EVALUATION_DIR / f"results_{EVALUATOR_PROVIDER}.csv",
)
REPORT_PATH = prefer_new_path(
    EVALUATION_ARTIFACTS_DIR / f"evaluation_report_{EVALUATOR_PROVIDER}.md",
    LEGACY_EVALUATION_DIR / f"evaluation_report_{EVALUATOR_PROVIDER}.md",
)

# Pre-provider-suffix legacy filenames. Only used for a one-time,
# read-only heads-up notice in main() - never read from or written to
# automatically. See main()'s startup check.
_LEGACY_RESULTS_CSV_PATH = LEGACY_EVALUATION_DIR / "results.csv"
_LEGACY_REPORT_PATH = LEGACY_EVALUATION_DIR / "evaluation_report.md"

# Column order for results.csv. All of the original columns are preserved;
# latency and token/cost columns are appended at the end so existing
# downstream consumers of the CSV that read columns by position for the
# first block are unaffected.
RESULT_COLUMNS = [
    "id",
    "question",
    "generated_answer",
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
    "answer_correctness",
    "retrieval_latency_ms",
    "generation_latency_ms",
    "total_latency_ms",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "estimated_cost",
]

METRIC_COLUMNS = [
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
    "answer_correctness",
]

LATENCY_COLUMNS = ["retrieval_latency_ms", "generation_latency_ms", "total_latency_ms"]


def get_completed_ids(path: Path = RESULTS_CSV_PATH) -> set[str]:
    """
    Read already-completed question ids from an existing results CSV (if
    any) so a resumed run can skip them. Ids are compared as strings since
    ground-truth ids and CSV-round-tripped ids may differ in dtype (int vs
    str) after a save/reload cycle.
    """
    if not path.exists():
        return set()
    try:
        existing_df = pd.read_csv(path)
    except Exception as e:
        logger.warning("Could not read existing results at %s (%s) - starting fresh.", path, e)
        return set()
    if "id" not in existing_df.columns:
        return set()
    return set(existing_df["id"].astype(str))


# --------------------------------------------------------------------------
# Evaluator pricing (for cost estimation - see run_ragas_evaluation)
# --------------------------------------------------------------------------
# Ollama runs locally, so it has no entry here - its cost is always $0.00
# and is never looked up in this table.
#
# Groq's GPT-OSS-20B list price as of July 2026: $0.075 / $0.30 per
# million input/output tokens. Groq updates pricing periodically -
# check https://groq.com/pricing if the estimated cost in your reports
# looks off, and update the single tuple below.
PROVIDER_PRICING = {
    "groq": {
        GROQ_MODEL_NAME: (0.075 / 1_000_000, 0.30 / 1_000_000),  # (cost_per_input_token, cost_per_output_token)
    },
}


# --------------------------------------------------------------------------
# RAG pipeline
# --------------------------------------------------------------------------

from multimodal_rag.rag.generation.answer_generator import (
    GenerationConfig,
    generate_answer,
    generate_answer_with_metadata,
)

from multimodal_rag.rag.generation.citation import resolve_citations
from multimodal_rag.rag.generation.prompt_builder import build_prompt
from multimodal_rag.rag.indexing.chroma_index import load_index
from multimodal_rag.rag.retrieval.retriever_2 import RetrieverConfig, retrieve

# Load the index on first use so importing evaluation helpers does not require
# a rebuilt local Chroma collection.
INDEX = None
ID_MAP = None


def _get_index():
    global INDEX, ID_MAP
    if INDEX is None or ID_MAP is None:
        INDEX, ID_MAP = load_index(str(prefer_new_path(INDEX_DIR, LEGACY_INDEX_DIR)))
    return INDEX, ID_MAP


def ask_rag(question: str) -> tuple[str, list[str]]:
    """
    Original, unmodified pipeline entry point. Retrieval and generation
    logic are untouched.

    Returns:
        answer (str)
        retrieved_context (list[str])
    """

    index, id_map = _get_index()
    chunks = retrieve(
        question,
        index,
        id_map,
        retriever_config=RetrieverConfig(top_k=8),
    )

    if not chunks:
        return "", []

    built = build_prompt(question, chunks)

    raw_answer = generate_answer(
        built.prompt_text,
        GenerationConfig(),
    )

    result = resolve_citations(raw_answer, built.source_map)

    # RAGAS expects plain text chunks
    retrieved_context = [
        f"Page {chunk.page_numbers}: {chunk.chunk_text}"
        for chunk in chunks
    ]

    return result.answer_text, retrieved_context


@dataclass
class TimedRagResult:
    answer: str
    retrieved_context: list[str]
    retrieved_chunk_ids: list[str]
    retrieval_latency_ms: float
    generation_latency_ms: float
    total_latency_ms: float


def ask_rag_timed(question: str, top_k: int = 8) -> TimedRagResult:
    """
    Latency-instrumented wrapper that calls the exact same retrieve() and
    generate_answer() functions ask_rag() calls, only adding perf_counter
    timers around each stage. No retrieval or generation logic is changed
    here - this function exists solely so the evaluation runner can report
    retrieval vs. generation vs. total latency per question, without
    altering ask_rag()'s own signature/behavior for other callers.
    """
    t_start = time.perf_counter()

    t_retrieval_start = time.perf_counter()
    index, id_map = _get_index()
    chunks = retrieve(
        question,
        index,
        id_map,
        retriever_config=RetrieverConfig(top_k=top_k),
    )
    retrieval_latency_ms = (time.perf_counter() - t_retrieval_start) * 1000

    if not chunks:
        total_latency_ms = (time.perf_counter() - t_start) * 1000
        return TimedRagResult("", [], [], retrieval_latency_ms, 0.0, total_latency_ms)

    built = build_prompt(question, chunks)

    t_generation_start = time.perf_counter()
    raw_answer = generate_answer(
        built.prompt_text,
        GenerationConfig(),
    )
    generation_latency_ms = (time.perf_counter() - t_generation_start) * 1000

    result = resolve_citations(raw_answer, built.source_map)

    retrieved_context = [
        f"Page {chunk.page_numbers}: {chunk.chunk_text}"
        for chunk in chunks
    ]

    total_latency_ms = (time.perf_counter() - t_start) * 1000

    return TimedRagResult(
        result.answer_text,
        retrieved_context,
        [chunk.chunk_id for chunk in chunks],
        retrieval_latency_ms,
        generation_latency_ms,
        total_latency_ms,
    )


# --------------------------------------------------------------------------
# Gemini rate-limit retry handling (RAG pipeline's own generation LLM -
# unrelated to which RAGAS evaluator provider is active)
# --------------------------------------------------------------------------

def _is_rate_limit_error(exc: BaseException) -> bool:
    """
    True for Gemini free-tier rate limiting: HTTP 429 / RESOURCE_EXHAUSTED.
    Checks the typed google-api-core exception first, then falls back to
    matching the error text, since some transports surface the same
    condition as a generic exception with the code embedded in the message.
    """
    if isinstance(exc, (ResourceExhausted, TooManyRequests)):
        return True
    message = str(exc)
    return "429" in message or "RESOURCE_EXHAUSTED" in message


@retry(
    retry=retry_if_exception(_is_rate_limit_error),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    stop=stop_after_attempt(6),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def _ask_rag_with_backoff(question: str) -> TimedRagResult:
    """
    Calls ask_rag_timed() with exponential backoff specifically for Gemini
    rate-limit errors (429 / RESOURCE_EXHAUSTED). Up to 6 attempts, waiting
    2s, 4s, 8s, 16s, 32s, 60s (capped) between tries. Any non-rate-limit
    exception propagates immediately - only capacity errors are retried
    here, so a real bug in the pipeline still fails fast and gets logged
    per-question below rather than being retried pointlessly.
    """
    return ask_rag_timed(question)


# --------------------------------------------------------------------------
# Ground truth loading
# --------------------------------------------------------------------------

def load_ground_truth(path: Path = GROUND_TRUTH_PATH) -> list[dict[str, Any]]:
    """
    Load and lightly validate the ground truth dataset. Fails loudly on a
    missing file or malformed JSON rather than silently evaluating an
    empty/partial set - a bad ground truth load should stop the run, not
    produce a misleadingly small report.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Ground truth file not found at '{path}'. Expected the schema "
            f"produced for this project's evaluation dataset."
        )

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Ground truth file at '{path}' is not valid JSON: {e}") from e

    if not isinstance(data, list) or not data:
        raise ValueError(f"Ground truth file at '{path}' must contain a non-empty JSON list.")

    required_fields = {"id", "question", "ground_truth"}
    for i, entry in enumerate(data):
        missing = required_fields - entry.keys()
        if missing:
            raise ValueError(f"Ground truth entry at index {i} is missing required field(s): {missing}")

    logger.info("Loaded %d ground truth question(s) from %s", len(data), path)
    return data


# --------------------------------------------------------------------------
# Run the RAG pipeline over the dataset
# --------------------------------------------------------------------------

def run_rag_on_dataset(ground_truth: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Call ask_rag() (via the timed + rate-limit-retrying wrapper) for every
    question in the ground truth set, collecting the generated answer,
    retrieved context, and per-stage latency alongside each entry.

    A failure on one question (pipeline exception, timeout, exhausted
    retries, etc.) is logged and recorded with empty answer/context rather
    than crashing the whole run - one bad question shouldn't cost you the
    other 24 results. Failed rows are still written to the CSV/report so
    they're visible, not silently dropped.
    """
    records: list[dict[str, Any]] = []

    for entry in tqdm(ground_truth, desc="Running RAG pipeline", unit="question"):
        question = entry["question"]
        try:
            timed_result = _ask_rag_with_backoff(question)
            answer = timed_result.answer
            retrieved_context = timed_result.retrieved_context
            retrieval_latency_ms = timed_result.retrieval_latency_ms
            generation_latency_ms = timed_result.generation_latency_ms
            total_latency_ms = timed_result.total_latency_ms
        except Exception as e:
            logger.error("ask_rag() failed for question id=%s: %s", entry.get("id"), e)
            answer = ""
            retrieved_context = []
            retrieval_latency_ms = None
            generation_latency_ms = None
            total_latency_ms = None

        # Defensive normalization: RAGAS expects `contexts` as a list of
        # strings. If the pipeline returns something else (a single
        # string, None, etc.), coerce it rather than letting evaluate()
        # fail deep inside the RAGAS library with an opaque error.
        if retrieved_context is None:
            retrieved_context = []
        elif isinstance(retrieved_context, str):
            retrieved_context = [retrieved_context]

        records.append({
            "id": entry["id"],
            "question": question,
            "answer": answer or "",
            "contexts": retrieved_context,
            "ground_truth": entry["ground_truth"],
            "retrieval_latency_ms": retrieval_latency_ms,
            "generation_latency_ms": generation_latency_ms,
            "total_latency_ms": total_latency_ms,
        })

    return records


# --------------------------------------------------------------------------
# RAGAS evaluator - provider factory
# --------------------------------------------------------------------------

def _build_ollama_llm_and_embeddings():
    """
    Ollama (qwen2.5:7b) + Gemini Embedding 2 embeddings. Runs a fast
    connectivity check before returning, so a stopped Ollama server or a
    model that was never pulled fails immediately with a clear message
    instead of failing deep inside RAGAS's per-sample retry logic 25
    questions later.
    """
    try:
        from langchain_ollama import ChatOllama
        from ragas.llms import LangchainLLMWrapper
        from ragas.embeddings import LangchainEmbeddingsWrapper
        from multimodal_rag.rag.embedding.embedder import GeminiEmbeddings
    except ImportError as e:
        raise ImportError(
            "Missing dependency for Ollama-backed RAGAS evaluation. Install with:\n"
            "  pip install langchain-ollama==0.3.10 langchain-huggingface==0.3.1\n"
            "(Both pinned deliberately - the latest langchain-ollama and the 1.x "
            "line of langchain-huggingface both require langchain-core>=1.x, same "
            "conflict as langchain-groq. langchain-ollama==0.3.10 and "
            "langchain-huggingface==0.3.1 both resolve cleanly against "
            "langchain-core==0.3.86 - verified.)\n"
            f"Original error: {e}"
        ) from e

    chat_model = ChatOllama(model=OLLAMA_MODEL_NAME, temperature=0, keep_alive="30m")

    try:
        chat_model.invoke("Connectivity check - reply with OK.")
    except Exception as e:
        raise ConnectionError(
            f"Could not reach the local Ollama server for model '{OLLAMA_MODEL_NAME}'. "
            f"Is Ollama running ('ollama serve') and is the model pulled "
            f"('ollama pull {OLLAMA_MODEL_NAME}')? Original error: {e}"
        ) from e

    embedding_model = GeminiEmbeddings()
    # NOTE on the other deprecation warning (ragas.embeddings.LangchainEmbeddingsWrapper):
    # deliberately NOT modernized here. On ragas==0.3.5 (the pinned version)
    # this only emits a DeprecationWarning - it is not broken and the
    # migration guide confirms it "still works" through the whole 0.3.x
    # line. The replacement is ragas's native, non-LangChain embedding
    # provider classes, which are a v0.4 concept with a different method
    # surface (embed_text/embed_texts instead of embed_query/
    # embed_documents) and were still being finalized upstream as of this
    # pass - swapping to them now would be a bigger behavioral change than
    # "fix a warning" and risks silently altering how answer_relevancy's
    # cosine-similarity step embeds text, which is explicitly out of scope
    # ("do not change the evaluation workflow"). Recommendation: revisit
    # this specific wrapper only as part of a deliberate, tested ragas 0.3
    # -> 0.4 upgrade, not as a drive-by warning fix.
    return LangchainLLMWrapper(chat_model), LangchainEmbeddingsWrapper(embedding_model)


def _build_groq_llm_and_embeddings():
    """
    Groq (GPT-OSS-20B) + Gemini Embedding 2 embeddings. Requires
    GROQ_API_KEY in .env. Runs the same kind of fast connectivity check
    as the Ollama path, but with Groq-specific error categorization
    (auth vs. connection vs. rate-limit) so a startup problem is
    immediately actionable instead of surfacing as 25 questions' worth
    of NaN metrics.

    The Gemini embedding provider is shared regardless of which judge LLM
    is active - only the evaluator LLM changes between providers.
    """
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "EVALUATOR_PROVIDER=groq but GROQ_API_KEY is not set.\n"
            "Add it to your .env file:\n"
            "  GROQ_API_KEY=your_key_here\n"
            "Get a free key at https://console.groq.com/keys"
        )

    try:
        from langchain_groq import ChatGroq
        from ragas.llms import LangchainLLMWrapper
        from ragas.embeddings import LangchainEmbeddingsWrapper
        from multimodal_rag.rag.embedding.embedder import GeminiEmbeddings
        import groq as groq_sdk
    except ImportError as e:
        raise ImportError(
            "Missing dependency for Groq-backed RAGAS evaluation. Install with:\n"
            "  pip install langchain-groq==0.2.4 langchain-huggingface==0.3.1\n"
            "(Both pinned deliberately - the latest langchain-groq requires "
            "langchain-core>=1.4.0, and the 1.x line of langchain-huggingface has "
            "the same requirement, both conflicting with langchain-core==0.3.86 "
            "already required by this project's langchain-google-genai/langchain "
            "pins. langchain-groq==0.2.4 and langchain-huggingface==0.3.1 both "
            "resolve cleanly against the existing pins - verified.)\n"
            f"Original error: {e}"
        ) from e

    # max_retries here is passed straight through to the underlying `groq`
    # Python SDK client (langchain_groq.ChatGroq is a thin wrapper around
    # it). That SDK already retries 429 / connection errors / 5xx with
    # exponential backoff AND - unlike a generic retry wrapper - it reads
    # Groq's `Retry-After` response header when present and waits exactly
    # that long instead of guessing. That is a more accurate response to a
    # real TPM/RPM rate limit than a fixed backoff schedule, and it costs
    # nothing on the success path: it only activates once a 429/5xx is
    # actually returned, so normal (non-rate-limited) runs are not slowed
    # down at all. The SDK default is 2 retries; raised to 5 here because a
    # 25-question run makes enough Groq calls (each question runs 4-5
    # metrics, each 1 LLM call after the fix below) that transient
    # rate-limit windows are expected, not exceptional, over a full run.
    # This sits underneath - and is a more precise complement to - the
    # existing RunConfig(max_retries=3, max_wait=120) below, which is
    # RAGAS's own generic per-sample safety net for whichever provider is
    # active and is left unchanged.
    chat_model = ChatGroq(
        model=GROQ_MODEL_NAME,
        api_key=api_key,
        temperature=0,
        max_retries=5,
    )

    try:
        chat_model.invoke("Connectivity check - reply with OK.")
    except groq_sdk.AuthenticationError as e:
        raise EnvironmentError(
            "Groq rejected GROQ_API_KEY (authentication failed). Double-check the "
            "key in your .env file - get a fresh one at "
            f"https://console.groq.com/keys. Original error: {e}"
        ) from e
    except groq_sdk.RateLimitError as e:
        raise RuntimeError(
            "Groq's free-tier rate limit was already hit before evaluation even "
            "started. Wait a few minutes and retry, or run this evaluation with "
            f"EVALUATOR_PROVIDER=ollama instead. Original error: {e}"
        ) from e
    except groq_sdk.APIConnectionError as e:
        raise ConnectionError(
            f"Could not reach the Groq API (network/connectivity issue): {e}"
        ) from e
    except groq_sdk.GroqError as e:
        raise RuntimeError(f"Groq API error during connectivity check: {e}") from e

    embedding_model = GeminiEmbeddings()
    return LangchainLLMWrapper(chat_model), LangchainEmbeddingsWrapper(embedding_model)


_EVALUATOR_PROVIDER_BUILDERS = {
    "ollama": _build_ollama_llm_and_embeddings,
    "groq": _build_groq_llm_and_embeddings,
}


def build_ragas_llm_and_embeddings():
    """
    Single entry point for constructing the RAGAS judge LLM + embeddings,
    dispatched by EVALUATOR_PROVIDER. Everything else in this file calls
    this one function and doesn't need to know which provider is active.
    Adding a third provider later means adding one more builder function
    and one more entry in _EVALUATOR_PROVIDER_BUILDERS - nothing else
    in this file changes.
    """
    builder = _EVALUATOR_PROVIDER_BUILDERS[EVALUATOR_PROVIDER]  # EVALUATOR_PROVIDER already validated
    return builder()


def evaluator_token_usage_parser(llm_result) -> "TokenUsage":
    """
    RAGAS TokenUsageParser for whichever evaluator provider is active.
    RAGAS ships built-in parsers for OpenAI/Anthropic/Bedrock only
    (ragas.cost); this covers both Ollama and Groq using the standard
    LangChain `usage_metadata` field that both ChatOllama and ChatGroq
    populate on every AIMessage - verified to exist on both wrappers, so
    one implementation serves both providers without branching.

    If usage metadata isn't present on a given generation, it's simply
    skipped rather than raising - this parser degrades to reporting zero
    tokens instead of breaking the evaluation run, per the "skip
    gracefully" requirement.
    """
    from ragas.cost import TokenUsage

    input_tokens = 0
    output_tokens = 0
    for generation_list in llm_result.generations:
        for generation in generation_list:
            message = getattr(generation, "message", None)
            usage = getattr(message, "usage_metadata", None) if message else None
            if usage:
                input_tokens += usage.get("input_tokens", 0) or 0
                output_tokens += usage.get("output_tokens", 0) or 0

    active_model = OLLAMA_MODEL_NAME if EVALUATOR_PROVIDER == "ollama" else GROQ_MODEL_NAME
    return TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens, model=active_model)


def _compute_run_cost(total_tokens) -> tuple[Optional[float], bool]:
    """
    Cost estimation for one evaluate() call's token usage, provider-aware:
    - Ollama is local inference - cost is always $0.00 when tokens were
      captured at all (no pricing lookup needed, nothing to fail).
    - Groq is looked up in PROVIDER_PRICING by the active model. If the
      model isn't in the table (e.g. GROQ_MODEL_NAME was changed without
      updating pricing), this logs a warning and reports cost as
      unavailable rather than silently reporting $0.00, which would be
      misleading for a paid API.
    Returns (total_cost_or_None, token_usage_available).
    """
    if total_tokens is None or not (total_tokens.input_tokens or total_tokens.output_tokens):
        return None, False

    if EVALUATOR_PROVIDER == "ollama":
        return 0.0, True

    pricing = PROVIDER_PRICING.get(EVALUATOR_PROVIDER, {}).get(GROQ_MODEL_NAME)
    if not pricing:
        logger.warning(
            "No pricing entry for provider=%s model=%s - cost will be reported as unavailable "
            "(token counts are still captured). Add an entry to PROVIDER_PRICING to fix this.",
            EVALUATOR_PROVIDER, GROQ_MODEL_NAME,
        )
        return None, True  # tokens ARE available, just not cost

    cost_per_input_token, cost_per_output_token = pricing
    total_cost = (
        total_tokens.input_tokens * cost_per_input_token
        + total_tokens.output_tokens * cost_per_output_token
    )
    return total_cost, True


@dataclass
class RagasRunOutcome:
    scores_df: pd.DataFrame
    total_tokens: Optional[object] = None  # ragas.cost.TokenUsage, kept loosely typed to avoid a hard import here
    total_cost: Optional[float] = None
    token_usage_available: bool = False


def run_ragas_evaluation(
    records: list[dict[str, Any]],
    ragas_llm=None,
    ragas_embeddings=None,
) -> RagasRunOutcome:
    """
    Run the five RAGAS metrics (Faithfulness, Answer Relevancy,
    Context Precision, Context Recall, Answer Correctness) over the
    collected records and return a DataFrame with one row per question,
    plus aggregate token/cost totals for the evaluation run when available.

    ragas_llm / ragas_embeddings can be passed in to reuse an
    already-built model (Ollama load + HuggingFace embeddings load, or a
    validated Groq client, are all relatively expensive to construct)
    instead of rebuilding them on every call. If omitted, they're built
    here via build_ragas_llm_and_embeddings() - fully backward compatible.
    """
    try:
        from ragas import evaluate, EvaluationDataset, SingleTurnSample
        from ragas.metrics import (
            faithfulness,
            Faithfulness,
            answer_relevancy,
            AnswerRelevancy,
            context_precision,
            context_recall,
            answer_correctness,
            AnswerCorrectness,
        )
        from ragas.llms import LangchainLLMWrapper
        from ragas.run_config import RunConfig
    except ImportError as e:
        raise ImportError(
            "RAGAS is not installed. Install with:\n"
            "  pip install ragas\n"
            f"Original error: {e}"
        ) from e

    if ragas_llm is None or ragas_embeddings is None:
        ragas_llm, ragas_embeddings = build_ragas_llm_and_embeddings()

    # EvaluationDataset + SingleTurnSample is the current, non-deprecated
    # way to hand data to evaluate() - the older pattern of building a
    # HuggingFace `datasets.Dataset` and passing it directly was superseded
    # back in ragas 0.2.
    samples = [
        SingleTurnSample(
            user_input=r["question"],
            response=r["answer"],
            retrieved_contexts=r["contexts"],
            reference=r["ground_truth"],
        )
        for r in records
    ]
    eval_dataset = EvaluationDataset(samples=samples)

    # --- Root cause of "'n' : number must be at most 1" and of
    # answer_relevancy always being N/A (these are the SAME bug) ---
    #
    # RAGAS's answer_relevancy metric (ragas.metrics._answer_relevance.
    # ResponseRelevancy._ascore) reverse-engineers `self.strictness`
    # (default 3) candidate questions from the generated answer, then
    # scores relevancy as the average cosine similarity between those
    # candidate questions and the real question. To get all `strictness`
    # candidates in one round trip, it calls the LLM wrapper's .generate()
    # with n=self.strictness - i.e. it asks the underlying chat completion
    # API for 3 completions from a single request via the `n` parameter.
    #
    # Every other metric used here (faithfulness, context_precision,
    # context_recall, answer_correctness) always calls the LLM with n=1
    # (single completion per request, even when they issue several
    # sequential requests) - none of them ever ask for multiple
    # completions in one call. That is exactly why answer_relevancy is the
    # ONLY metric that fails: it is the only one that ever sends n>1 to
    # the LLM.
    #
    # OpenAI's API tolerates n>1 (that's a legitimate multi-completion
    # request). Groq's Chat Completions API does not: per Groq's own API
    # reference, "How many chat completion choices to generate for each
    # input message. Note that at the current moment, only n=1 is
    # supported. Other values will result in a 400 response." That 400 -
    # "'n' : number must be at most 1" - is Groq rejecting exactly this
    # n=3 request, every single time answer_relevancy runs. It then
    # exhausts RAGAS's per-sample retries (all failing the same way, since
    # it's not transient) and the score is recorded as NaN -> "N/A" in the
    # report. This is not a LangChain wrapper bug, not an incompatible
    # model choice, and not a RAGAS implementation defect for any other
    # metric - it is Groq's API contract, and only answer_relevancy
    # violates it.
    #
    # Fix: use a dedicated AnswerRelevancy(strictness=1) instance when
    # Groq is the active provider, so it requests n=1 like every other
    # metric already does. strictness is a first-class, documented
    # constructor argument on this exact metric for exactly this kind of
    # tuning - this is not a monkeypatch or a suppressed error.
    #
    # Trade-off: with strictness=1, the Groq run's answer_relevancy score
    # is a single generated-question similarity rather than an average of
    # three, so it is inherently noisier per-question than the Ollama
    # run's (which keeps strictness=3, since ChatOllama has no such n
    # restriction and is left completely untouched). This makes the two
    # providers' answer_relevancy numbers not perfectly apples-to-apples -
    # worth noting in any report that compares them side by side - but
    # there is no way to keep strictness=3 against Groq: n>1 is a hard
    # 400 on every call, not something a retry/backoff can work around.
    active_faithfulness = faithfulness
    active_answer_correctness = answer_correctness
    if EVALUATOR_PROVIDER == "groq":
        if not isinstance(ragas_llm, LangchainLLMWrapper):
            raise TypeError(
                "Groq Faithfulness requires the configured LangchainLLMWrapper "
                "so its completion-token limit can be applied without changing "
                "the shared evaluator used by the other metrics."
            )
        faithfulness_chat_model = ragas_llm.langchain_llm.model_copy(
            update={"max_tokens": GROQ_FAITHFULNESS_MAX_TOKENS}
        )
        active_faithfulness = Faithfulness(
            llm=LangchainLLMWrapper(faithfulness_chat_model)
        )
        answer_correctness_chat_model = ragas_llm.langchain_llm.model_copy(
            update={"max_tokens": GROQ_ANSWER_CORRECTNESS_MAX_TOKENS}
        )
        active_answer_correctness = AnswerCorrectness(
            llm=LangchainLLMWrapper(answer_correctness_chat_model)
        )

        active_answer_relevancy = AnswerRelevancy(strictness=1)
    else:
        active_answer_relevancy = answer_relevancy

    metrics = [
        active_faithfulness,
        active_answer_relevancy,
        context_precision,
        context_recall,
        active_answer_correctness,
    ]

    # Generous retry/backoff budget for the evaluator LLM itself - this
    # applies uniformly to whichever provider is active (Ollama connection
    # hiccups, Groq rate limits/timeouts, etc.) since RunConfig's executor
    # retries on ANY exception per sample by default before giving up and
    # scoring that sample NaN. raise_exceptions=False means a persistently
    # failing sample degrades to NaN instead of aborting the whole run.
    run_config = RunConfig(max_retries=3, max_wait=120, timeout=600, max_workers=1)

    logger.info(
        "Running RAGAS evaluation over %d question(s) using provider=%s...",
        len(records), EVALUATOR_PROVIDER,
    )
    logger.debug("LLM: %s", ragas_llm)
    logger.debug("Embeddings: %s", ragas_embeddings)

    try:
        result = evaluate(
            dataset=eval_dataset,
            metrics=metrics,
            llm=ragas_llm,
            embeddings=ragas_embeddings,
            run_config=run_config,
            token_usage_parser=evaluator_token_usage_parser,
            raise_exceptions=False,
        )
    except Exception as e:
        # A raised exception here means something failed at the evaluate()
        # setup level itself (not a single sample - those are already
        # absorbed into NaN by raise_exceptions=False above), so surface
        # it clearly rather than as a bare stack trace.
        logger.exception("RAGAS evaluate() call failed for provider=%s", EVALUATOR_PROVIDER)
        raise RuntimeError(f"RAGAS evaluation failed (provider={EVALUATOR_PROVIDER}): {e}") from e

    scores_df = result.to_pandas()

    # Re-attach the original ground-truth `id`, and rename the
    # EvaluationDataset's column names (user_input/response) to the
    # column names our output schema expects (question/generated_answer).
    scores_df.insert(0, "id", [r["id"] for r in records])
    scores_df = scores_df.rename(columns={"user_input": "question", "response": "generated_answer"})

    # Token usage / cost is tracked in aggregate for the whole evaluate()
    # call (ragas does not expose a clean per-sample breakdown across
    # concurrently-scored metrics), so it's surfaced as run-level totals
    # here rather than forced into a per-row number. If token info is
    # unavailable this degrades to blank/None rather than raising.
    total_tokens = None
    total_cost = None
    token_usage_available = False
    try:
        total_tokens = result.total_tokens()
        # total_tokens() may return a single TokenUsage or a list of them
        # depending on how many distinct models were used; normalize to one.
        if isinstance(total_tokens, list):
            total_tokens = total_tokens[0] if total_tokens else None
        total_cost, token_usage_available = _compute_run_cost(total_tokens)
    except Exception as e:
        logger.warning("Token usage/cost unavailable for this run: %s", e)

    return RagasRunOutcome(
        scores_df=scores_df,
        total_tokens=total_tokens,
        total_cost=total_cost,
        token_usage_available=token_usage_available,
    )


# --------------------------------------------------------------------------
# Output: results.csv
# --------------------------------------------------------------------------

def save_results_csv(scores_df: pd.DataFrame, records: list[dict[str, Any]], path: Path = RESULTS_CSV_PATH) -> pd.DataFrame:
    """
    Save the final results in exactly the required column order/subset.
    Missing metric columns (e.g. a metric that failed to compute for
    every row) are filled with NaN rather than raising, so a partial
    RAGAS failure still produces a usable CSV for the rows that succeeded.

    NOTE: kept for backward compatibility / any other caller. The
    resumable per-question loop in main() no longer calls this directly -
    it uses append_row_to_csv() instead so each question's row is durable
    on disk immediately rather than only at the end of a full batch.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    latency_df = pd.DataFrame([
        {
            "id": r["id"],
            "retrieval_latency_ms": r.get("retrieval_latency_ms"),
            "generation_latency_ms": r.get("generation_latency_ms"),
            "total_latency_ms": r.get("total_latency_ms"),
        }
        for r in records
    ])
    merged_df = scores_df.merge(latency_df, on="id", how="left")

    for col in ["prompt_tokens", "completion_tokens", "total_tokens", "estimated_cost"]:
        merged_df[col] = pd.NA

    for col in RESULT_COLUMNS:
        if col not in merged_df.columns:
            logger.warning("Expected column '%s' missing from RAGAS output - filling with NaN", col)
            merged_df[col] = pd.NA

    output_df = merged_df[RESULT_COLUMNS].copy()
    output_df.to_csv(path, index=False)
    logger.info("Saved results to %s", path)
    return output_df


def append_row_to_csv(row: dict[str, Any], path: Path = RESULTS_CSV_PATH) -> None:
    """
    Append a single completed question's result to the active provider's
    results CSV, writing the header only if the file doesn't exist yet.
    This is what makes the run resumable: each row is durable on disk the
    moment a question finishes. Never truncates or rewrites existing rows.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    row_df = pd.DataFrame([{col: row.get(col, pd.NA) for col in RESULT_COLUMNS}])
    write_header = not path.exists()
    row_df.to_csv(path, mode="a", header=write_header, index=False)


# --------------------------------------------------------------------------
# Output: evaluation_report.md
# --------------------------------------------------------------------------

def compute_overall_averages(df: pd.DataFrame) -> dict[str, float]:
    """Mean of each metric column, ignoring NaNs (failed/uncomputable rows)."""
    return {col: float(df[col].mean(skipna=True)) for col in METRIC_COLUMNS}


def compute_latency_averages(df: pd.DataFrame) -> dict[str, Optional[float]]:
    """Mean of each latency column, ignoring NaNs (failed questions)."""
    averages: dict[str, Optional[float]] = {}
    for col in LATENCY_COLUMNS:
        if col in df.columns and df[col].notna().any():
            averages[col] = float(pd.to_numeric(df[col], errors="coerce").mean(skipna=True))
        else:
            averages[col] = None
    return averages


def update_token_stats_from_df(run_stats: "RunStats", df: pd.DataFrame) -> None:
    """
    Recompute RunStats' token/cost totals from the results collected so
    far. Token usage is captured per-question (one evaluate() call per
    question in the resumable loop), so this just sums whatever is
    already in the CSV/DataFrame.
    """
    if "total_tokens" not in df.columns:
        return
    tokens = pd.to_numeric(df["total_tokens"], errors="coerce")
    if not tokens.notna().any():
        return
    run_stats.token_usage_available = True
    run_stats.total_prompt_tokens = int(pd.to_numeric(df["prompt_tokens"], errors="coerce").sum(skipna=True))
    run_stats.total_completion_tokens = int(pd.to_numeric(df["completion_tokens"], errors="coerce").sum(skipna=True))
    run_stats.total_tokens = int(tokens.sum(skipna=True))
    run_stats.total_cost = float(pd.to_numeric(df["estimated_cost"], errors="coerce").sum(skipna=True))


def compute_composite_score(row: pd.Series) -> float:
    """
    Simple unweighted mean across the five metrics for a single question,
    used only to rank questions as strongest/weakest for the report -
    NOT a replacement for looking at each metric individually.
    """
    values = [row[col] for col in METRIC_COLUMNS if pd.notna(row[col])]
    return sum(values) / len(values) if values else float("nan")


def generate_improvement_suggestions(df: pd.DataFrame, averages: dict[str, float]) -> list[str]:
    """
    Rule-based suggestions derived from which metric averages are weakest.
    Kept simple and deterministic (no LLM call) so the report is fast to
    regenerate and reproducible. Only fires per-metric suggestions when
    that metric actually has a computed (non-NaN) average - an all-NaN
    metric is a computation failure, not a "score is fine" result, so it
    must never be silently treated as if it cleared the 0.7 bar.
    """
    suggestions = []

    def _below_threshold(metric: str) -> bool:
        value = averages.get(metric)
        return value is not None and not pd.isna(value) and value < 0.7

    def _uncomputed(metric: str) -> bool:
        value = averages.get(metric)
        return value is None or pd.isna(value)

    uncomputed = [m for m in METRIC_COLUMNS if _uncomputed(m)]
    if uncomputed:
        suggestions.append(
            f"The following metric(s) could not be computed for any question: {', '.join(uncomputed)}. "
            "This usually means the evaluator LLM failed on every sample (bad API key, exhausted rate "
            "limit, or unreachable server) - check the run logs above before trusting any other numbers "
            "in this report."
        )

    if _below_threshold("context_recall"):
        suggestions.append(
            "Low context_recall suggests the retriever is missing relevant chunks for some "
            "questions - review retrieval top_k, chunking granularity, and whether multi-page "
            "or infographic-derived content is being retrieved at all."
        )
    if _below_threshold("context_precision"):
        suggestions.append(
            "Low context_precision suggests relevant chunks are being retrieved but not ranked "
            "highly - consider reranking or tuning retrieval scoring."
        )
    if _below_threshold("faithfulness"):
        suggestions.append(
            "Low faithfulness suggests generated answers include claims not supported by the "
            "retrieved context - review the generation prompt for stricter grounding instructions."
        )
    if _below_threshold("answer_relevancy"):
        suggestions.append(
            "Low answer_relevancy suggests answers are grounded but not directly addressing the "
            "question asked - review prompt instructions for staying on-topic and concise."
        )
    if _below_threshold("answer_correctness"):
        suggestions.append(
            "Low answer_correctness suggests generated answers diverge from the ground truth "
            "even when grounded - review whether retrieved context actually contains the "
            "specific fact needed, not just related content."
        )

    if not suggestions:
        suggestions.append(
            "All metric averages are at or above 0.7 - no immediate red flags. Continue tracking "
            "these scores over future pipeline changes to catch regressions early."
        )

    return suggestions


def generate_report(
    df: pd.DataFrame,
    run_stats: "RunStats",
    path: Path = REPORT_PATH,
    top_n: int = 5,
) -> None:
    """
    Build evaluation_report_<provider>.md: evaluator provider used,
    overall metric averages, latency averages, total execution time and
    cost, strongest/weakest questions, and rule-based improvement
    suggestions.
    """
    averages = compute_overall_averages(df)
    latency_averages = compute_latency_averages(df)

    df = df.copy()
    df["composite_score"] = df.apply(compute_composite_score, axis=1)
    ranked = df.dropna(subset=["composite_score"]).sort_values("composite_score", ascending=False)

    strongest = ranked.head(top_n)
    weakest = ranked.tail(top_n).sort_values("composite_score")

    suggestions = generate_improvement_suggestions(df, averages)

    active_model = OLLAMA_MODEL_NAME if EVALUATOR_PROVIDER == "ollama" else GROQ_MODEL_NAME

    lines: list[str] = []
    lines.append("# RAGAS Evaluation Report")
    lines.append("")
    lines.append(f"Evaluator provider: **{EVALUATOR_PROVIDER}** (`{active_model}`)")
    lines.append("")
    lines.append(f"Total questions evaluated: **{len(df)}**  ")
    lines.append(f"Successful: **{run_stats.successful}** | Failed: **{run_stats.failed}**")
    lines.append("")

    lines.append("## Overall Metric Averages")
    lines.append("")
    lines.append("| Metric | Average Score |")
    lines.append("|---|---|")
    for metric in METRIC_COLUMNS:
        value = averages.get(metric)
        lines.append(f"| {metric} | {value:.3f} |" if value is not None and not pd.isna(value) else f"| {metric} | N/A |")
    lines.append("")

    lines.append("## Latency & Execution Time")
    lines.append("")
    lines.append("| Measure | Value |")
    lines.append("|---|---|")
    lines.append(_format_latency_row("Average retrieval latency", latency_averages.get("retrieval_latency_ms")))
    lines.append(_format_latency_row("Average generation latency", latency_averages.get("generation_latency_ms")))
    lines.append(_format_latency_row("Average total latency", latency_averages.get("total_latency_ms")))
    lines.append(f"| Total execution time | {_format_duration(run_stats.total_duration_seconds)} |")
    if run_stats.token_usage_available and run_stats.total_cost is not None:
        lines.append(f"| Total estimated API cost | ${run_stats.total_cost:.4f} |")
    elif run_stats.token_usage_available:
        lines.append("| Total estimated API cost | N/A (no pricing configured for this model) |")
    else:
        lines.append("| Total estimated API cost | N/A (token usage unavailable) |")
    lines.append("")

    lines.append("## Strongest Questions")
    lines.append("")
    lines.append("| ID | Question | Composite Score |")
    lines.append("|---|---|---|")
    for _, row in strongest.iterrows():
        question_preview = str(row["question"])[:100]
        lines.append(f"| {row['id']} | {question_preview} | {row['composite_score']:.3f} |")
    lines.append("")

    lines.append("## Weakest Questions")
    lines.append("")
    lines.append("| ID | Question | Composite Score |")
    lines.append("|---|---|---|")
    for _, row in weakest.iterrows():
        question_preview = str(row["question"])[:100]
        lines.append(f"| {row['id']} | {question_preview} | {row['composite_score']:.3f} |")
    lines.append("")

    lines.append("## Improvement Suggestions")
    lines.append("")
    for suggestion in suggestions:
        lines.append(f"- {suggestion}")
    lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    logger.info("Saved evaluation report to %s", path)


def _format_latency_row(label: str, value_ms: Optional[float]) -> str:
    if value_ms is None:
        return f"| {label} | N/A |"
    return f"| {label} | {value_ms:.1f} ms |"


def _format_duration(total_seconds: float) -> str:
    minutes, seconds = divmod(int(total_seconds), 60)
    return f"{minutes} min {seconds} sec"


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

@dataclass
class RunStats:
    questions_evaluated: int = 0
    successful: int = 0
    failed: int = 0
    total_duration_seconds: float = 0.0
    total_cost: Optional[float] = None
    token_usage_available: bool = False
    total_prompt_tokens: Optional[int] = None
    total_completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None


def _warn_if_legacy_files_present() -> None:
    """
    One-time, read-only heads-up if pre-provider-suffix files exist from
    before this change (evaluation/results.csv /
    evaluation/evaluation_report.md). Never read from, written to, moved,
    or deleted automatically - see the design discussion on why this is
    a manual step: it keeps "which file has my 8.8-hour Ollama run"
    unambiguous rather than something the script decided on your behalf.
    """
    if _LEGACY_RESULTS_CSV_PATH.exists() and not RESULTS_CSV_PATH.exists():
        logger.warning(
            "Found a legacy results file at %s from before provider-suffixed output paths. "
            "It is NOT read or modified by this script. If it holds a completed Ollama run you "
            "want to preserve as a baseline, rename it manually:\n"
            "  mv %s %s\n"
            "  mv %s %s",
            _LEGACY_RESULTS_CSV_PATH,
            _LEGACY_RESULTS_CSV_PATH, EVAL_DIR / "results_ollama.csv",
            _LEGACY_REPORT_PATH, EVAL_DIR / "evaluation_report_ollama.md",
        )


def main() -> None:
    eval_start = time.perf_counter()
    run_stats = RunStats()
    results_df: Optional[pd.DataFrame] = None

    _warn_if_legacy_files_present()

    try:
        ground_truth = load_ground_truth(GROUND_TRUTH_PATH)
        completed_ids = get_completed_ids(RESULTS_CSV_PATH)
        # NOTE: this used to end in `[:1]`, silently capping every run -
        # resumed or not - to exactly one question regardless of how many
        # were actually remaining. That wasn't one of the 8 requested
        # fixes, but it directly blocks the stated goal (running the full
        # 25-question evaluation) and would do so silently, which
        # conflicts with "raise errors loudly rather than silently
        # producing partial output." Flagging it here rather than
        # leaving it, since every other fix in this pass is moot if the
        # loop still only ever processes one question. If this slice was
        # intentional (e.g. you're still smoke-testing), remove this
        # comment and re-add `[:1]` explicitly rather than relying on it
        # being there by accident.
        remaining = [g for g in ground_truth if str(g["id"]) not in completed_ids]

        run_stats.questions_evaluated = len(ground_truth)
        logger.info(
            "Evaluator provider: %s | Resuming: %d already completed, %d remaining",
            EVALUATOR_PROVIDER, len(completed_ids), len(remaining),
        )

        if remaining:
            # Build + validate the evaluator LLM/embeddings ONCE, before
            # touching the RAG pipeline at all. A bad GROQ_API_KEY, an
            # unreachable Ollama server, or a rate limit already in effect
            # is caught right here with a clear message - not after
            # burning time running the RAG pipeline for 25 questions.
            try:
                ragas_llm, ragas_embeddings = build_ragas_llm_and_embeddings()
            except (EnvironmentError, ConnectionError, ImportError, RuntimeError) as e:
                logger.error(
                    "Could not initialize the '%s' evaluator - stopping before any "
                    "evaluation work starts. %s",
                    EVALUATOR_PROVIDER, e,
                )
                sys.exit(1)

            for entry in tqdm(remaining, desc="Evaluating", unit="question"):
                try:
                    pipeline_records = run_rag_on_dataset([entry])
                    record = pipeline_records[0]

                    ragas_outcome = run_ragas_evaluation(
                        pipeline_records,
                        ragas_llm=ragas_llm,
                        ragas_embeddings=ragas_embeddings,
                    )
                    row = ragas_outcome.scores_df.iloc[0].to_dict()
                    row["retrieval_latency_ms"] = record["retrieval_latency_ms"]
                    row["generation_latency_ms"] = record["generation_latency_ms"]
                    row["total_latency_ms"] = record["total_latency_ms"]

                    if ragas_outcome.token_usage_available and ragas_outcome.total_tokens is not None:
                        row["prompt_tokens"] = ragas_outcome.total_tokens.input_tokens
                        row["completion_tokens"] = ragas_outcome.total_tokens.output_tokens
                        row["total_tokens"] = (
                            ragas_outcome.total_tokens.input_tokens + ragas_outcome.total_tokens.output_tokens
                        )
                        row["estimated_cost"] = ragas_outcome.total_cost
                    else:
                        row["prompt_tokens"] = row["completion_tokens"] = row["total_tokens"] = row["estimated_cost"] = pd.NA

                    # Durable the moment this question finishes - a crash
                    # on the next question still leaves this one saved.
                    append_row_to_csv(row, RESULTS_CSV_PATH)

                except Exception:
                    # Deliberately NOT appended, so this id is still
                    # "incomplete" and will be retried automatically the
                    # next time the script runs. Already-completed rows
                    # on disk are untouched.
                    logger.exception(
                        "Evaluation failed for question id=%s - skipping for now, "
                        "already-completed results remain safe on disk.",
                        entry.get("id"),
                    )
                    continue

                # Regenerate the report from everything completed so far,
                # after every single question, so it never goes stale if
                # the run is interrupted.
                results_df = pd.read_csv(RESULTS_CSV_PATH)
                run_stats.successful = len(results_df)
                run_stats.failed = run_stats.questions_evaluated - run_stats.successful
                run_stats.total_duration_seconds = time.perf_counter() - eval_start
                update_token_stats_from_df(run_stats, results_df)
                generate_report(results_df, run_stats, REPORT_PATH)

        if results_df is None and RESULTS_CSV_PATH.exists():
            results_df = pd.read_csv(RESULTS_CSV_PATH)

        if results_df is not None:
            run_stats.successful = len(results_df)
            run_stats.failed = run_stats.questions_evaluated - run_stats.successful
            run_stats.total_duration_seconds = time.perf_counter() - eval_start
            update_token_stats_from_df(run_stats, results_df)
            generate_report(results_df, run_stats, REPORT_PATH)
            _print_summary(run_stats, results_df)
        else:
            logger.info("No questions were evaluated.")

    except Exception as e:
        run_stats.total_duration_seconds = time.perf_counter() - eval_start
        logger.error("Evaluation run failed: %s", e)
        if RESULTS_CSV_PATH.exists():
            logger.info("Completed results so far remain safe in %s.", RESULTS_CSV_PATH)
        sys.exit(1)


def _print_summary(run_stats: RunStats, results_df: pd.DataFrame) -> None:
    latency_averages = compute_latency_averages(results_df)
    active_model = OLLAMA_MODEL_NAME if EVALUATOR_PROVIDER == "ollama" else GROQ_MODEL_NAME

    print()
    print("Evaluation completed successfully.")
    print()
    print(f"Evaluator provider  : {EVALUATOR_PROVIDER} ({active_model})")
    print(f"Questions evaluated : {run_stats.questions_evaluated}")
    print(f"Successful          : {run_stats.successful}")
    print(f"Failed              : {run_stats.failed}")
    print()

    def _fmt(v):
        return f"{v:.1f} ms" if v is not None else "N/A"

    print(f"Average retrieval latency  : {_fmt(latency_averages.get('retrieval_latency_ms'))}")
    print(f"Average generation latency : {_fmt(latency_averages.get('generation_latency_ms'))}")
    print(f"Average total latency      : {_fmt(latency_averages.get('total_latency_ms'))}")
    print()

    print(f"Total execution time : {_format_duration(run_stats.total_duration_seconds)}")
    print()

    if run_stats.token_usage_available:
        print(f"Total prompt tokens     : {run_stats.total_prompt_tokens}")
        print(f"Total completion tokens : {run_stats.total_completion_tokens}")
        print(f"Total tokens            : {run_stats.total_tokens}")
        print()
        if run_stats.total_cost is not None:
            print(f"Estimated API cost : ${run_stats.total_cost:.4f}")
        else:
            print("Estimated API cost : N/A (no pricing configured for this model)")
    else:
        print("Token usage / cost estimate : unavailable for this run")
    print()

    print(f"Results saved to:\n  {RESULTS_CSV_PATH}")
    print()
    print(f"Report saved to:\n  {REPORT_PATH}")


if __name__ == "__main__":
    main()
