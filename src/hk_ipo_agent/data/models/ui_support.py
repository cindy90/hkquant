"""UI-support ORM models — v1.2.1 tables per PROJECT_SPEC.md §5 + §16.

Phase 7 MVP held all three in-memory; Phase 7.5a moves to PG:

- ``whatif_calculations`` — every What-If run is persisted so the result
  can be re-shown across devices and used for attribution.
- ``realtime_events`` — SSE / WebSocket event log for replay & audit.
- ``api_rate_limit_state`` — sliding-window counter (only when Redis
  isn't available; production usually uses Redis instead).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDMixin


class WhatIfCalculationRow(UUIDMixin, Base):
    """Persisted What-If run — PROJECT_SPEC.md §5 ``whatif_calculations``.

    CLAUDE.md v1.2.1 constraint: What-If results MUST be persisted so
    Phase 10 attribution can compare assumptions to outcomes.
    """

    __tablename__ = "whatif_calculations"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("prediction_snapshots.id"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("user_accounts.id"),
    )
    modified_assumptions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    original_distribution: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    new_distribution: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    runtime_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("idx_whatif_snapshot", "snapshot_id", "created_at"),)


class RealtimeEventRow(UUIDMixin, Base):
    """SSE / WebSocket event log — PROJECT_SPEC.md §5 ``realtime_events``.

    Used for replay (e.g., UI reconnect after disconnect) + forensic
    audit. ``broadcast_count`` tracks how many subscribers received the
    event for SSE delivery diagnostics.
    """

    __tablename__ = "realtime_events"

    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    # enums.RealtimeEventType: alert.created / snapshot.updated / ...
    related_ipo_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("ipo_events.id"),
    )
    related_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("prediction_snapshots.id"),
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    broadcast_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (
        Index("idx_realtime_events_time", "created_at"),
        Index("idx_realtime_events_type", "event_type", "created_at"),
    )


class APIRateLimitStateRow(UUIDMixin, Base):
    """API rate-limit counter — PROJECT_SPEC.md §5 ``api_rate_limit_state``.

    Only used as a DB-backed fallback when Redis is unavailable.
    UNIQUE on (user_id, endpoint_pattern, window_start) gives the
    upsert anchor.
    """

    __tablename__ = "api_rate_limit_state"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("user_accounts.id"),
    )
    endpoint_pattern: Mapped[str | None] = mapped_column(String(200))
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request_count: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        Index(
            "uq_rate_limit_user_endpoint_window",
            "user_id",
            "endpoint_pattern",
            "window_start",
            unique=True,
        ),
    )


__all__ = (
    "APIRateLimitStateRow",
    "RealtimeEventRow",
    "WhatIfCalculationRow",
)
