"""Sponsor ORM per PROJECT_SPEC.md §5.

Table: ``sponsors`` — IPO sponsor track record (rolled-up metrics over 24m window).
``SponsorRecord`` (per-window snapshot) is deferred to Phase 8 backtest needs.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Boolean, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class Sponsor(UUIDMixin, TimestampMixin, Base):
    """IPO sponsor (investment bank / advisory). 24m rolling track-record snapshot."""

    __tablename__ = "sponsors"

    name: Mapped[str | None] = mapped_column(String(200))
    is_sfc_licensed: Mapped[bool | None] = mapped_column(Boolean)
    track_record_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    cases_count_24m: Mapped[int | None] = mapped_column()
    avg_day1_return: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    avg_6m_return: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
