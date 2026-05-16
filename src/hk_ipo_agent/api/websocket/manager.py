"""Chat session + message store — in-memory default, PG opt-in (7.5b-3).

ADR 0012 §7.5b-3 wires a PostgreSQL backend honouring CLAUDE.md v1.2.1's
constraint: "WebSocket chat 所有消息必须持久化到 chat_messages 表".
The default is still in-memory so Phase 7 unit tests pass without a DB;
production lifespan in api/main.py calls ``set_chat_store(PGChatStore())``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...common.enums import ChatMessageRole
from ...common.schemas import ChatMessage, ChatSession
from ...data.database import async_session_factory
from ...data.models import ChatMessageRow, ChatSessionRow


@runtime_checkable
class ChatStoreProtocol(Protocol):
    """Public API both backends honour."""

    async def create_session(
        self,
        *,
        user_id: UUID,
        title: str,
        snapshot_id: UUID | None = None,
        ipo_id: UUID | None = None,
    ) -> ChatSession: ...

    async def get_session(self, session_id: UUID) -> ChatSession | None: ...

    async def list_sessions_for_user(self, user_id: UUID) -> list[ChatSession]: ...

    async def append_message(
        self,
        *,
        session_id: UUID,
        role: ChatMessageRole,
        content: str,
        content_json: dict[str, Any] | None = None,
    ) -> ChatMessage: ...

    async def list_messages(self, session_id: UUID) -> list[ChatMessage]: ...


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


class PGChatStore:
    """PostgreSQL-backed chat store.

    chat_messages has ON DELETE CASCADE on session_id; deleting a session
    wipes its messages. ``sequence`` is monotonic per session and is the
    sort key the API uses to stream messages in order.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        self._sf = session_factory or async_session_factory()

    async def create_session(
        self,
        *,
        user_id: UUID,
        title: str,
        snapshot_id: UUID | None = None,
        ipo_id: UUID | None = None,
    ) -> ChatSession:
        now = datetime.now(UTC)
        row = ChatSessionRow(
            id=uuid4(), user_id=user_id, snapshot_id=snapshot_id, ipo_id=ipo_id,
            title=title, created_at=now, last_active_at=now, archived=False,
        )
        async with self._sf() as s:
            s.add(row)
            await s.commit()
        return ChatSession(
            id=row.id, user_id=row.user_id, snapshot_id=row.snapshot_id,
            ipo_id=row.ipo_id, title=row.title or "",
            created_at=row.created_at, last_active_at=row.last_active_at,
            archived=row.archived,
        )

    async def get_session(self, session_id: UUID) -> ChatSession | None:
        async with self._sf() as s:
            row = await s.get(ChatSessionRow, session_id)
        if row is None:
            return None
        return ChatSession(
            id=row.id, user_id=row.user_id, snapshot_id=row.snapshot_id,
            ipo_id=row.ipo_id, title=row.title or "",
            created_at=row.created_at, last_active_at=row.last_active_at,
            archived=row.archived,
        )

    async def list_sessions_for_user(self, user_id: UUID) -> list[ChatSession]:
        stmt = (
            select(ChatSessionRow)
            .where(ChatSessionRow.user_id == user_id)
            .order_by(ChatSessionRow.last_active_at.desc())
        )
        async with self._sf() as s:
            rows = (await s.execute(stmt)).scalars().all()
        return [
            ChatSession(
                id=r.id, user_id=r.user_id, snapshot_id=r.snapshot_id, ipo_id=r.ipo_id,
                title=r.title or "", created_at=r.created_at,
                last_active_at=r.last_active_at, archived=r.archived,
            )
            for r in rows
        ]

    async def append_message(
        self,
        *,
        session_id: UUID,
        role: ChatMessageRole,
        content: str,
        content_json: dict[str, Any] | None = None,
    ) -> ChatMessage:
        async with self._sf() as s:
            sess = await s.get(ChatSessionRow, session_id)
            if sess is None:
                raise KeyError(session_id)
            # Sequence = current count.
            count_stmt = select(ChatMessageRow.sequence).where(
                ChatMessageRow.session_id == session_id
            )
            seqs = (await s.execute(count_stmt)).scalars().all()
            next_seq = (max(seqs) + 1) if seqs else 0
            now = datetime.now(UTC)
            msg_row = ChatMessageRow(
                id=uuid4(), session_id=session_id,
                role=role.value, content=content, content_json=content_json,
                sequence=next_seq, created_at=now,
            )
            s.add(msg_row)
            sess.last_active_at = now
            await s.commit()
        return ChatMessage(
            id=msg_row.id, session_id=session_id, role=role,
            content=content, content_json=content_json,
            sequence=next_seq, created_at=now,
        )

    async def list_messages(self, session_id: UUID) -> list[ChatMessage]:
        stmt = (
            select(ChatMessageRow)
            .where(ChatMessageRow.session_id == session_id)
            .order_by(ChatMessageRow.sequence.asc())
        )
        async with self._sf() as s:
            rows = (await s.execute(stmt)).scalars().all()
        return [
            ChatMessage(
                id=r.id, session_id=r.session_id,
                role=ChatMessageRole(r.role),
                content=r.content or "", content_json=r.content_json,
                sequence=r.sequence, created_at=r.created_at,
            )
            for r in rows
        ]


_default_store: list[ChatStoreProtocol] = []


def get_chat_store() -> ChatStoreProtocol:
    if not _default_store:
        _default_store.append(ChatStore())
    return _default_store[0]


def set_chat_store(store: ChatStoreProtocol) -> None:
    """Replace the process-wide store — called from FastAPI lifespan."""
    _default_store.clear()
    _default_store.append(store)


def reset_chat_store_for_test() -> None:
    _default_store.clear()


__all__ = (
    "ChatStore",
    "ChatStoreProtocol",
    "PGChatStore",
    "get_chat_store",
    "reset_chat_store_for_test",
    "set_chat_store",
)
