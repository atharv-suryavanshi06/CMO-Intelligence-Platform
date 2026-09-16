"""PostgreSQL persistence implementation for chat business memory."""

from __future__ import annotations

import json
from collections.abc import Iterable
from contextlib import contextmanager
from typing import Any, Iterator, Protocol

from multimodal_rag.memory.models import MemoryCandidate


class MemoryRepository(Protocol):
    def initialize(self) -> None: ...
    def get_memory(self, user_id: str, chat_id: str, memory_type: str, key: str) -> dict[str, Any] | None: ...
    def upsert_memory(self, user_id: str, chat_id: str, candidate: MemoryCandidate) -> str: ...
    def get_profile(self, user_id: str, chat_id: str) -> dict[str, Any]: ...
    def list_memories(self, user_id: str, chat_id: str) -> Iterable[dict[str, Any]]: ...
    def clone_chat_memory(self, user_id: str, source_chat_id: str, target_chat_id: str) -> None: ...


class PostgresMemoryRepository:
    """Small, parameterized PostgreSQL repository with strict user and chat scoping."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - environment configuration
            raise RuntimeError("psycopg is required for PostgreSQL memory persistence") from exc
        with psycopg.connect(self._database_url) as connection:
            yield connection

    def initialize(self) -> None:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chat_profiles (
                    user_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    profile_data JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (user_id, chat_id)
                );
                CREATE TABLE IF NOT EXISTS chat_memories (
                    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    memory_type TEXT NOT NULL,
                    memory_key TEXT NOT NULL,
                    memory_value TEXT NOT NULL,
                    confidence DOUBLE PRECISION,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (user_id, chat_id, memory_type, memory_key)
                );
                CREATE INDEX IF NOT EXISTS chat_memories_chat_idx ON chat_memories (user_id, chat_id);
            """)

    def get_memory(self, user_id: str, chat_id: str, memory_type: str, key: str) -> dict[str, Any] | None:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT memory_value, confidence FROM chat_memories WHERE user_id = %s AND chat_id = %s AND memory_type = %s AND memory_key = %s",
                (user_id, chat_id, memory_type, key),
            )
            row = cursor.fetchone()
        return {"value": row[0], "confidence": row[1]} if row else None

    def upsert_memory(self, user_id: str, chat_id: str, candidate: MemoryCandidate) -> str:
        existing = self.get_memory(user_id, chat_id, candidate.memory_type, candidate.key)
        if existing and self._normalized(existing["value"]) == self._normalized(candidate.value):
            return "ignored"
        operation = "updated" if existing else "inserted"
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO chat_memories (user_id, chat_id, memory_type, memory_key, memory_value, confidence)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (user_id, chat_id, memory_type, memory_key) DO UPDATE
                   SET memory_value = EXCLUDED.memory_value, confidence = EXCLUDED.confidence, updated_at = NOW()""",
                (user_id, chat_id, candidate.memory_type, candidate.key, candidate.value, candidate.confidence),
            )
            if candidate.memory_type in {"company_context", "industry", "user_role", "target_audience", "market", "brand_positioning", "user_preference"}:
                cursor.execute(
                    """INSERT INTO chat_profiles (user_id, chat_id, profile_data) VALUES (%s, %s, %s::jsonb)
                       ON CONFLICT (user_id, chat_id) DO UPDATE
                       SET profile_data = chat_profiles.profile_data || EXCLUDED.profile_data, updated_at = NOW()""",
                    (user_id, chat_id, json.dumps({candidate.key: candidate.value})),
                )
        return operation

    def get_profile(self, user_id: str, chat_id: str) -> dict[str, Any]:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT profile_data FROM chat_profiles WHERE user_id = %s AND chat_id = %s", (user_id, chat_id))
            row = cursor.fetchone()
        return dict(row[0]) if row else {}

    def list_memories(self, user_id: str, chat_id: str) -> Iterable[dict[str, Any]]:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT memory_type, memory_key, memory_value, confidence FROM chat_memories WHERE user_id = %s AND chat_id = %s ORDER BY memory_type, memory_key",
                (user_id, chat_id),
            )
            rows = cursor.fetchall()
        return [
            {"memory_type": row[0], "key": row[1], "value": row[2], "confidence": row[3]}
            for row in rows
        ]

    def clone_chat_memory(self, user_id: str, source_chat_id: str, target_chat_id: str) -> None:
        """Clone memories and profile from source_chat_id to target_chat_id."""
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO chat_profiles (user_id, chat_id, profile_data)
                   SELECT user_id, %s, profile_data FROM chat_profiles
                   WHERE user_id = %s AND chat_id = %s
                   ON CONFLICT (user_id, chat_id) DO UPDATE
                   SET profile_data = EXCLUDED.profile_data, updated_at = NOW()""",
                (target_chat_id, user_id, source_chat_id),
            )
            cursor.execute(
                """INSERT INTO chat_memories (user_id, chat_id, memory_type, memory_key, memory_value, confidence)
                   SELECT user_id, %s, memory_type, memory_key, memory_value, confidence
                   FROM chat_memories WHERE user_id = %s AND chat_id = %s
                   ON CONFLICT (user_id, chat_id, memory_type, memory_key) DO UPDATE
                   SET memory_value = EXCLUDED.memory_value, confidence = EXCLUDED.confidence, updated_at = NOW()""",
                (target_chat_id, user_id, source_chat_id),
            )

    @staticmethod
    def _normalized(value: str) -> str:
        return " ".join(value.casefold().split())
