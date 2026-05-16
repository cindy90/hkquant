"""Benchmark service tests — Phase 7.5b per ADR 0012."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from hk_ipo_agent.prediction_registry.benchmarks import (
    BenchmarkPriceService,
    BenchmarkReturns,
    _close_series,
    _nearest_close,
)


class _StubFetcher:
    """Records call args; returns canned payloads."""

    def __init__(
        self,
        *,
        index_payload: Any = None,
        prices_payload: Any = None,
    ) -> None:
        self._index_payload = index_payload
        self._prices_payload = prices_payload
        self.index_calls: list[tuple] = []
        self.price_calls: list[tuple] = []

    async def get_macro_index_history(
        self, as_of_date, *, start, index_keys=None,
    ):
        self.index_calls.append((as_of_date, start, tuple(index_keys or ())))
        return self._index_payload

    async def get_hk_history_prices(
        self, tickers, as_of_date, *, start,
    ):
        self.price_calls.append((tuple(tickers) if isinstance(tickers, list) else tickers, as_of_date, start))
        return self._prices_payload


def _payload(rows: list[dict]) -> dict:
    return {"data": rows}


def test_close_series_normalises_dict_and_list_inputs() -> None:
    rows = [
        {"time": "2026-05-01", "thscode": "HSI.HI", "close": 18000.0},
        {"time": "2026-05-02", "thscode": "HSI.HI", "close": 18200.0},
    ]
    series_dict = _close_series({"data": rows})
    series_list = _close_series(rows)
    assert series_dict == series_list
    assert series_dict[date(2026, 5, 1)]["HSI.HI"] == 18000.0


def test_nearest_close_backfills_weekends() -> None:
    series = {date(2026, 5, 1): {"X": 100.0}}
    # 2026-05-03 is a Sunday — backfill picks Friday's close.
    assert _nearest_close(series, "X", date(2026, 5, 3)) == 100.0
    # Too far back → None.
    assert _nearest_close(series, "X", date(2026, 5, 10)) is None


@pytest.mark.asyncio
async def test_compute_returns_three_benchmarks() -> None:
    index_rows = [
        {"time": "2026-01-01", "thscode": "HSI.HI", "close": 20000.0},
        {"time": "2026-03-01", "thscode": "HSI.HI", "close": 21000.0},
    ]
    peer_rows = [
        {"time": "2026-01-01", "thscode": "0700.HK", "close": 400.0},
        {"time": "2026-03-01", "thscode": "0700.HK", "close": 440.0},
        {"time": "2026-01-01", "thscode": "0981.HK", "close": 50.0},
        {"time": "2026-03-01", "thscode": "0981.HK", "close": 52.0},
    ]
    fetcher = _StubFetcher(
        index_payload=_payload(index_rows),
        prices_payload=_payload(peer_rows),
    )

    # Need two index calls (HSI + HSTECH). Re-use same fixture for both — the
    # stub returns the same payload either way; we tolerate that here.
    svc = BenchmarkPriceService(fetcher, peer_limit=10)
    out = await svc.compute(
        t0=date(2026, 1, 1), tn=date(2026, 3, 1),
        industry_peers=["0700.HK", "0981.HK"],
    )
    assert isinstance(out, BenchmarkReturns)
    # HSI = 20000 -> 21000 = +5%
    assert out.hsi == Decimal("0.050000")
    # Peer median: 0700 +10%, 0981 +4%, median = (0.04 + 0.10) / 2 = 0.07
    assert out.industry_median == Decimal("0.070000")


@pytest.mark.asyncio
async def test_compute_returns_none_on_missing_index_payload() -> None:
    fetcher = _StubFetcher(index_payload={}, prices_payload={})
    svc = BenchmarkPriceService(fetcher)
    out = await svc.compute(t0=date(2026, 1, 1), tn=date(2026, 3, 1))
    assert out.hsi is None
    assert out.hstech is None
    assert out.industry_median is None


@pytest.mark.asyncio
async def test_compute_validates_window_ordering() -> None:
    svc = BenchmarkPriceService(_StubFetcher())
    with pytest.raises(ValueError, match="must be >="):
        await svc.compute(t0=date(2026, 3, 1), tn=date(2026, 1, 1))


@pytest.mark.asyncio
async def test_industry_skipped_when_peers_empty() -> None:
    fetcher = _StubFetcher(index_payload=_payload([]), prices_payload=_payload([]))
    svc = BenchmarkPriceService(fetcher)
    out = await svc.compute(t0=date(2026, 1, 1), tn=date(2026, 3, 1), industry_peers=[])
    assert out.industry_median is None
    # No price call should have been made.
    assert fetcher.price_calls == []
