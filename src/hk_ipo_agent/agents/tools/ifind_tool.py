"""iFind data tool — narrow projection over ``data.sources.ifind_client.IFindClient``.

Per PROJECT_SPEC.md §3.6. Agents pull macro / peer / IPO history through
this thin wrapper so that test mocking is easy (mock the tool, not the
underlying SDK).
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...data.sources.ifind_client import IFindClient


class IFindTool:
    """Inject this into ``AgentContext.ifind_tool``.

    All methods are async pass-throughs to ``IFindClient``; we re-export
    only the subset agents are allowed to use, keeping the API surface
    contained and test-mockable.

    Signatures match the existing ``IFindClient`` API (as_of_date guard
    + lookback) — see ``data/sources/ifind_client.py``.
    """

    def __init__(self, client: IFindClient) -> None:
        self._client = client

    async def ipo_history(
        self,
        *,
        as_of_date: date,
        start: date,
        pool_filter: str = "AHK",
    ) -> Any:
        """Historical HK IPO listing window. Used by ``policy_agent`` to
        compute the NACS Regime Gate."""
        return await self._client.get_ipo_history(
            as_of_date=as_of_date, start=start, pool_filter=pool_filter
        )

    async def macro_index_history(
        self,
        *,
        as_of_date: date,
        start: date,
        index_keys: list[str] | None = None,
    ) -> Any:
        """HSI / HSTECH / HSCEI history for macro indicators."""
        return await self._client.get_macro_index_history(
            as_of_date=as_of_date, start=start, index_keys=index_keys
        )

    async def valuation_snapshot(
        self,
        tickers: str | list[str],
        as_of_date: date,
    ) -> Any:
        """Latest PE / PS / PB snapshot for one or more peers."""
        return await self._client.get_valuation_snapshot(tickers=tickers, as_of_date=as_of_date)

    async def ah_premium_history(
        self,
        *,
        ticker_pair: tuple[str, str],
        as_of_date: date,
        lookback_days: int = 365,
    ) -> Any:
        """Daily H + A close pair series for AH premium computation."""
        return await self._client.get_ah_premium_history(
            ticker_pair=ticker_pair, as_of_date=as_of_date, lookback_days=lookback_days
        )


__all__ = ("IFindTool",)
