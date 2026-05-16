"""Prospectus repositories."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from ..models import ProspectusDoc, ProspectusExtractionRow
from .base import BaseRepository

if TYPE_CHECKING:
    from uuid import UUID


class ProspectusDocRepository(BaseRepository[ProspectusDoc]):
    model = ProspectusDoc

    async def list_for_ipo(self, ipo_id: UUID) -> list[ProspectusDoc]:
        stmt = (
            select(ProspectusDoc)
            .where(ProspectusDoc.ipo_id == ipo_id)
            .order_by(ProspectusDoc.filing_date.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class ProspectusExtractionRepository(BaseRepository[ProspectusExtractionRow]):
    model = ProspectusExtractionRow

    async def latest_for_prospectus(
        self,
        prospectus_id: UUID,
    ) -> ProspectusExtractionRow | None:
        stmt = (
            select(ProspectusExtractionRow)
            .where(ProspectusExtractionRow.prospectus_id == prospectus_id)
            .order_by(ProspectusExtractionRow.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
