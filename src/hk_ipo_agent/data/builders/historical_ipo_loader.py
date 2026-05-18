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

from sqlalchemy.ext.asyncio import AsyncSession

from ...common.logging import get_logger
from ..database import async_session_factory
from ..repositories import IPOEventRepository

if TYPE_CHECKING:
    # R7-1: the original path ``.data.sources.ifind_client`` was broken
    # (would resolve to ``hk_ipo_agent.data.builders.data.sources.ifind_client``).
    # The correct relative path is up one level (builders → data), then into
    # sources — ``..sources.ifind_client``.
    from ..sources.ifind_client import IFindClient

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

    def __init__(
        self,
        ifind: IFindClient | None = None,
        *,
        session: AsyncSession | None = None,
    ) -> None:
        """R7-9: optional ``session`` injection for transaction composition.

        When None (back-compat default), each method opens its own session
        via the factory.
        """
        self.ifind = ifind
        self._session = session

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

        R3-1: previously returned ``(0, 0)`` as a stub. That made the
        ``ifind != None`` path silently equivalent to the ``ifind is None``
        audit-only path — a caller wiring real credentials but hitting an
        unimplemented response-shape parse would silently get "no new
        IPOs" instead of an error. Per PLAN option B we now raise so the
        failure is loud; full wiring is tracked under ADR 0018.

        ADR 0005 §1 prediction "iFind 仅作补漏 / 一次性 ETL 为主" still
        holds — the PG seed from migrate_sqlite_to_pg.py is the canonical
        source. This method is the forward-going incremental loader and
        ADR 0018 describes when / how it lands.
        """
        raise NotImplementedError(
            "iFind incremental upsert not yet wired — see ADR 0018 and "
            "docs/PLAN_post_v1.0.md §5 R3-1. Use audit-only mode "
            "(HistoricalIPOLoader(ifind=None)) until then. "
            f"got {len(getattr(ifind_result, '__dict__', {})) if ifind_result else 0} "
            "field(s) in payload."
        )
