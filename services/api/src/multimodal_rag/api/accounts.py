"""Persistent account, session, and chat storage for the HTTP API."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Iterator


class PasswordHasher:
    """Use a salted, memory-hard password hash without retaining plaintext."""

    _prefix = "scrypt"
    _n = 2**14
    _r = 8
    _p = 1

    @classmethod
    def hash(cls, password: str) -> str:
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=cls._n, r=cls._r, p=cls._p)
        return "$".join((cls._prefix, base64.b64encode(salt).decode("ascii"), base64.b64encode(digest).decode("ascii")))

    @classmethod
    def verify(cls, password: str, encoded: str) -> bool:
        try:
            prefix, salt_value, digest_value = encoded.split("$", 2)
            if prefix != cls._prefix:
                return False
            salt = base64.b64decode(salt_value, validate=True)
            expected = base64.b64decode(digest_value, validate=True)
            actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=cls._n, r=cls._r, p=cls._p)
            return secrets.compare_digest(actual, expected)
        except (ValueError, TypeError):
            return False


class PostgresUserStore:
    """Parameterized PostgreSQL storage that scopes sessions and chats to one user."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - deployment configuration
            raise RuntimeError("psycopg is required for persistent user accounts") from exc
        with psycopg.connect(self._database_url) as connection:
            yield connection

    def initialize(self) -> None:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS user_sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    expires_at TIMESTAMPTZ NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS user_sessions_user_id_idx ON user_sessions (user_id);
                CREATE TABLE IF NOT EXISTS user_chats (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    strategy_state JSONB,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                ALTER TABLE user_chats ADD COLUMN IF NOT EXISTS strategy_state JSONB;
                CREATE INDEX IF NOT EXISTS user_chats_user_updated_idx ON user_chats (user_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS user_chat_messages (
                    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                    chat_id TEXT NOT NULL REFERENCES user_chats(id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    payload JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS user_chat_messages_chat_idx ON user_chat_messages (user_id, chat_id, id);
            """)

    def create_user(self, username: str, password: str) -> str | None:
        user_id = str(uuid.uuid4())
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (id, username, password_hash) VALUES (%s, %s, %s) ON CONFLICT (username) DO NOTHING RETURNING id",
                (user_id, username, PasswordHasher.hash(password)),
            )
            row = cursor.fetchone()
        return row[0] if row else None

    def authenticate(self, username: str, password: str) -> str | None:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT id, password_hash FROM users WHERE username = %s", (username,))
            row = cursor.fetchone()
        if row is None or not PasswordHasher.verify(password, row[1]):
            return None
        return row[0]

    def create_session(self, user_id: str, duration: timedelta = timedelta(days=14)) -> str:
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM user_sessions WHERE expires_at <= NOW()")
            cursor.execute(
                "INSERT INTO user_sessions (token_hash, user_id, expires_at) VALUES (%s, %s, %s)",
                (token_hash, user_id, datetime.now(UTC) + duration),
            )
        return token

    def user_for_token(self, token: str) -> str | None:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT user_id FROM user_sessions WHERE token_hash = %s AND expires_at > NOW()",
                (token_hash,),
            )
            row = cursor.fetchone()
        return row[0] if row else None

    def append_message(self, user_id: str, chat_id: str, role: str, payload: dict[str, Any]) -> None:
        title = str(payload.get("question") or "New conversation").strip()[:120] or "New conversation"
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO user_chats (id, user_id, title) VALUES (%s, %s, %s)
                   ON CONFLICT (id) DO UPDATE SET updated_at = NOW()
                   WHERE user_chats.user_id = EXCLUDED.user_id RETURNING id""",
                (chat_id, user_id, title),
            )
            if cursor.fetchone() is None:
                raise ValueError("Conversation does not belong to this user.")
            cursor.execute(
                "INSERT INTO user_chat_messages (chat_id, user_id, role, payload) VALUES (%s, %s, %s, %s::jsonb)",
                (chat_id, user_id, role, json.dumps(payload)),
            )

    def list_chats(self, user_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT chats.id, chats.title, chats.created_at, chats.updated_at, COUNT(messages.id)
                   FROM user_chats chats LEFT JOIN user_chat_messages messages ON messages.chat_id = chats.id
                   WHERE chats.user_id = %s GROUP BY chats.id ORDER BY chats.updated_at DESC""",
                (user_id,),
            )
            rows = cursor.fetchall()
        return [{"id": row[0], "title": row[1], "created_at": row[2], "updated_at": row[3], "message_count": row[4]} for row in rows]

    def get_chat(self, user_id: str, chat_id: str) -> dict[str, Any] | None:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT id, title, created_at, updated_at FROM user_chats WHERE id = %s AND user_id = %s", (chat_id, user_id))
            chat = cursor.fetchone()
            if chat is None:
                return None
            cursor.execute(
                "SELECT id, role, payload, created_at FROM user_chat_messages WHERE chat_id = %s AND user_id = %s ORDER BY id",
                (chat_id, user_id),
            )
            messages = cursor.fetchall()
        return {"id": chat[0], "title": chat[1], "created_at": chat[2], "updated_at": chat[3], "messages": [{"id": str(row[0]), "role": row[1], "payload": row[2], "created_at": row[3]} for row in messages]}

    def get_strategy_state(self, user_id: str, chat_id: str) -> dict[str, Any] | None:
        """Load a pending Market Strategy continuation scoped to one user/chat."""
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT strategy_state FROM user_chats WHERE id = %s AND user_id = %s",
                (chat_id, user_id),
            )
            row = cursor.fetchone()
        return dict(row[0]) if row and row[0] else None

    def set_strategy_state(self, user_id: str, chat_id: str, state: dict[str, Any]) -> None:
        """Persist a server-created intelligence snapshot for clarification reuse."""
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE user_chats SET strategy_state = %s::jsonb, updated_at = NOW() WHERE id = %s AND user_id = %s RETURNING id",
                (json.dumps(state), chat_id, user_id),
            )
            if cursor.fetchone() is None:
                raise ValueError("Conversation does not belong to this user.")

    def clear_strategy_state(self, user_id: str, chat_id: str) -> None:
        """Clear a completed or invalid pending Market Strategy continuation."""
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE user_chats SET strategy_state = NULL WHERE id = %s AND user_id = %s",
                (chat_id, user_id),
            )

    def get_recent_exchanges(self, user_id: str, chat_id: str, limit: int = 10) -> list[dict[str, Any]]:
        """Return at most `limit` recent (question, assistant-payload) pairs, oldest first.

        Pairing happens in Python, not SQL: a 500 mid-request can leave an
        orphan user row (the user message is recorded before the answer is
        generated - see router.py), and a pure-SQL window/LAG join would
        silently mispair across that gap. `limit * 3` rows is a generous
        bound that still lets the existing (user_id, chat_id, id) index
        serve the whole query.
        """
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT id, role, payload FROM (
                       SELECT id, role, payload FROM user_chat_messages
                       WHERE user_id = %s AND chat_id = %s
                       ORDER BY id DESC LIMIT %s
                   ) recent ORDER BY id""",
                (user_id, chat_id, limit * 3),
            )
            rows = cursor.fetchall()
        exchanges: list[dict[str, Any]] = []
        question: str | None = None
        for message_id, role, payload in rows:
            if role == "user":
                question = str((payload or {}).get("question") or "") or None
            else:
                exchanges.append({"message_id": str(message_id), "question": question, "payload": payload})
                question = None
        return exchanges[-limit:]

    def create_chat(self, user_id: str, chat_id: str, title: str) -> dict[str, Any]:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO user_chats (id, user_id, title) VALUES (%s, %s, %s)
                   ON CONFLICT (id) DO UPDATE SET title = EXCLUDED.title, updated_at = NOW()
                   WHERE user_chats.user_id = EXCLUDED.user_id
                   RETURNING id, title, created_at, updated_at""",
                (chat_id, user_id, title),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError("Conversation does not belong to this user.")
        return {"id": row[0], "title": row[1], "created_at": row[2], "updated_at": row[3], "message_count": 0}

    def delete_chat(self, user_id: str, chat_id: str) -> bool:
        """Delete a chat and all of its messages and memory. Returns False if it did not belong to this user."""
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM chat_memories WHERE chat_id = %s AND user_id = %s", (chat_id, user_id))
            cursor.execute("DELETE FROM chat_profiles WHERE chat_id = %s AND user_id = %s", (chat_id, user_id))
            cursor.execute(
                "DELETE FROM user_chats WHERE id = %s AND user_id = %s RETURNING id",
                (chat_id, user_id),
            )
            return cursor.fetchone() is not None

