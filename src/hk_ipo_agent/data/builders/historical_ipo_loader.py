"""Load HK IPO history into PostgreSQL.

Inherits from NACS v8 legacy assets per ADR 0005 §1:
- Primary source: PostgreSQL ``ipo_events`` / ``ipo_pricings`` / ``ipo_postmarket``
  populated from ``data/nacs_real.db`` by ``scripts/migrate_sqlite_to_pg.py``
  (399 IPOs as of 2026-05).
- Fallback / incremental: iFind SDK for IPOs newer than the SQLite snapshot
  cutoff, and for backfilling fields missing in SQLite.

This builder is the orchestration layer; the source-of-truth ETL is the
migrate script (one-shot) and Phase 7.5's scheduler (continuous). Builders
are invoked by ``scripts/update_knowledge_base.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from ...common.logging import get_logger
from ..database import async_session_factory
from ..repositories import IPOEventRepository

if TYPE_CHECKING:
    from .data.sources.ifind_client import IFindClient

log = get_logger(__name__)


@dataclass
class HistoricalLoadStats:
    existing: int
    new_from_ifind: int
    skipped_no_data: int


class HistoricalIPOLoader:
    """Loader that reconciles the PG ``ipo_events`` table with iFind.

    Order of precedence (ADR 0005 §1):
    1. PostgreSQL existing rows (NACS migration is the seed)
    2. iFind backfill for IPOs missing from PG
    3. Don't touch rows that look like NACS-migrated (preserve curation)
    """

    def __init__(self, ifind: IFindClient | None = None) -> None:
        self.ifind = ifind

    async def load_listed_between(
        self,
        start: date,
        as_of_date: date,
    ) -> HistoricalLoadStats:
        """Backfill IPOs listed in [start, as_of_date] that aren't already in PG.

        If ``ifind`` is None, runs in audit-only mode (counts but doesn't fetch).
        """
        factory = async_session_factory()
        async with factory() as session:
            ipo_repo = IPOEventRepository(session)
            existing = await ipo_repo.list_listed_between(start, as_of_date)
            existing_codes = {ipo.stock_code for ipo in existing if ipo.stock_code}
            log.info(
                "historical_ipo_existing_count",
                count=len(existing),
                range=f"{start.isoformat()}..{as_of_date.isoformat()}",
            )

            if self.ifind is None:
                return HistoricalLoadStats(
                    existing=len(existing),
                    new_from_ifind=0,
                    skipped_no_data=0,
                )

            ifind_result = await self.ifind.get_ipo_history(
                as_of_date=as_of_date,
                start=start,
            )
            new_count, skipped = await self._upsert_from_ifind(
                ifind_result, existing_codes, ipo_repo
            )
            await session.commit()
            return HistoricalLoadStats(
                existing=len(existing),
                new_from_ifind=new_count,
                skipped_no_data=skipped,
            )

    async def _upsert_from_ifind(
        self,
        ifind_result: object,
        existing_codes: set[str | None],
        repo: IPOEventRepository,
    ) -> tuple[int, int]:
        """Parse iFind result and upsert any IPO not already in PG.

        TODO Phase 2.1: implement against the actual iFind response shape.
        The repo migration is the source of truth for Phase 2; this is the
        forward-going incremental loader.
        """
        log.warning(
            "ifind_upsert_stub",
            note="Phase 2.1 will wire this once iFind credentials are provisioned",
        )
        return (0, 0)
