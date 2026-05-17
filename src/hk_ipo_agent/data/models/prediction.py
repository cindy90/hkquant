"""Prediction-registry ORM models — v1.1 tables per PROJECT_SPEC.md §3.11 + §5.

Tables landed in Phase 7.5a per ADR 0012:

- ``prediction_snapshots`` — immutable; UPDATE/DELETE blocked by DB trigger
  ``prevent_snapshot_modification`` (see Alembic v1.1 migration).
- ``prediction_outcomes`` — T+N checkpoint records, unique on (snapshot_id, day).
- ``post_ipo_events`` — earnings / profit warning / cornerstone disclosure / etc.
- ``prediction_reviews`` — append-only human review notes (the only mutable
  artifact attached to a snapshot).
- ``config_versions`` — config / prompt version history (for adjustment_applier
  in Phase 10 learning loop, schema seeded here).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class PredictionSnapshotRow(UUIDMixin, Base):
    """Immutable prediction snapshot — PROJECT_SPEC.md §5 ``prediction_snapshots``.

    DB-level immutability is enforced via the ``snapshot_no_update`` and
    ``snapshot_no_delete`` triggers wired in the v1.1 Alembic migration.
    Pydantic-layer immutability is provided by ``common.schemas.PredictionSnapshot``
    (FrozenModel).
    """

    __tablename__ = "prediction_snapshots"

    ipo_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("ipo_events.id"),
        nullable=False,
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    prospectus_version: Mapped[str | None] = mapped_column(String(50))

    input_data_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_data_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    agent_outputs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    valuation_output: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    debate_output: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    decision: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    system_version: Mapped[str] = mapped_column(String(50), nullable=False)
    model_versions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    total_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    runtime_seconds: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index(
            "uq_snapshots_ipo_asof_version",
            "ipo_id",
            "as_of_date",
            "prospectus_version",
            unique=True,
        ),
        Index("idx_snapshots_ipo", "ipo_id"),
    )


class PredictionOutcomeRow(UUIDMixin, Base):
    """T+N checkpoint outcome — PROJECT_SPEC.md §5 ``prediction_outcomes``.

    Idempotent by ``(snapshot_id, checkpoint_day)`` UNIQUE. Phase 7.5b
    outcome_tracker relies on this to avoid double-counting.
    """

    __tablename__ = "prediction_outcomes"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("prediction_snapshots.id"),
        nullable=False,
    )
    checkpoint_day: Mapped[int] = mapped_column(Integer, nullable=False)
    # -1 marks terminal outcomes (withdrawn / hearing failed) per terminal_handlers.

    return_since_ipo: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    return_since_listing: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    max_drawdown: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    relative_return_hsi: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    relative_return_hstech: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    relative_return_industry: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))

    events_in_window: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    earnings_released: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    earnings_beat_extraction: Mapped[bool | None] = mapped_column(Boolean)

    cornerstone_held_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    cornerstone_reduced: Mapped[bool | None] = mapped_column(Boolean)
    # R2-5: see PredictionOutcome schema docstring. server_default="false"
    # so existing prediction_outcomes rows (before migration) read as
    # tracking-reliable instead of NULL.
    cornerstone_tracking_unreliable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    price_in_predicted_range: Mapped[bool | None] = mapped_column(Boolean)
    decision_correct: Mapped[bool | None] = mapped_column(Boolean)

    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index(
            "uq_outcomes_snapshot_checkpoint",
            "snapshot_id",
            "checkpoint_day",
            unique=True,
        ),
        Index("idx_outcomes_checkpoint", "checkpoint_day"),
    )


class PostIPOEventRow(UUIDMixin, Base):
    """Post-listing event stream — PROJECT_SPEC.md §5 ``post_ipo_events``."""

    __tablename__ = "post_ipo_events"

    ipo_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("ipo_events.id"),
        nullable=False,
    )
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # enums.PostIPOEventType: earnings/profit_warning/major_contract/regulatory/...
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    # enums.EventSeverity: critical/major/minor
    description: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(500))
    price_impact_1d: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    price_impact_5d: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("idx_events_ipo_date", "ipo_id", "event_date"),)


class PredictionReviewRow(UUIDMixin, TimestampMixin, Base):
    """Append-only human review — PROJECT_SPEC.md §5 ``prediction_reviews``.

    The only mutable artifact attached to a snapshot (notes_md +
    adjustment_status flow). Workflow: proposed → accepted/rejected →
    implemented, enforced by ``review_workflow.py``.
    """

    __tablename__ = "prediction_reviews"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("prediction_snapshots.id"),
        nullable=False,
    )
    review_checkpoint_day: Mapped[int | None] = mapped_column(Integer)
    reviewer: Mapped[str | None] = mapped_column(String(100))

    what_we_got_right: Mapped[str | None] = mapped_column(Text)
    what_we_got_wrong: Mapped[str | None] = mapped_column(Text)

    primary_attribution: Mapped[str | None] = mapped_column(String(50))
    attribution_details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    proposed_adjustments: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    adjustment_status: Mapped[str | None] = mapped_column(String(20))
    # enums.AdjustmentStatus: proposed / accepted / rejected / implemented
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applied_version: Mapped[str | None] = mapped_column(String(50))

    notes_md: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("idx_reviews_status", "adjustment_status"),)


class ConfigVersionRow(UUIDMixin, Base):
    """Config / prompt version history — PROJECT_SPEC.md §5 ``config_versions``.

    Phase 10 learning loop ``adjustment_applier`` writes here when applying
    an accepted proposal. Schema is seeded in Phase 7.5a so reviews can
    reference applied_version forward-compatibly.
    """

    __tablename__ = "config_versions"

    target_path: Mapped[str] = mapped_column(String(500), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    content: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    change_type: Mapped[str | None] = mapped_column(String(50))
    # 'manual'/'learning_loop_applied'/'rollback'
    source_review_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("prediction_reviews.id"),
    )
    applied_by: Mapped[str | None] = mapped_column(String(100))
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index(
            "uq_config_versions_path_version",
            "target_path",
            "version",
            unique=True,
        ),
    )


__all__ = (
    "ConfigVersionRow",
    "PostIPOEventRow",
    "PredictionOutcomeRow",
    "PredictionReviewRow",
    "PredictionSnapshotRow",
)
