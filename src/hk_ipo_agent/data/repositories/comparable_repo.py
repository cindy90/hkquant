"""Comparable companies repository."""

from __future__ import annotations

from ..models import ComparableCompany
from .base import BaseRepository


class ComparableCompanyRepository(BaseRepository[ComparableCompany]):
    model = ComparableCompany

    async def list_for_industry(
        self,
        industry_code: str,
        *,
        market: str | None = None,
    ) -> list[ComparableCompany]:
        filters: dict[str, object] = {"industry_code": industry_code}
        if market is not None:
            filters["market"] = market
        return await self.list(**filters)
