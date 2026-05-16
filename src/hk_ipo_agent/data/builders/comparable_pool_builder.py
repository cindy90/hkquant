"""Build comparable companies pool.

Phase 2: ingest seed comparable companies from iFind for known industry codes.
NACS legacy didn't track a separate ``comparable_companies`` table, so this
builder starts fresh from iFind.

Phase 4 (valuation/comparable.py) will read from this builder's output.
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

    async def _ingest(
        self, ifind_result: object, repo: ComparableCompanyRepository
    ) -> int:
        """Parse iFind comparable list. Phase 2.1: stub."""
        log.warning(
            "comparable_ingest_stub",
            note="Phase 2.1 wires this once iFind credentials are provisioned",
        )
        return 0
