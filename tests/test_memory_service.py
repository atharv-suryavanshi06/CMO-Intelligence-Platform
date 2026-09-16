from __future__ import annotations

import json
import unittest
from collections.abc import Iterable
from typing import Any

from multimodal_rag.memory import MemoryCandidate, MemoryExtractor, MemoryService


class FakeRepository:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self.profiles: dict[tuple[str, str], dict[str, Any]] = {}
        self.initialized = False

    def initialize(self) -> None:
        self.initialized = True

    def get_memory(self, user_id: str, chat_id: str, memory_type: str, key: str) -> dict[str, Any] | None:
        return self.rows.get((user_id, chat_id, memory_type, key))

    def upsert_memory(self, user_id: str, chat_id: str, candidate: MemoryCandidate) -> str:
        identity = (user_id, chat_id, candidate.memory_type, candidate.key)
        existing = self.rows.get(identity)
        if existing and " ".join(existing["value"].casefold().split()) == " ".join(candidate.value.casefold().split()):
            return "ignored"
        self.rows[identity] = {"value": candidate.value, "confidence": candidate.confidence}
        if candidate.memory_type in {"company_context", "industry", "user_role", "target_audience", "market", "brand_positioning", "user_preference"}:
            self.profiles.setdefault((user_id, chat_id), {})[candidate.key] = candidate.value
        return "updated" if existing else "inserted"

    def get_profile(self, user_id: str, chat_id: str) -> dict[str, Any]:
        return dict(self.profiles.get((user_id, chat_id), {}))

    def list_memories(self, user_id: str, chat_id: str) -> Iterable[dict[str, Any]]:
        return [
            {"memory_type": memory_type, "key": key, **value}
            for (row_user_id, row_chat_id, memory_type, key), value in self.rows.items()
            if row_user_id == user_id and row_chat_id == chat_id
        ]

    def clone_chat_memory(self, user_id: str, source_chat_id: str, target_chat_id: str) -> None:
        source_profile = self.profiles.get((user_id, source_chat_id), {})
        if source_profile:
            self.profiles[(user_id, target_chat_id)] = dict(source_profile)
        for (row_user_id, row_chat_id, memory_type, key), value in list(self.rows.items()):
            if row_user_id == user_id and row_chat_id == source_chat_id:
                self.rows[(user_id, target_chat_id, memory_type, key)] = dict(value)


def generator_for(payload: dict[str, Any]):
    return lambda _prompt: json.dumps(payload)


class MemoryServiceTests(unittest.TestCase):
    def make_service(self, payload: dict[str, Any]) -> tuple[MemoryService, FakeRepository]:
        repository = FakeRepository()
        return MemoryService(MemoryExtractor(generator_for(payload)), repository), repository

    def test_useful_business_information_is_extracted_and_inserted(self) -> None:
        service, repository = self.make_service({"should_store": True, "memories": [
            {"memory_type": "company_context", "key": "company", "value": "Northstar Apparel", "confidence": 0.95},
            {"memory_type": "target_audience", "key": "primary_audience", "value": "Gen Z", "confidence": 0.95},
            {"memory_type": "market", "key": "geography", "value": "India", "confidence": 0.95},
        ]})
        self.assertEqual(service.process_message("alice", "chat-1", "We are Northstar Apparel targeting Gen Z in India."), ["inserted", "inserted", "inserted"])
        self.assertEqual(repository.get_profile("alice", "chat-1")["company"], "Northstar Apparel")

    def test_ordinary_question_and_empty_extraction_are_not_persisted(self) -> None:
        service, repository = self.make_service({"should_store": False, "memories": []})
        self.assertEqual(service.process_message("alice", "chat-1", "Give me five campaign ideas."), [])
        self.assertEqual(list(repository.list_memories("alice", "chat-1")), [])

    def test_existing_memory_is_updated_and_duplicate_is_ignored(self) -> None:
        repository = FakeRepository()
        first = MemoryService(MemoryExtractor(generator_for({"should_store": True, "memories": [{"memory_type": "business_goal", "key": "current_priority", "value": "Customer acquisition"}]})), repository)
        second = MemoryService(MemoryExtractor(generator_for({"should_store": True, "memories": [{"memory_type": "business_goal", "key": "current_priority", "value": "Improve customer retention"}]})), repository)
        duplicate = MemoryService(MemoryExtractor(generator_for({"should_store": True, "memories": [{"memory_type": "business_goal", "key": "current_priority", "value": "  improve CUSTOMER retention "}]})), repository)
        self.assertEqual(first.process_message("alice", "chat-1", "Acquisition is our priority."), ["inserted"])
        self.assertEqual(second.process_message("alice", "chat-1", "Retention is our priority now."), ["updated"])
        self.assertEqual(duplicate.process_message("alice", "chat-1", "Retention remains our priority."), ["ignored"])

    def test_invalid_output_and_sensitive_candidates_are_handled_safely(self) -> None:
        repository = FakeRepository()
        invalid = MemoryService(MemoryExtractor(lambda _prompt: "not JSON"), repository)
        sensitive = MemoryService(MemoryExtractor(generator_for({"should_store": True, "memories": [{"memory_type": "user_preference", "key": "api_key", "value": "sk-secret-value"}]})), repository)
        self.assertEqual(invalid.process_message("alice", "chat-1", "We are in fashion."), [])
        self.assertEqual(sensitive.process_message("alice", "chat-1", "My API key is sk-secret-value."), [])
        self.assertEqual(list(repository.list_memories("alice", "chat-1")), [])

    def test_chat_isolation_and_structured_context(self) -> None:
        service, _repository = self.make_service({"should_store": True, "memories": [
            {"memory_type": "industry", "key": "primary_industry", "value": "Fashion"},
            {"memory_type": "business_goal", "key": "retention", "value": "Improve customer retention"},
            {"memory_type": "competitor", "key": "primary_competitor", "value": "Zara"},
            {"memory_type": "budget_constraint", "key": "paid_media", "value": "Limited paid advertising budget"},
        ]})
        service.process_message("alice", "chat-1", "Fashion company with retention goals.")
        # Other user's chat is empty
        self.assertEqual(service.get_user_context("bob", "chat-1"), {"profile": {}, "business_context": {}})
        # Same user in a different (new) chat is completely empty
        self.assertEqual(service.get_user_context("alice", "chat-2"), {"profile": {}, "business_context": {}})
        # Missing chat_id returns empty
        self.assertEqual(service.get_user_context("alice", None), {"profile": {}, "business_context": {}})
        # Specific chat returns the chat's context
        self.assertEqual(service.get_user_context("alice", "chat-1"), {
            "profile": {"primary_industry": "Fashion"},
            "business_context": {
                "business_goals": ["Improve customer retention"],
                "competitors": ["Zara"],
                "constraints": ["Limited paid advertising budget"],
            },
        })

    def test_clone_context_for_branching(self) -> None:
        service, repository = self.make_service({"should_store": True, "memories": [
            {"memory_type": "industry", "key": "primary_industry", "value": "Footwear"},
            {"memory_type": "market", "key": "primary_market", "value": "India"},
        ]})
        service.process_message("alice", "chat-parent", "Footwear company in India.")
        self.assertEqual(service.get_user_context("alice", "chat-child"), {"profile": {}, "business_context": {}})
        
        # Clone context from parent to child
        service.clone_context("alice", "chat-parent", "chat-child")
        child_context = service.get_user_context("alice", "chat-child")
        self.assertEqual(child_context["profile"], {"primary_industry": "Footwear", "primary_market": "India"})

    def test_extractor_has_no_persistence_dependency(self) -> None:
        extractor = MemoryExtractor(generator_for({"should_store": True, "memories": [{"memory_type": "kpi", "key": "retention_rate", "value": "Increase retention rate"}]}))
        result = extractor.extract("We want to improve retention.")
        self.assertEqual(result.memories[0].key, "retention_rate")
        self.assertFalse(hasattr(extractor, "repository"))


if __name__ == "__main__":
    unittest.main()
