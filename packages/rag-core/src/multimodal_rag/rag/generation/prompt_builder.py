"""
Prompt Builder Module (RAG Stage 5)
=======================================

Assembles a query + retrieved chunks into a prompt for the LLM. Each
chunk is presented with a numbered [S1], [S2]... citation marker; the
LLM is instructed to cite sources by that marker when answering. Stage 6
(citation mapping) resolves those markers back to real chunk/page info
using the `source_map` this function also returns.

MODIFIED: added optional `conversation_history` support so follow-up
questions ("explain that", "summarize it") can be resolved by the LLM
using recent chat context. This is purely a prompt-construction concern
- retrieval still runs on the latest query alone (see rag/retrieval/
retriever_2.py, unchanged) and answer generation is unaware memory exists
(rag/generation/answer_generator.py, unchanged). `conversation_history`
defaults to None, so every existing call site behaves exactly as before.
"""

from __future__ import annotations

from dataclasses import dataclass

from multimodal_rag.rag.retrieval.retriever_2 import RetrievedChunk

DEFAULT_SYSTEM_INSTRUCTIONS = (
    "You are a helpful Retrieval-Augmented Generation (RAG) assistant. "
    "Answer ONLY using the information provided in the SOURCES section. "
    "The retrieved sources are ordered by relevance, with S1 being the most relevant. "
    "Use S1 as the primary source whenever it fully answers the user's question. "
    "Only combine information from lower-ranked sources when S1 is incomplete or the user explicitly asks for a comparison, summary, or broader explanation. "
    "Do NOT introduce unrelated information from lower-ranked sources. "
    "Do NOT use outside knowledge or make up information. "
    "Do NOT mention source markers, page numbers, or citations in your response. "
    "Write the answer in clear bullet points whenever the information is presented as lists or key points in the source document. "
    "Use short headings followed by bullet points where appropriate. "
    "Avoid long paragraphs unless the user explicitly asks for a detailed explanation. "
    "If the answer is not available in the provided sources, clearly say that the document does not contain enough information."
)

FOLLOW_UP_INSTRUCTIONS = (
    " Use the CONVERSATION HISTORY below only to understand what the user is "
    "referring to in follow-up questions (e.g. 'explain that', 'summarize it', "
    "'give an example', 'compare it with the previous concept') - but continue to "
    "base the actual answer content and citations only on the SOURCES section."
)


@dataclass
class ConversationTurn:
    user_query: str
    assistant_answer: str


@dataclass
class BuiltPrompt:
    prompt_text: str
    source_map: dict[str, RetrievedChunk]  # "S1" -> the chunk it refers to


def build_prompt(
    query: str,
    chunks: list[RetrievedChunk],
    system_instructions: str = DEFAULT_SYSTEM_INSTRUCTIONS,
    conversation_history: list[ConversationTurn] | None = None,
    max_history_turns: int = 5,
) -> BuiltPrompt:
    if not chunks:
        raise ValueError("Cannot build a prompt with zero retrieved chunks.")

    source_map: dict[str, RetrievedChunk] = {}
    source_blocks = []
    for i, chunk in enumerate(chunks, start=1):
        marker = f"S{i}"
        source_map[marker] = chunk
        pages = ", ".join(str(p) for p in chunk.page_numbers) or "unknown"
        source_blocks.append(
            f"[{marker}] (source: {chunk.source_file}, page(s): {pages})\n{chunk.chunk_text}"
        )

    history_block = ""
    instructions = system_instructions
    if conversation_history:
        recent = conversation_history[-max_history_turns:]
        turns_text = "\n\n".join(
            f"User: {t.user_query}\nAssistant: {t.assistant_answer}" for t in recent
        )
        history_block = f"--- CONVERSATION HISTORY ---\n{turns_text}\n\n"
        instructions = system_instructions + FOLLOW_UP_INSTRUCTIONS

    prompt_text = (
        f"{instructions}\n\n"
        f"{history_block}"
        f"--- SOURCES ---\n\n" + "\n\n".join(source_blocks) +
        f"\n\n--- QUESTION ---\n{query.strip()}\n\n--- ANSWER ---"
    )
    return BuiltPrompt(prompt_text=prompt_text, source_map=source_map)
