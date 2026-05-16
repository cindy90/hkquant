"""Sponsor repository."""

from __future__ import annotations

from ..models import Sponsor
from .base import BaseRepository


class SponsorRepository(BaseRepository[Sponsor]):
    model = Sponsor

    async def find_by_name(self, name: str) -> Sponsor | None:
        return await self.find_one(name=name)
