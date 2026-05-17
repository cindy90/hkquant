"""Build comparable companies pool.

**Status of the pool after Phase 2**: the ``comparable_companies`` PG table
exists (created by Phase 1 migration + widened by Phase 2 migration) but
is **intentionally empty** as of Phase 2 close. NACS SQLite has no
comparable-company corpus to seed from, and the design is to build the
pool **dynamically on-demand** in Phase 4 when ``valuation/comparable.py``
needs peers for an IPO under analysis. This avoids stale peer snapshots
and aligns with PROJECT_SPEC.md §3.4 "支持跨市场（A/H/US ADR）的可比".

Phase 4 implementation plan:
1. ``valuation/comparable.py`` calls ``ComparablePoolBuilder.refresh_industry()``
   when needed (industry not yet in PG, or snapshot older than N days).
2. ``refresh_industry()`` invokes iFind ``get_comparable_companies()`` and
   upserts into the PG table.
3. Cache invalidation triggered by daily scheduler (Phase 7.5).

Phase 2 scope: builder interface + iFind passthrough stub. Real ingest logic
gates on iFind credentials (see Phase 2.1 / ``data/sources/ifind_client.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from ...common.logging import get_logger
from ..database import async_session_factory
from ..repositories import ComparableCompanyRepository

if TYPE_CHECKING:
    from ..sources.ifind_client import IFindClient

log = get_logger(__name__)


@dataclass
class ComparablePoolStats:
    industry_code: str
    existing: int
    fetched: int


class ComparablePoolBuilder:
    """Ingest peer-comp pool by industry code; depends on iFind for the seed."""

    def __init__(self, ifind: IFindClient | None = None) -> None:
        self.ifind = ifind

    async def refresh_industry(
        self,
        industry_code: str,
        as_of_date: date,
        *,
        market: str = "HK",
    ) -> ComparablePoolStats:
        """Refresh the pool for one industry. Phase 2 stub: counts only."""
        factory = async_session_factory()
        async with factory() as session:
            repo = ComparableCompanyRepository(session)
            existing = await repo.list_for_industry(industry_code, market=market)
            log.info(
                "comparable_pool_existing",
                industry=industry_code,
                market=market,
                count=len(existing),
            )

            if self.ifind is None:
                return ComparablePoolStats(
                    industry_code=industry_code,
                    existing=len(existing),
                    fetched=0,
                )

            ifind_result = await self.ifind.get_comparable_companies(
                industry_code, as_of_date=as_of_date, market=market
            )
            fetched = await self._ingest(ifind_result, repo)
            await session.commit()
            return ComparablePoolStats(
                industry_code=industry_code,
                existing=len(existing),
                fetched=fetched,
            )

    async def _ingest(self, ifind_result: object, repo: ComparableCompanyRepository) -> int:
        """Parse iFind comparable list.

        R3-1: previously a ``return 0`` stub which masked the lack of
        implementation behind the ``ifind != None`` path. Now raises to
        force callers to either run in audit-only mode or wait for the
        wiring described in ADR 0018.
        """
        raise NotImplementedError(
            "iFind comparable-pool ingest not yet wired — see ADR 0018 and "
            "docs/PLAN_post_v1.0.md §5 R3-1. Use audit-only mode "
            "(ComparablePoolBuilder(ifind=None)) until then."
        )
