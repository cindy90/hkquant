"""Cornerstone repositories per PROJECT_SPEC.md §3.4 + ADR 0005 §2 (Cluster Bonus)."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from sqlalchemy import select

from ..models import CornerstoneInvestment, CornerstoneInvestor
from .base import BaseRepository

if TYPE_CHECKING:
    from uuid import UUID


class CornerstoneInvestorRepository(BaseRepository[CornerstoneInvestor]):
    model = CornerstoneInvestor

    async def find_by_canonical_name(self, name: str) -> CornerstoneInvestor | None:
        """Try Chinese first, then English."""
        zh = await self.find_one(name_zh=name)
        if zh is not None:
            return zh
        return await self.find_one(name_en=name)

    async def list_by_ultimate_holder(self, holder: str) -> list[CornerstoneInvestor]:
        return await self.list(ultimate_holder=holder)


class CornerstoneInvestmentRepository(BaseRepository[CornerstoneInvestment]):
    model = CornerstoneInvestment

    async def list_for_ipo(self, ipo_id: UUID) -> list[CornerstoneInvestment]:
        return await self.list(ipo_id=ipo_id)

    async def list_for_investor(self, investor_id: UUID) -> list[CornerstoneInvestment]:
        return await self.list(investor_id=investor_id)

    async def ultimate_holder_clusters_for_ipo(self, ipo_id: UUID) -> dict[str, int]:
        """Return ``{ultimate_holder: cornerstone_count}`` for one IPO.

        Implements the data-side of ADR 0005 §2 Cluster Bonus: callers use
        this to detect when 2+ cornerstones share the same ultimate_holder
        (industry-capital syndicates via multiple SPVs).
        """
        stmt = (
            select(CornerstoneInvestor.ultimate_holder)
            .join(
                CornerstoneInvestment,
                CornerstoneInvestment.investor_id == CornerstoneInvestor.id,
            )
            .where(CornerstoneInvestment.ipo_id == ipo_id)
        )
        result = await self.session.execute(stmt)
        counts: dict[str, int] = defaultdict(int)
        for (holder,) in result.all():
            if holder:
                counts[holder] += 1
        return dict(counts)
