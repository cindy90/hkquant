"""Operations ORM models — v1.2 tables per PROJECT_SPEC.md §3.11 + §3.11.2 + §5.

Schedulers / alerts / earnings comparator schema land in Phase 7.5a per
ADR 0012; business logic in 7.5c (alerts, earnings) and 7.5d (schedulers).

- ``scheduler_runs`` — every BaseScheduler run writes a row (run_id +
  status + processed counts + errors); idempotency via run_id UNIQUE.
- ``alerts`` — info/warning/critical with mandatory ``actionable_info``.
- ``earnings_comparisons`` — actual filing vs prospectus extraction
  diff; first 3 per system MUST set ``requires_human_review=True``.
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


class SchedulerRunRow(UUIDMixin, Base):
    """Scheduler execution log — PROJECT_SPEC.md §5 ``scheduler_runs``.

    ``run_id`` UNIQUE provides the idempotency anchor; ``BaseScheduler``
    (Phase 7.5d) uses it with DB advisory lock to prevent overlap.
    """

    __tablename__ = "scheduler_runs"

    scheduler_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # enums.SchedulerType: high_freq / daily / event_driven
    run_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snapshots_processed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    events_detected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    errors_encountered: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_details: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="running", nullable=False)
    # enums.SchedulerStatus: running / completed / failed

    __table_args__ = (Index("idx_scheduler_runs_started", "scheduler_type", "started_at"),)


class AlertRow(UUIDMixin, Base):
    """Alert with mandatory actionable_info — PROJECT_SPEC.md §5 ``alerts``.

    CLAUDE.md v1.2 constraint: ``actionable_info`` is NOT optional —
    "Failed" is rejected; alerts must say "what to do next".
    Deduplication on (category, ipo_id, level) over 24h handled by
    ``alerts.py`` (Phase 7.5c), not the DB.
    """

    __tablename__ = "alerts"

    level: Mapped[str] = mapped_column(String(20), nullable=False)
    # enums.AlertLevel: info / warning / critical
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    related_ipo_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("ipo_events.id"),
    )
    related_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("prediction_snapshots.id"),
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    actionable_info: Mapped[str] = mapped_column(Text, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[str | None] = mapped_column(String(100))
    extra_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # Partial index for unacked alerts — fastest path for the dashboard.
    __table_args__ = (
        Index(
            "idx_alerts_unacked",
            "acknowledged_at",
            "level",
            postgresql_where="acknowledged_at IS NULL",
        ),
    )


class EarningsComparisonRow(UUIDMixin, TimestampMixin, Base):
    """Actual filing vs prospectus extraction — PROJECT_SPEC.md §5 ``earnings_comparisons``.

    UNIQUE on (snapshot_id, report_period) prevents double-writing.
    First 3 comparisons per system MUST set ``requires_human_review=True``
    (CLAUDE.md v1.2 constraint), enforced in Phase 7.5c.
    """

    __tablename__ = "earnings_comparisons"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("prediction_snapshots.id"),
        nullable=False,
    )
    report_period: Mapped[str] = mapped_column(String(20), nullable=False)
    # 'FY2025' / 'H1-2025' / 'Q1-2025'
    filing_date: Mapped[date | None] = mapped_column(Date)

    actual_revenue: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    predicted_revenue_from_prospectus: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    revenue_deviation_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))

    actual_net_profit: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    predicted_net_profit: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    profit_deviation_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))

    actual_gross_margin: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    predicted_gross_margin: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    margin_deviation_pp: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))

    qualitative_deviations: Mapped[list[str] | None] = mapped_column(JSONB)
    overall_assessment: Mapped[str | None] = mapped_column(String(30))
    # enums.EarningsAssessment: beat / in_line / miss / significant_miss
    confidence: Mapped[str | None] = mapped_column(String(20))
    notes: Mapped[str | None] = mapped_column(Text)
    requires_human_review: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (
        Index(
            "uq_earnings_snapshot_period",
            "snapshot_id",
            "report_period",
            unique=True,
        ),
    )


__all__ = (
    "AlertRow",
    "EarningsComparisonRow",
    "SchedulerRunRow",
)
