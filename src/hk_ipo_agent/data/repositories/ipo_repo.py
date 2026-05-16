"""IPO repositories per PROJECT_SPEC.md §3.4."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import select

from ..models import IPOEvent, IPOPostMarket, IPOPricing
from .base import BaseRepository

if TYPE_CHECKING:
    from collections.abc import Sequence


class IPOEventRepository(BaseRepository[IPOEvent]):
    model = IPOEvent

    async def find_by_stock_code(self, stock_code: str) -> IPOEvent | None:
        return await self.find_one(stock_code=stock_code)

    async def list_listed_between(
        self,
        start: date,
        end: date,
        *,
        listing_type: str | None = None,
    ) -> Sequence[IPOEvent]:
        stmt = select(IPOEvent).where(
            IPOEvent.listing_date.is_not(None),
            IPOEvent.listing_date >= start,
            IPOEvent.listing_date <= end,
        )
        if listing_type is not None:
            stmt = stmt.where(IPOEvent.listing_type == listing_type)
        stmt = stmt.order_by(IPOEvent.listing_date.asc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class IPOPricingRepository(BaseRepository[IPOPricing]):
    model = IPOPricing


class IPOPostMarketRepository(BaseRepository[IPOPostMarket]):
    model = IPOPostMarket
