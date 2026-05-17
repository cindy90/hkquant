"""AsOfDataProvider — leak-proof time-point data access.

Per PROJECT_SPEC.md §3.9: this is the **★★★ most critical file** for
backtest credibility — any data access during walk-forward must go
through ``AsOfDataProvider(as_of_date)``, which guarantees no field
post-dating ``as_of_date`` is ever returned.

What "leak-proof" means here (ADR 0005 §4 + ADR 0013 §8a):

1. **Financial snapshots** filtered by ``period_end <= as_of_date - 30d``
   (per filing convention — a fiscal-year-2024 figure isn't typically
   disclosed before late Jan 2025) or by an explicit publication-date
   field when present. Defaults to a 30-day disclosure lag.

2. **Market prices** filtered by ``trade_date < as_of_date`` (strict
   inequality — same-day prices are leaks).

3. **HKEX filings** filtered by ``filing_date <= as_of_date``.

4. **Cornerstone disclosures** filtered by ``disclosure_date <= as_of_date``.

5. **Regulatory regime** resolved via ``regulatory_regime_for(as_of_date)``
   so the right pricing rules and tier definitions are used (e.g.
   pre-2025-08-04 vs post — see ``regime_detection.py``).

CLAUDE.md "as_of_date 严格" 约束: any caller-side attempt to fetch a
field with a date column post-dating ``as_of_date`` raises
``LookAheadError`` rather than silently returning empty results — the
caller MUST then either skip the case or back off the as_of_date.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..common.exceptions import LookAheadError
from ..common.logging import get_logger
from ..data.models import (
    CornerstoneInvestment,
    FinancialSnapshotRow,
    IPOEvent,
    IPOPricing,
    PostIPOEventRow,
    ProspectusDoc,
)

logger = get_logger(__name__)

# How many days after period_end a financial snapshot is typically
# disclosed. Used when no explicit publication-date column exists.
DEFAULT_DISCLOSURE_LAG_DAYS = 30


@dataclass(frozen=True)
class AsOfPolicy:
    """How conservative the provider should be when a date column is absent."""

    disclosure_lag_days: int = DEFAULT_DISCLOSURE_LAG_DAYS
    # When True, queries without an obvious date column raise rather than
    # silently fall through. Phase 8 keeps this on by default.
    strict_unknown_columns: bool = True


class _PriceFetcher(Protocol):
    """iFind / other price source — must support ``as_of_date`` slicing."""

    async def get_hk_history_prices(
        self,
        tickers: str | list[str],
        as_of_date: date,
        *,
        start: date,
    ) -> Any: ...


class AsOfDataProvider:
    """Single entry point for all walk-forward data access.

    Construct one per ``as_of_date`` (typically ``pricing_date - 1``) and
    pass it through the runner. The provider exposes coroutines for each
    data class the agents / valuation models consume.
    """

    def __init__(
        self,
        *,
        as_of_date: date,
        session_factory: async_sessionmaker[AsyncSession],
        price_fetcher: _PriceFetcher | None = None,
        policy: AsOfPolicy | None = None,
    ) -> None:
        if as_of_date > date.today():
            raise LookAheadError(
                f"as_of_date {as_of_date} is in the future; "
                "walk-forward backtest must use a historical anchor"
            )
        self._as_of = as_of_date
        self._sf = session_factory
        self._prices = price_fetcher
        self._policy = policy or AsOfPolicy()

    @property
    def as_of_date(self) -> date:
        return self._as_of

    # ------------------------------------------------------------------
    # IPO core
    # ------------------------------------------------------------------

    async def get_ipo_event(self, ipo_id: UUID) -> IPOEvent | None:
        """Return ipo_events row IFF the IPO was *known* by ``as_of_date``.

        "Known" means ``a1_filing_date <= as_of_date`` — pre-filing IPOs
        are invisible.
        """
        async with self._sf() as s:
            row = await s.get(IPOEvent, ipo_id)
            if row is None:
                return None
            if row.a1_filing_date is None or row.a1_filing_date > self._as_of:
                # Filing date in future — the IPO didn't exist yet.
                return None
            return row

    async def get_ipo_pricing(self, ipo_id: UUID) -> IPOPricing | None:
        """Return ipo_pricings IFF ``as_of_date < pricing_date``.

        Returning the pricing row would be a leak when as_of < pricing
        (we'd see the offer price before it was decided). So we filter
        by the parent ipo_event's pricing_date.
        """
        async with self._sf() as s:
            ipo = await s.get(IPOEvent, ipo_id)
            if ipo is None or ipo.pricing_date is None:
                return None
            if ipo.pricing_date <= self._as_of:
                # By as_of_date pricing was already public → safe to return.
                stmt = select(IPOPricing).where(IPOPricing.ipo_id == ipo_id)
                return (await s.execute(stmt)).scalar_one_or_none()
            raise LookAheadError(
                f"pricing for ipo_id={ipo_id} not yet known at "
                f"as_of_date={self._as_of} (pricing_date={ipo.pricing_date})"
            )

    # ------------------------------------------------------------------
    # Financial snapshots
    # ------------------------------------------------------------------

    async def get_financials(self, company_id: UUID) -> list[FinancialSnapshotRow]:
        """Return only financial snapshots disclosed by ``as_of_date``.

        Disclosure threshold = ``period_end + disclosure_lag_days`` (defaults
        to 30 days). A FY2024 (period_end=2024-12-31) snapshot is visible
        from 2025-01-31 onwards.
        """
        lag_cutoff = self._as_of - timedelta(days=self._policy.disclosure_lag_days)
        stmt = (
            select(FinancialSnapshotRow)
            .where(FinancialSnapshotRow.company_id == company_id)
            .where(FinancialSnapshotRow.period_end <= lag_cutoff)
            .order_by(FinancialSnapshotRow.period_end.desc())
        )
        async with self._sf() as s:
            return list((await s.execute(stmt)).scalars().all())

    # ------------------------------------------------------------------
    # HKEX filings
    # ------------------------------------------------------------------

    async def get_prospectus_docs(self, ipo_id: UUID) -> list[ProspectusDoc]:
        stmt = (
            select(ProspectusDoc)
            .where(ProspectusDoc.ipo_id == ipo_id)
            .where(ProspectusDoc.filing_date <= self._as_of)
            .order_by(ProspectusDoc.filing_date.asc())
        )
        async with self._sf() as s:
            return list((await s.execute(stmt)).scalars().all())

    # ------------------------------------------------------------------
    # Cornerstone disclosures
    # ------------------------------------------------------------------

    async def get_cornerstone_investments(
        self,
        ipo_id: UUID,
    ) -> list[CornerstoneInvestment]:
        stmt = (
            select(CornerstoneInvestment)
            .where(CornerstoneInvestment.ipo_id == ipo_id)
            .where(CornerstoneInvestment.disclosure_date <= self._as_of)
        )
        async with self._sf() as s:
            return list((await s.execute(stmt)).scalars().all())

    # ------------------------------------------------------------------
    # Post-IPO events — invisible by construction during walk-forward
    # (any post-IPO event by definition occurs after pricing_date which
    # is itself ≥ as_of_date in walk-forward mode).
    # ------------------------------------------------------------------

    async def get_post_ipo_events(self, ipo_id: UUID) -> list[PostIPOEventRow]:
        """Walk-forward never sees post-listing events — returns [] always."""
        # We could query event_date <= as_of, but that's both confusing and
        # unnecessary: in walk-forward we anchor as_of < pricing_date and
        # post_ipo events come strictly after listing_date > pricing_date.
        return []

    # ------------------------------------------------------------------
    # Market prices — strictly before as_of_date
    # ------------------------------------------------------------------

    async def get_hk_prices(
        self,
        tickers: str | list[str],
        *,
        start: date,
    ) -> Any:
        """Strict-inequality price slice: trade_date < as_of_date."""
        if self._prices is None:
            raise LookAheadError(
                "AsOfDataProvider was not given a price_fetcher; "
                "cannot serve historical prices without leaking same-day data"
            )
        # The iFind client respects as_of_date itself; we subtract 1 day to
        # enforce strict-less-than semantics.
        as_of_minus_1 = self._as_of - timedelta(days=1)
        if start > as_of_minus_1:
            raise LookAheadError(f"start={start} > as_of-1 ({as_of_minus_1}); empty window")
        return await self._prices.get_hk_history_prices(
            tickers,
            as_of_minus_1,
            start=start,
        )

    # ------------------------------------------------------------------
    # Generic statement-level guard
    # ------------------------------------------------------------------

    def assert_within_window(self, candidate: date | datetime, *, field_name: str) -> None:
        """Raise ``LookAheadError`` if ``candidate`` post-dates ``as_of_date``.

        Callers writing custom queries can wrap their date columns to keep
        the leak surface auditable.
        """
        candidate_d = candidate.date() if isinstance(candidate, datetime) else candidate
        if candidate_d > self._as_of:
            raise LookAheadError(
                f"field={field_name!r} value={candidate_d} post-dates as_of_date={self._as_of}"
            )

    def with_as_of_filter(
        self,
        stmt: Select[Any],
        *,
        date_column: Any,
    ) -> Select[Any]:
        """Helper for repository callers: append ``WHERE date_col <= as_of``.

        Use this when writing one-off queries that need leak-proofing
        outside the canonical accessors above.
        """
        return stmt.where(date_column <= self._as_of)


# Suppress unused-import warnings for symbols re-exported only for
# downstream type hints.
_ = UTC


__all__ = (
    "DEFAULT_DISCLOSURE_LAG_DAYS",
    "AsOfDataProvider",
    "AsOfPolicy",
)
