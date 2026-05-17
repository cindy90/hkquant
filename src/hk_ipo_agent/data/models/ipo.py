"""IPO core ORM models per PROJECT_SPEC.md §5 + §3.4.

Tables: ``ipo_events``, ``ipo_pricings``, ``ipo_allocations``, ``ipo_postmarket``.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, Date, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from .cornerstone import CornerstoneInvestment
    from .prospectus import ProspectusDoc


class IPOEvent(UUIDMixin, TimestampMixin, Base):
    """Single IPO event (one row per company-issuance).

    See PROJECT_SPEC.md §5 ``ipo_events`` table.
    """

    __tablename__ = "ipo_events"

    stock_code: Mapped[str | None] = mapped_column(String(10))
    company_name_zh: Mapped[str | None] = mapped_column(String(200))
    company_name_en: Mapped[str | None] = mapped_column(String(200))
    listing_type: Mapped[str | None] = mapped_column(String(20))  # enums.ListingType value
    industry_code: Mapped[str | None] = mapped_column(String(120))

    sponsor_ids: Mapped[list[uuid.UUID] | None] = mapped_column(ARRAY(PgUUID(as_uuid=True)))

    a1_filing_date: Mapped[date | None] = mapped_column(Date)
    hearing_date: Mapped[date | None] = mapped_column(Date)
    pricing_date: Mapped[date | None] = mapped_column(Date)
    listing_date: Mapped[date | None] = mapped_column(Date)

    issue_size_hkd: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    use_of_proceeds: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    regulatory_regime: Mapped[str | None] = mapped_column(String(30))
    is_18c_pre_commercial: Mapped[bool | None] = mapped_column(Boolean)

    ah_pair_a_code: Mapped[str | None] = mapped_column(String(10))

    pricing: Mapped[IPOPricing | None] = relationship(
        back_populates="ipo", uselist=False, cascade="all, delete-orphan"
    )
    postmarket: Mapped[IPOPostMarket | None] = relationship(
        back_populates="ipo", uselist=False, cascade="all, delete-orphan"
    )
    allocations: Mapped[list[IPOAllocation]] = relationship(
        back_populates="ipo", cascade="all, delete-orphan"
    )
    cornerstone_investments: Mapped[list[CornerstoneInvestment]] = relationship(
        back_populates="ipo", cascade="all, delete-orphan"
    )
    prospectus_docs: Mapped[list[ProspectusDoc]] = relationship(
        back_populates="ipo", cascade="all, delete-orphan"
    )


class IPOPricing(UUIDMixin, TimestampMixin, Base):
    """Pricing + subscription / allocation mechanism per PROJECT_SPEC.md §5."""

    __tablename__ = "ipo_pricings"

    ipo_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("ipo_events.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    price_range_low: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    price_range_high: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    final_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    intl_oversubscription: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    retail_oversubscription: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    margin_subscription_multiple: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    allocation_mechanism: Mapped[str | None] = mapped_column(String(10))
    final_public_allocation_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))

    ipo: Mapped[IPOEvent] = relationship(back_populates="pricing")


class IPOAllocation(UUIDMixin, TimestampMixin, Base):
    """Per-tranche allocation amounts (cornerstone / anchor / public / international)."""

    __tablename__ = "ipo_allocations"

    ipo_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("ipo_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    tranche: Mapped[str] = mapped_column(String(30), nullable=False)
    # tranche values: cornerstone / anchor / public / international / employee
    allocation_amount_hkd: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    allocation_pct_of_offering: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    investor_count: Mapped[int | None] = mapped_column()
    notes: Mapped[str | None] = mapped_column(Text)

    ipo: Mapped[IPOEvent] = relationship(back_populates="allocations")

    __table_args__ = (Index("ix_ipo_allocations_ipo_tranche", "ipo_id", "tranche"),)


class IPOPostMarket(UUIDMixin, TimestampMixin, Base):
    """Post-listing returns at canonical checkpoint days.

    See PROJECT_SPEC.md §5 ``ipo_postmarket`` table.

    Spec §5 defines six denormalized scalar columns (day1/5/22/126/127/252) plus
    drawdown + lockup-retained flag — these are the NACS-era summary fields and
    are kept verbatim for migration compatibility (ADR 0005 §1).

    For full-coverage checkpoint storage aligned with ``enums.CHECKPOINT_DAYS``
    (1, 5, 10, 22, 30, 60, 90, 126, 180, 252, 360), use ``returns_by_day`` JSONB.
    The mapping rules and how this composes with ``prediction_outcomes`` (v1.1,
    Phase 7.5) are documented in ADR 0007.
    """

    __tablename__ = "ipo_postmarket"

    ipo_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("ipo_events.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    # Spec §5 denormalized scalars (NACS-compatible).
    day1_return: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    day5_return: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    day22_return: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    day126_return: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    day127_return: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))  # lockup expiry
    day252_return: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    max_drawdown_d126: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    cornerstone_held_after_lockup: Mapped[bool | None] = mapped_column(Boolean)

    # Forward-compat full checkpoint storage. Shape: {"1": "0.05", "5": "0.12", ...}
    # Keys are str(day) per CHECKPOINT_DAYS; values are Decimal-as-string returns.
    returns_by_day: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Cornerstone holding pct at each canonical checkpoint (best-effort).
    cornerstone_held_pct_by_day: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    ipo: Mapped[IPOEvent] = relationship(back_populates="postmarket")
