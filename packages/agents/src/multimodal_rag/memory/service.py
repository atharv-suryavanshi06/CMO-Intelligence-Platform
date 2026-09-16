"""Application service that owns memory validation and persistence decisions."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from multimodal_rag.api.config import validate_scope_identifier
from multimodal_rag.memory.extractor import MemoryExtractor
from multimodal_rag.memory.models import MemoryCandidate
from multimodal_rag.memory.repository import MemoryRepository

_SENSITIVE = re.compile(r"(?:password|passphrase|api[ _-]?key|secret|access[ _-]?token|auth(?:entication)?[ _-]?token|bearer)\b", re.I)
_CREDENTIAL_VALUE = re.compile(r"(?:sk-|AIza|ghp_|xox[baprs]-)[A-Za-z0-9_=-]{8,}")


class MemoryService:
    """Validate extractor output and make all insert/update/ignore decisions."""

    def __init__(self, extractor: MemoryExtractor | None = None, repository: MemoryRepository | None = None) -> None:
        self._extractor = extractor
        self._repository = repository

    @property
    def enabled(self) -> bool:
        return self._extractor is not None and self._repository is not None

    def initialize(self) -> None:
        if self._repository:
            self._repository.initialize()

    def process_message(self, user_id: str, chat_id: str | None, message: str) -> list[str]:
        validate_scope_identifier(user_id, "user_id")
        if not chat_id:
            return []
        validate_scope_identifier(chat_id, "chat_id")
        if not self.enabled or not message.strip():
            return []
        extraction = self._extractor.extract(message)
        operations: list[str] = []
        seen: set[tuple[str, str]] = set()
        for candidate in extraction.memories:
            identity = (candidate.memory_type, candidate.key)
            if identity in seen or not self._is_storable(candidate):
                continue
            seen.add(identity)
            operations.append(self._repository.upsert_memory(user_id, chat_id, candidate))
        return operations

    def get_user_context(self, user_id: str, chat_id: str | None = None) -> dict[str, Any]:
        validate_scope_identifier(user_id, "user_id")
        if not chat_id or not self._repository:
            return {"profile": {}, "business_context": {}}
        validate_scope_identifier(chat_id, "chat_id")
        grouped: dict[str, list[str]] = defaultdict(list)
        for memory in self._repository.list_memories(user_id, chat_id):
            if memory["memory_type"] not in {"company_context", "industry", "user_role", "target_audience", "market", "brand_positioning", "user_preference"}:
                grouped[memory["memory_type"]].append(memory["value"])
        labels = {
            "business_goal": "business_goals", "strategic_priority": "strategic_priorities", "competitor": "competitors",
            "budget_constraint": "constraints", "business_constraint": "constraints", "marketing_channel": "marketing_channels", "kpi": "kpis",
        }
        business_context: dict[str, list[str]] = defaultdict(list)
        for memory_type, values in grouped.items():
            business_context[labels.get(memory_type, memory_type)].extend(values)
        return {"profile": self._repository.get_profile(user_id, chat_id), "business_context": dict(business_context)}

    def clone_context(self, user_id: str, source_chat_id: str, target_chat_id: str) -> None:
        validate_scope_identifier(user_id, "user_id")
        validate_scope_identifier(source_chat_id, "source_chat_id")
        validate_scope_identifier(target_chat_id, "target_chat_id")
        if self._repository and hasattr(self._repository, "clone_chat_memory"):
            self._repository.clone_chat_memory(user_id, source_chat_id, target_chat_id)

    @staticmethod
    def _is_storable(candidate: MemoryCandidate) -> bool:
        combined = f"{candidate.key} {candidate.value}"
        return not _SENSITIVE.search(combined) and not _CREDENTIAL_VALUE.search(candidate.value)
