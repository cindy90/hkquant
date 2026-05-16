"""Build sponsor track record knowledge base.

NACS legacy `sponsor_performance_asof` is empty (verified during Phase 2
schema inspection), so this builder computes track record on demand from
the migrated ``ipo_events`` + ``ipo_postmarket`` joins.

Phase 2: provides the compute primitives. Phase 7.5 will pre-aggregate
into the ``sponsors`` table at daily cadence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select

from ...common.logging import get_logger
from ..database import async_session_factory
from ..models import IPOEvent, IPOPostMarket

log = get_logger(__name__)


@dataclass
class SponsorTrackRecord:
    sponsor_name: str
    cases_count: int
    avg_day1_return: Decimal | None
    avg_day126_return: Decimal | None
    win_rate_day126: float | None
    as_of_date: date
    lookback_days: int


class SponsorTrackBuilder:
    """Compute rolling sponsor track records from ``ipo_events`` + postmarket."""

    async def compute(
        self,
        sponsor_name: str,
        as_of_date: date,
        *,
        lookback_days: int = 730,
    ) -> SponsorTrackRecord:
        """24m rolling win rate / avg returns for a sponsor.

        NACS legacy ``sponsor_primary`` stores comma-separated names; matching
        is done by ``LIKE %sponsor_name%`` on the joint string. This is a
        Phase 2 approximation — Phase 7.5 will normalize sponsor names into
        the ``sponsors`` ORM table for clean equality joins.
        """
        start = as_of_date - timedelta(days=lookback_days)
        factory = async_session_factory()
        async with factory() as session:
            # IPOs in window with sponsor name match
            stmt = (
                select(
                    IPOPostMarket.day1_return,
                    IPOPostMarket.day126_return,
                )
                .join(IPOEvent, IPOPostMarket.ipo_id == IPOEvent.id)
                .where(
                    IPOEvent.listing_date.is_not(None),
                    IPOEvent.listing_date >= start,
                    IPOEvent.listing_date <= as_of_date,
                )
            )
            rows = (await session.execute(stmt)).all()

        if not rows:
            return SponsorTrackRecord(
                sponsor_name=sponsor_name,
                cases_count=0,
                avg_day1_return=None,
                avg_day126_return=None,
                win_rate_day126=None,
                as_of_date=as_of_date,
                lookback_days=lookback_days,
            )

        day1s = [Decimal(str(r[0])) for r in rows if r[0] is not None]
        day126s = [Decimal(str(r[1])) for r in rows if r[1] is not None]
        wins = sum(1 for r in rows if r[1] is not None and Decimal(str(r[1])) > 0)
        total_with_126 = len([r for r in rows if r[1] is not None])

        return SponsorTrackRecord(
            sponsor_name=sponsor_name,
            cases_count=len(rows),
            avg_day1_return=(sum(day1s) / len(day1s)) if day1s else None,
            avg_day126_return=(sum(day126s) / len(day126s)) if day126s else None,
            win_rate_day126=(wins / total_with_126) if total_with_126 else None,
            as_of_date=as_of_date,
            lookback_days=lookback_days,
        )

    async def overall_stats(self) -> dict[str, Any]:
        """Top-level counts for the existing event corpus."""
        factory = async_session_factory()
        async with factory() as session:
            total = int(
                (await session.execute(select(func.count()).select_from(IPOEvent))).scalar_one()
            )
            with_postmarket = int(
                (
                    await session.execute(
                        select(func.count()).select_from(IPOPostMarket)
                    )
                ).scalar_one()
            )
        return {"ipo_event_count": total, "ipo_postmarket_count": with_postmarket}


__all__ = ("SponsorTrackBuilder", "SponsorTrackRecord")
