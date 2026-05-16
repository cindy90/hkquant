"""Three-benchmark price service per PROJECT_SPEC.md §3.11.

``outcome_tracker.py`` calls into this module to translate raw stock
returns into the *relative* returns required by `prediction_outcomes`
(vs HSI, HSTECH, and the industry comparable-pool median).

Phase 7.5b scope:
- HSI / HSTECH index returns via iFind ``get_macro_index_history``
- Industry comparable-pool median return: pool resolved via the existing
  ``comparable_pool_builder`` snapshot (Phase 2); price series via iFind
  ``get_hk_history_prices`` for each peer.

Design choices:
- The class is stateless apart from the injected iFind client.
- Returns are computed as ``(close_t / close_t0) - 1``; missing prices
  forward-fill within tolerance (1 trading day) then drop NaN.
- Pool size cap (default 30) keeps API quota in check; smaller is OK.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Protocol

from ..common.logging import get_logger

logger = get_logger(__name__)

# Default industry-peer cap for the median benchmark.
DEFAULT_PEER_LIMIT = 30


@dataclass(frozen=True)
class BenchmarkReturns:
    """Returns over the [t0, checkpoint] window for all 3 benchmarks.

    Values are Decimal fractions, e.g. ``Decimal("0.0524")`` for +5.24%.
    Any benchmark that couldn't be priced is ``None`` so the caller
    decides how to handle the gap (typically: log + persist NULL).
    """

    hsi: Decimal | None
    hstech: Decimal | None
    industry_median: Decimal | None


class _PriceFetcher(Protocol):
    """Subset of ``IFindClient`` we actually need (test seam)."""

    async def get_hk_history_prices(
        self,
        tickers: str | list[str],
        as_of_date: date,
        *,
        start: date,
    ) -> Any: ...

    async def get_macro_index_history(
        self,
        as_of_date: date,
        *,
        start: date,
        index_keys: list[str] | None = None,
    ) -> Any: ...


def _close_series(payload: Any) -> dict[date, dict[str, float]]:
    """Normalise the iFind ``THS_HistoryQuotes`` payload to ``{date: {ticker: close}}``.

    iFind wraps responses in ``{'data': [{...}, ...]}`` with each row
    containing ``thscode``, ``time`` and the requested indicators. Sticking
    to the dict shape (rather than pandas) keeps the unit-test surface
    small.
    """
    rows: list[dict[str, Any]]
    if isinstance(payload, dict):
        rows = payload.get("data") or payload.get("rows") or []
    elif isinstance(payload, list):
        rows = payload
    else:
        return {}

    out: dict[date, dict[str, float]] = {}
    for row in rows:
        d_raw = row.get("time") or row.get("date") or row.get("trade_date")
        if not d_raw:
            continue
        d = date.fromisoformat(d_raw) if isinstance(d_raw, str) else d_raw
        ticker = row.get("thscode") or row.get("ticker") or ""
        close = row.get("close")
        if close is None:
            continue
        out.setdefault(d, {})[ticker] = float(close)
    return out


def _return_pct(t0_close: float, tn_close: float) -> Decimal:
    if t0_close == 0:
        return Decimal("0")
    return Decimal(str(tn_close / t0_close - 1.0)).quantize(Decimal("0.000001"))


def _nearest_close(
    series: dict[date, dict[str, float]],
    ticker: str,
    target: date,
    *,
    backfill_days: int = 5,
) -> float | None:
    """Return the closest ``close`` for ``ticker`` at-or-before ``target``.

    Trading-day misalignment (weekends, holidays) is unavoidable; we
    look back up to ``backfill_days`` calendar days before giving up.
    """
    for shift in range(backfill_days + 1):
        d = target - timedelta(days=shift)
        if d in series and ticker in series[d]:
            return series[d][ticker]
    return None


class BenchmarkPriceService:
    """Resolves the 3 benchmark returns over a [t0, tn] window."""

    def __init__(
        self,
        ifind: _PriceFetcher,
        *,
        peer_limit: int = DEFAULT_PEER_LIMIT,
    ) -> None:
        self._ifind = ifind
        self._peer_limit = peer_limit

    async def compute(
        self,
        *,
        t0: date,
        tn: date,
        industry_peers: list[str] | None = None,
    ) -> BenchmarkReturns:
        """Returns over [t0, tn] for HSI, HSTECH and the industry-peer median.

        ``industry_peers`` is the list of HK tickers in the IPO's
        comparable pool (e.g. ``["0700.HK", "0981.HK", ...]``). Pass
        ``None`` or ``[]`` to skip the industry benchmark.
        """
        if tn < t0:
            raise ValueError(f"tn ({tn}) must be >= t0 ({t0})")

        hsi = await self._index_return("HSI", t0, tn)
        hstech = await self._index_return("HSTECH", t0, tn)
        industry_median: Decimal | None = None
        if industry_peers:
            industry_median = await self._industry_median_return(
                industry_peers[: self._peer_limit], t0, tn
            )
        return BenchmarkReturns(hsi=hsi, hstech=hstech, industry_median=industry_median)

    async def _index_return(
        self, index_key: str, t0: date, tn: date
    ) -> Decimal | None:
        try:
            payload = await self._ifind.get_macro_index_history(
                tn, start=t0, index_keys=[index_key]
            )
        except Exception as exc:
            logger.warning(
                "benchmark_index_fetch_failed",
                index_key=index_key, t0=t0.isoformat(), tn=tn.isoformat(),
                error=str(exc),
            )
            return None
        series = _close_series(payload)
        if not series:
            return None
        # Index ticker is the only key in each row.
        sample_row = next(iter(series.values()))
        if not sample_row:
            return None
        ticker = next(iter(sample_row.keys()))
        t0_close = _nearest_close(series, ticker, t0)
        tn_close = _nearest_close(series, ticker, tn)
        if t0_close is None or tn_close is None:
            return None
        return _return_pct(t0_close, tn_close)

    async def _industry_median_return(
        self, peers: list[str], t0: date, tn: date
    ) -> Decimal | None:
        try:
            payload = await self._ifind.get_hk_history_prices(
                peers, tn, start=t0
            )
        except Exception as exc:
            logger.warning(
                "benchmark_industry_fetch_failed",
                peers_count=len(peers), t0=t0.isoformat(), tn=tn.isoformat(),
                error=str(exc),
            )
            return None
        series = _close_series(payload)
        if not series:
            return None
        peer_returns: list[Decimal] = []
        for ticker in peers:
            t0_c = _nearest_close(series, ticker, t0)
            tn_c = _nearest_close(series, ticker, tn)
            if t0_c is not None and tn_c is not None and t0_c > 0:
                peer_returns.append(_return_pct(t0_c, tn_c))
        if not peer_returns:
            return None
        peer_returns.sort()
        mid = len(peer_returns) // 2
        if len(peer_returns) % 2 == 1:
            return peer_returns[mid]
        return (peer_returns[mid - 1] + peer_returns[mid]) / Decimal("2")


__all__ = (
    "DEFAULT_PEER_LIMIT",
    "BenchmarkPriceService",
    "BenchmarkReturns",
)
