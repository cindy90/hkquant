"""Chat session / message ORM models — v1.2.1 tables per PROJECT_SPEC.md §5 + §16.4.

Phase 7 MVP held chat history in an in-memory ``ChatManager``; Phase 7.5a
moves storage to ``chat_sessions`` + ``chat_messages``, satisfying CLAUDE.md
v1.2.1 constraint: "WebSocket chat 所有消息必须持久化到 chat_messages 表"
(otherwise users lose history on device switch).

- ``chat_sessions`` — one row per ongoing conversation; optionally
  scoped to a snapshot or IPO.
- ``chat_messages`` — messages with sequence + role + cost tracking;
  ON DELETE CASCADE wipes messages when a session is hard-deleted.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDMixin


class ChatSessionRow(UUIDMixin, Base):
    """Chat session — PROJECT_SPEC.md §5 ``chat_sessions``."""

    __tablename__ = "chat_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("user_accounts.id"),
        nullable=False,
    )
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("prediction_snapshots.id"),
    )
    ipo_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("ipo_events.id"),
    )
    title: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (Index("idx_chat_sessions_user", "user_id", "last_active_at"),)


class ChatMessageRow(UUIDMixin, Base):
    """Chat message — PROJECT_SPEC.md §5 ``chat_messages``.

    ``sequence`` is the in-session monotonic counter; the API uses
    (session_id, sequence) to stream messages in order.
    """

    __tablename__ = "chat_messages"

    session_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    # enums.ChatMessageRole: user / assistant / system / tool
    content: Mapped[str | None] = mapped_column(Text)
    content_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    citations: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    tools_used: Mapped[list[str] | None] = mapped_column(JSONB)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    tokens_input: Mapped[int | None] = mapped_column(Integer)
    tokens_output: Mapped[int | None] = mapped_column(Integer)
    model_used: Mapped[str | None] = mapped_column(String(100))
    runtime_ms: Mapped[int | None] = mapped_column(Integer)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("idx_chat_messages_session", "session_id", "sequence"),)


__all__ = (
    "ChatMessageRow",
    "ChatSessionRow",
)
