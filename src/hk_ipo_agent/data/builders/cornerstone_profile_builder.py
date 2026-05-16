"""Build cornerstone investor profiles knowledge base.

Inherits from NACS v8 legacy assets per ADR 0005 §1 and §2:
- Primary seed: 2,014 cornerstone investors in ``cornerstone_investors``
  (post NACS SQLite -> PG migration), 1,770 aliases merged into the JSONB
  ``aliases`` field, 2,560 IPO-investor links in ``cornerstone_investments``.
- Derived: ultimate_holder clustering (NACS v7 "Cluster Bonus" data basis)
  used by ``agents/cornerstone_signal_agent.py`` to detect industry-capital
  syndicates splitting across multiple SPVs.
- NOT migrated: ``cornerstone_performance_asof`` (31k rows) — recomputed
  in Phase 7.5 by ``prediction_registry/outcome_tracker.py``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from ...common.logging import get_logger
from ..database import async_session_factory
from ..models import CornerstoneInvestor
from ..repositories import (
    CornerstoneInvestmentRepository,
    CornerstoneInvestorRepository,
)

if TYPE_CHECKING:
    from uuid import UUID

log = get_logger(__name__)


@dataclass
class CornerstoneClusterReport:
    """Per-IPO ultimate_holder cluster summary (ADR 0005 §2 Cluster Bonus)."""

    ipo_id: UUID
    holder_to_count: dict[str, int]

    @property
    def max_cluster_size(self) -> int:
        return max(self.holder_to_count.values(), default=0)

    @property
    def has_cluster(self) -> bool:
        return self.max_cluster_size >= 2


class CornerstoneProfileBuilder:
    """Read-side façade over the migrated cornerstone profiles.

    Phase 2: provides query primitives. The actual ETL is done one-shot by
    ``scripts/migrate_sqlite_to_pg.py``. Phase 7.5 will plug in continuous
    refresh from iFind / 披露易.
    """

    async def list_unique_holders(self) -> list[str]:
        """Return all distinct ``ultimate_holder`` values present in PG."""
        factory = async_session_factory()
        async with factory() as session:
            stmt = (
                select(CornerstoneInvestor.ultimate_holder)
                .where(CornerstoneInvestor.ultimate_holder.is_not(None))
                .distinct()
            )
            result = await session.execute(stmt)
            return [row[0] for row in result.all() if row[0]]

    async def cluster_report_for_ipo(self, ipo_id: UUID) -> CornerstoneClusterReport:
        """Return cluster sizes per ultimate_holder for one IPO."""
        factory = async_session_factory()
        async with factory() as session:
            repo = CornerstoneInvestmentRepository(session)
            counts = await repo.ultimate_holder_clusters_for_ipo(ipo_id)
            return CornerstoneClusterReport(ipo_id=ipo_id, holder_to_count=counts)

    async def coverage_stats(self) -> dict[str, int]:
        """Quick sanity stats for the knowledge base."""
        factory = async_session_factory()
        async with factory() as session:
            inv_repo = CornerstoneInvestorRepository(session)
            link_repo = CornerstoneInvestmentRepository(session)
            inv_count = await inv_repo.count()
            link_count = await link_repo.count()

            stmt_with_holder = select(func.count()).select_from(
                CornerstoneInvestor
            ).where(CornerstoneInvestor.ultimate_holder.is_not(None))
            with_holder = int((await session.execute(stmt_with_holder)).scalar_one())

            stmt_aliased = select(func.count()).select_from(
                CornerstoneInvestor
            ).where(CornerstoneInvestor.aliases.is_not(None))
            with_aliases = int((await session.execute(stmt_aliased)).scalar_one())

        return {
            "investor_count": inv_count,
            "investment_count": link_count,
            "with_ultimate_holder": with_holder,
            "with_aliases": with_aliases,
        }

    async def histogram_by_category(self) -> dict[str, int]:
        """Distribution of investors by ``category`` (sovereign / hedge / etc)."""
        factory = async_session_factory()
        async with factory() as session:
            stmt = (
                select(CornerstoneInvestor.category, func.count())
                .group_by(CornerstoneInvestor.category)
            )
            result = await session.execute(stmt)
            buckets: dict[str, int] = defaultdict(int)
            for category, count in result.all():
                buckets[category or "unknown"] = int(count)
        return dict(buckets)


__all__ = ("CornerstoneClusterReport", "CornerstoneProfileBuilder")
