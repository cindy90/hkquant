"""T+N checkpoint outcome tracker per PROJECT_SPEC.md §3.11 + §11.

For each (snapshot, checkpoint_day) the tracker:
1. fetches close prices for ``listing_date`` and ``listing_date + N`` via iFind
2. computes raw + relative returns vs the 3 benchmarks (HSI / HSTECH /
   industry-peer median)
3. delegates event-window scan to ``event_detector`` (optional, injected)
4. writes ``prediction_outcomes`` — UNIQUE(snapshot_id, checkpoint_day)
   gives the idempotency anchor

CLAUDE.md prediction-lifecycle constraints:
- checkpoints are fixed (``CHECKPOINT_DAYS``); -1 = terminal outcome
- missing the exact target date is acceptable — back-fill weekends /
  holidays inside ``BenchmarkPriceService`` (up to 5 calendar days)

Phase 7.5b ships the per-(snapshot, day) ``track`` coroutine only. The
"loop over active snapshots, decide which days are due" orchestration
is the ``daily_scheduler``'s job in 7.5d.
"""

from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..common.enums import CHECKPOINT_DAYS
from ..common.logging import get_logger
from ..common.schemas import PostIPOEvent, PredictionSnapshot
from ..data.models import PostIPOEventRow, PredictionOutcomeRow
from .benchmarks import BenchmarkPriceService, BenchmarkReturns, _close_series, _nearest_close

logger = get_logger(__name__)


@dataclass(frozen=True)
class TrackResult:
    """Return value of ``OutcomeTracker.track``.

    ``skipped`` means the (snapshot, day) outcome already exists or the
    stock isn't priced yet (e.g. ``listing_date`` in the future).
    """

    outcome_id: UUID | None
    skipped: bool
    reason: str | None = None


class _SnapshotResolver(Protocol):
    """Subset of registry we need (test seam)."""

    async def get_snapshot(self, snapshot_id: UUID) -> PredictionSnapshot: ...


class _EventDetector(Protocol):
    async def scan_events(
        self,
        *,
        ipo_id: UUID,
        stock_code: str,
        window_start: date,
        window_end: date,
    ) -> list[PostIPOEvent]: ...


class _PriceFetcher(Protocol):
    async def get_hk_history_prices(
        self,
        tickers: str | list[str],
        as_of_date: date,
        *,
        start: date,
    ) -> Any: ...


class OutcomeTracker:
    """Persists a single (snapshot, checkpoint_day) outcome row.

    Inject the session_factory + benchmarks + price fetcher so unit tests
    can substitute in-memory fakes without touching a real DB or iFind.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        snapshot_resolver: _SnapshotResolver,
        benchmarks: BenchmarkPriceService,
        price_fetcher: _PriceFetcher,
        event_detector: _EventDetector | None = None,
    ) -> None:
        self._sf = session_factory
        self._registry = snapshot_resolver
        self._benchmarks = benchmarks
        self._prices = price_fetcher
        self._events = event_detector

    async def track(
        self,
        *,
        snapshot_id: UUID,
        checkpoint_day: int,
        stock_code: str,
        listing_date: date,
        industry_peers: list[str] | None = None,
        ipo_id: UUID | None = None,
    ) -> TrackResult:
        """Record a checkpoint outcome. Idempotent on (snapshot_id, day)."""
        if checkpoint_day != -1 and checkpoint_day not in CHECKPOINT_DAYS:
            raise ValueError(
                f"checkpoint_day {checkpoint_day} not in spec §11 fixed set: "
                f"{CHECKPOINT_DAYS} (or -1 for terminal)"
            )

        if await self._exists(snapshot_id, checkpoint_day):
            return TrackResult(outcome_id=None, skipped=True, reason="already_recorded")

        # R8-7: spec CHECKPOINT_DAYS are trading-day indices (skip weekends),
        # not calendar days. ``BenchmarkPriceService.get_trading_day_offset``
        # advances past weekends; the calendar-day -1 terminal sentinel is
        # passed through as max(...,0).
        target_date = BenchmarkPriceService.get_trading_day_offset(
            listing_date, max(checkpoint_day, 0)
        )

        # 1. Stock raw return
        try:
            prices_payload = await self._prices.get_hk_history_prices(
                stock_code, target_date, start=listing_date
            )
        except Exception as exc:
            logger.warning(
                "outcome_price_fetch_failed",
                snapshot_id=str(snapshot_id),
                stock_code=stock_code,
                checkpoint_day=checkpoint_day,
                error=str(exc),
            )
            return TrackResult(outcome_id=None, skipped=True, reason=f"price_fetch_failed: {exc}")

        series = _close_series(prices_payload)
        t0_close = _nearest_close(series, stock_code, listing_date)
        tn_close = _nearest_close(series, stock_code, target_date)
        if t0_close is None or tn_close is None:
            return TrackResult(outcome_id=None, skipped=True, reason="missing_close")

        return_since_listing = Decimal(str(tn_close / t0_close - 1.0)).quantize(Decimal("0.000001"))
        max_dd = self._max_drawdown(series, stock_code, listing_date, target_date)

        # 2. Benchmark relative returns
        bm: BenchmarkReturns = await self._benchmarks.compute(
            t0=listing_date,
            tn=target_date,
            industry_peers=industry_peers,
        )
        rel_hsi = (return_since_listing - bm.hsi) if bm.hsi is not None else None
        rel_hstech = (return_since_listing - bm.hstech) if bm.hstech is not None else None
        rel_industry = (
            (return_since_listing - bm.industry_median) if bm.industry_median is not None else None
        )

        # 3. Window events (best-effort)
        events_in_window: list[dict[str, Any]] = []
        if self._events is not None and ipo_id is not None:
            try:
                events = await self._events.scan_events(
                    ipo_id=ipo_id,
                    stock_code=stock_code,
                    window_start=listing_date,
                    window_end=target_date,
                )
                events_in_window = [e.model_dump(mode="json") for e in events]
                await self._persist_events(ipo_id=ipo_id, events=events)
            except Exception as exc:
                logger.warning(
                    "outcome_event_scan_failed",
                    snapshot_id=str(snapshot_id),
                    error=str(exc),
                )

        # 4. Decision-correctness vs predicted band
        snap = await self._registry.get_snapshot(snapshot_id)
        price_in_range = (
            snap.decision.price_range_low
            <= Decimal(str(tn_close))
            <= snap.decision.price_range_high
        )
        decision_correct = self._is_decision_correct(snap, return_since_listing)

        # 5. Insert (UNIQUE on snapshot_id+day enforces idempotency)
        row = PredictionOutcomeRow(
            snapshot_id=snapshot_id,
            checkpoint_day=checkpoint_day,
            return_since_ipo=return_since_listing,
            return_since_listing=return_since_listing,
            max_drawdown=max_dd,
            relative_return_hsi=rel_hsi,
            relative_return_hstech=rel_hstech,
            relative_return_industry=rel_industry,
            events_in_window=events_in_window or None,
            earnings_released=any(e.get("event_type") == "earnings" for e in events_in_window),
            price_in_predicted_range=price_in_range,
            decision_correct=decision_correct,
            recorded_at=datetime.now(UTC),
        )
        async with self._sf() as s:
            s.add(row)
            await s.commit()
        return TrackResult(outcome_id=row.id, skipped=False)

    async def _exists(self, snapshot_id: UUID, checkpoint_day: int) -> bool:
        stmt = (
            select(PredictionOutcomeRow.id)
            .where(PredictionOutcomeRow.snapshot_id == snapshot_id)
            .where(PredictionOutcomeRow.checkpoint_day == checkpoint_day)
            .limit(1)
        )
        async with self._sf() as s:
            return (await s.execute(stmt)).scalar_one_or_none() is not None

    @staticmethod
    def _max_drawdown(
        series: dict[date, dict[str, float]],
        stock_code: str,
        t0: date,
        tn: date,
    ) -> Decimal:
        prices: list[float] = []
        d = t0
        while d <= tn:
            close = series.get(d, {}).get(stock_code)
            if close is not None:
                prices.append(close)
            d += timedelta(days=1)
        if len(prices) < 2:
            return Decimal("0")
        peak = prices[0]
        max_dd = 0.0
        for p in prices[1:]:
            if p > peak:
                peak = p
            else:
                dd = (p - peak) / peak if peak else 0.0
                max_dd = min(max_dd, dd)
        return Decimal(str(max_dd)).quantize(Decimal("0.000001"))

    @staticmethod
    def _is_decision_correct(snap: PredictionSnapshot, return_since_listing: Decimal) -> bool:
        """Heuristic: PARTICIPATE/PARTIAL + up move OR SKIP + flat/down move."""
        d = snap.decision.decision.value
        positive = return_since_listing > Decimal("0.05")
        if d in ("participate", "partial"):
            return positive
        if d == "skip":
            return not positive
        return False

    async def _persist_events(
        self,
        *,
        ipo_id: UUID,
        events: list[PostIPOEvent],
    ) -> None:
        if not events:
            return
        rows = [
            {
                "id": _uuid.uuid4(),
                "ipo_id": ipo_id,
                "event_date": e.event_date,
                "event_type": e.event_type.value,
                "severity": e.severity.value,
                "description": e.description,
                "source_url": e.source_url,
                "price_impact_1d": Decimal(str(e.price_impact_1d))
                if e.price_impact_1d is not None
                else None,
                "price_impact_5d": Decimal(str(e.price_impact_5d))
                if e.price_impact_5d is not None
                else None,
                "detected_at": datetime.now(UTC),
            }
            for e in events
        ]
        async with self._sf() as s:
            await s.execute(pg_insert(PostIPOEventRow).values(rows))
            await s.commit()


__all__ = (
    "OutcomeTracker",
    "TrackResult",
)
