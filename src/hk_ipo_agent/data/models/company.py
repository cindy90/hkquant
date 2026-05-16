"""Generic company + financial snapshot ORM per PROJECT_SPEC.md §3.4.

Distinct from ``IPOEvent`` (which is the issuance event):
- ``Company`` is the issuer entity, with stable identifiers across pre / post IPO.
- ``FinancialSnapshotRow`` stores per-period financials, both from prospectus and
  post-listing earnings filings (used by ``earnings_comparator.py``).
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Index, Numeric, String
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin


class Company(UUIDMixin, TimestampMixin, Base):
    """A company (issuer)."""

    __tablename__ = "companies"

    name_zh: Mapped[str | None] = mapped_column(String(200))
    name_en: Mapped[str | None] = mapped_column(String(200))
    hk_stock_code: Mapped[str | None] = mapped_column(String(10))
    a_share_code: Mapped[str | None] = mapped_column(String(10))
    us_adr_code: Mapped[str | None] = mapped_column(String(10))
    industry_code: Mapped[str | None] = mapped_column(String(120))
    incorporation_country: Mapped[str | None] = mapped_column(String(50))

    snapshots: Mapped[list[FinancialSnapshotRow]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )


class FinancialSnapshotRow(UUIDMixin, TimestampMixin, Base):
    """One financial period for a company.

    Mirrors the Pydantic ``FinancialSnapshot`` model (PROJECT_SPEC.md §6).
    Source can be prospectus extraction or post-listing earnings filing.
    """

    __tablename__ = "financial_snapshots"

    company_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    fiscal_year: Mapped[int] = mapped_column(nullable=False)
    fiscal_period: Mapped[str] = mapped_column(String(8), nullable=False)
    period_end: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str | None] = mapped_column(String(30))  # prospectus / annual_report / interim

    revenue_rmb: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    gross_profit_rmb: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    gross_margin: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    rd_expense_rmb: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    rd_pct_of_revenue: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    net_profit_rmb: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    adjusted_net_profit_rmb: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    operating_cash_flow_rmb: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    cash_balance_rmb: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))

    company: Mapped[Company] = relationship(back_populates="snapshots")

    __table_args__ = (
        Index(
            "ix_financial_snapshots_company_period",
            "company_id",
            "fiscal_year",
            "fiscal_period",
            unique=True,
        ),
    )
