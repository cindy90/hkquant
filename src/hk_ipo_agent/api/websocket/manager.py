"""In-memory chat session + message store per ADR 0011 §Phase 7 MVP."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from ...common.enums import ChatMessageRole
from ...common.schemas import ChatMessage, ChatSession


class ChatStore:
    """Append-only in-memory chat sessions + messages."""

    def __init__(self) -> None:
        self._sessions: dict[UUID, ChatSession] = {}
        self._messages: dict[UUID, list[ChatMessage]] = {}
        self._lock = asyncio.Lock()

    async def create_session(
        self,
        *,
        user_id: UUID,
        title: str,
        snapshot_id: UUID | None = None,
        ipo_id: UUID | None = None,
    ) -> ChatSession:
        async with self._lock:
            now = datetime.now(UTC)
            session = ChatSession(
                id=uuid4(),
                user_id=user_id,
                snapshot_id=snapshot_id,
                ipo_id=ipo_id,
                title=title,
                created_at=now,
                last_active_at=now,
            )
            self._sessions[session.id] = session
            self._messages[session.id] = []
            return session

    async def get_session(self, session_id: UUID) -> ChatSession | None:
        async with self._lock:
            return self._sessions.get(session_id)

    async def list_sessions_for_user(self, user_id: UUID) -> list[ChatSession]:
        async with self._lock:
            return [s for s in self._sessions.values() if s.user_id == user_id]

    async def append_message(
        self,
        *,
        session_id: UUID,
        role: ChatMessageRole,
        content: str,
        content_json: dict[str, Any] | None = None,
    ) -> ChatMessage:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise KeyError(session_id)
            messages = self._messages.setdefault(session_id, [])
            now = datetime.now(UTC)
            msg = ChatMessage(
                id=uuid4(),
                session_id=session_id,
                role=role,
                content=content,
                content_json=content_json,
                sequence=len(messages),
                created_at=now,
            )
            messages.append(msg)
            self._sessions[session_id] = session.model_copy(update={"last_active_at": now})
            return msg

    async def list_messages(self, session_id: UUID) -> list[ChatMessage]:
        async with self._lock:
            return list(self._messages.get(session_id, []))

    def clear(self) -> None:
        self._sessions.clear()
        self._messages.clear()


_default_store: list[ChatStore] = []


def get_chat_store() -> ChatStore:
    if not _default_store:
        _default_store.append(ChatStore())
    return _default_store[0]


def reset_chat_store_for_test() -> None:
    _default_store.clear()


__all__ = (
    "ChatStore",
    "get_chat_store",
    "reset_chat_store_for_test",
)
