"""Cornerstone ORM per PROJECT_SPEC.md §5 + ADR 0005 §1.

Tables: ``cornerstone_investors``, ``cornerstone_investments``.
The investor table holds the 1,314 NACS legacy profiles migrated by
``scripts/migrate_sqlite_to_pg.py``.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, Date, ForeignKey, Index, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from .ipo import IPOEvent


class CornerstoneInvestor(UUIDMixin, TimestampMixin, Base):
    """Cornerstone investor (entity).

    NACS v8 legacy seed: 1,314 entities with category / parent_org / home_country
    (ADR 0005 §1). ``aliases`` JSONB holds the 1,051 ``cornerstone_aliases`` rows
    merged in for fuzzy name resolution.
    """

    __tablename__ = "cornerstone_investors"

    name_zh: Mapped[str | None] = mapped_column(String(500))
    name_en: Mapped[str | None] = mapped_column(String(500))
    category: Mapped[str | None] = mapped_column(String(50))  # CornerstoneCategory value
    parent_org: Mapped[str | None] = mapped_column(String(300))
    ultimate_holder: Mapped[str | None] = mapped_column(String(300))
    home_country: Mapped[str | None] = mapped_column(String(50))
    signal_strength_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))

    # Migrated NACS cornerstone_aliases + AUM tags + free-form metadata
    aliases: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    extra_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    investments: Mapped[list[CornerstoneInvestment]] = relationship(
        back_populates="investor", cascade="all, delete-orphan"
    )


class CornerstoneInvestment(UUIDMixin, TimestampMixin, Base):
    """A single cornerstone subscription event (one investor → one IPO)."""

    __tablename__ = "cornerstone_investments"

    ipo_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("ipo_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    investor_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("cornerstone_investors.id", ondelete="CASCADE"),
        nullable=False,
    )
    commitment_amount_hkd: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    pct_of_offering: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    lockup_months: Mapped[int | None] = mapped_column()
    disclosure_date: Mapped[date | None] = mapped_column(Date)
    is_anchor: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    ipo: Mapped[IPOEvent] = relationship(back_populates="cornerstone_investments")
    investor: Mapped[CornerstoneInvestor] = relationship(back_populates="investments")

    __table_args__ = (
        Index("ix_cornerstone_investments_ipo_investor", "ipo_id", "investor_id"),
    )
