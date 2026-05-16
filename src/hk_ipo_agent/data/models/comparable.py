"""Comparable companies pool ORM per PROJECT_SPEC.md §3.4 + §3.7.

Table: ``comparable_companies`` — pool used by ``valuation/comparable.py`` for
PS / PE / EV-Sales percentile valuation. Cross-market (A / H / US ADR).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class ComparableCompany(UUIDMixin, TimestampMixin, Base):
    """A single comparable company entry."""

    __tablename__ = "comparable_companies"

    ticker: Mapped[str] = mapped_column(String(30), nullable=False)
    market: Mapped[str | None] = mapped_column(String(10))  # HK / A / US
    company_name: Mapped[str | None] = mapped_column(String(200))
    industry_code: Mapped[str | None] = mapped_column(String(20))
    sub_industry: Mapped[str | None] = mapped_column(String(100))

    # Latest snapshot of valuation multiples for percentile screening.
    market_cap_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    enterprise_value_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    ps_ttm: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    pe_ttm: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    ev_sales_ttm: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    ev_ebitda_ttm: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))

    # Adjustments (liquidity discount, geography, IFRS vs US GAAP)
    adjustments: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    snapshot_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
