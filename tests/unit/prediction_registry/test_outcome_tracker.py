"""OutcomeTracker tests — Phase 7.5b per ADR 0012.

These tests use the docker postgres instance (truncate-and-fill pattern)
because outcome persistence is the whole point of the module — mocking
the session factory would dilute the test.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from hk_ipo_agent.common.enums import AgentRole, DecisionType, ListingType
from hk_ipo_agent.common.schemas import (
    AgentOutput,
    DebateOutput,
    FinalDecision,
    ProspectusExtraction,
    SingleModelValuation,
    ValuationDistribution,
    ValuationEnsembleOutput,
)
from hk_ipo_agent.common.settings import get_settings
from hk_ipo_agent.prediction_registry.benchmarks import BenchmarkPriceService, BenchmarkReturns
from hk_ipo_agent.prediction_registry.outcome_tracker import OutcomeTracker, TrackResult
from hk_ipo_agent.prediction_registry.registry import PGPredictionRegistry
from hk_ipo_agent.prediction_registry.snapshot import build_snapshot


def _build_snapshot():
    dist = ValuationDistribution(
        p10=Decimal("9"), p25=Decimal("9.5"), p50=Decimal("10"),
        p75=Decimal("10.5"), p90=Decimal("11"),
        mean=Decimal("10"), std=Decimal("0.5"),
    )
    return build_snapshot(
        ipo_id=uuid.uuid4(),
        extraction=ProspectusExtraction(
            prospectus_id=f"P-OT-{uuid.uuid4().hex[:6]}",
            company_name_zh="测试 OT",
            listing_type=ListingType.MAINBOARD_TECH,
            industry_code="TECH",
            industry_description="AI",
            business_model="B2B",
            extraction_version="0.0.1",
            extracted_at=datetime.now(UTC),
        ),
        agent_outputs={
            "fundamental": AgentOutput(
                agent_role=AgentRole.FUNDAMENTAL,
                scores={"x": 70.0}, overall_score=70.0, runtime_seconds=0.1,
            ),
        },
        valuation=ValuationEnsembleOutput(
            company_id="P-OT-1",
            single_models=[
                SingleModelValuation(model_name="x", applicable=True, valuation_distribution=dist),
            ],
            weights_used={"x": 1.0},
            ensemble_distribution=dist,
            implied_price_range={"low": Decimal("9"), "fair": Decimal("10"), "high": Decimal("11")},
        ),
        debate=DebateOutput(final_consensus="balanced"),
        decision=FinalDecision(
            decision=DecisionType.PARTICIPATE,
            confidence=0.7, suggested_allocation_pct=0.02,
            price_range_low=Decimal("9"), price_range_fair=Decimal("10"), price_range_high=Decimal("11"),
            expected_return_6m=dist, expected_return_12m=dist,
        ),
        total_cost_usd=Decimal("0.05"), runtime_seconds=5.0,
    )


class _StubBenchmarks:
    """Bypass the BenchmarkPriceService — return fixed returns."""

    def __init__(
        self,
        *,
        hsi: Decimal | None = Decimal("0.03"),
        hstech: Decimal | None = Decimal("0.05"),
        industry: Decimal | None = Decimal("0.08"),
    ) -> None:
        self._r = BenchmarkReturns(hsi=hsi, hstech=hstech, industry_median=industry)

    async def compute(self, *, t0, tn, industry_peers=None):
        return self._r


class _StubPrices:
    def __init__(self, t0_close: float, tn_close: float, stock_code: str) -> None:
        self._t0 = t0_close
        self._tn = tn_close
        self._code = stock_code

    async def get_hk_history_prices(self, tickers, as_of_date, *, start):
        return {
            "data": [
                {"time": start.isoformat(), "thscode": self._code, "close": self._t0},
                {"time": as_of_date.isoformat(), "thscode": self._code, "close": self._tn},
            ]
        }


class _FailingPrices:
    async def get_hk_history_prices(self, tickers, as_of_date, *, start):
        raise RuntimeError("iFind unavailable")


@pytest_asyncio.fixture
async def pg_setup():
    """Returns (session_factory, PGPredictionRegistry) with truncated tables."""
    engine = create_async_engine(get_settings().database.url, poolclass=NullPool)
    sf = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with sf() as s:
        await s.execute(text(
            "TRUNCATE TABLE prediction_reviews, prediction_outcomes, post_ipo_events, "
            "prediction_snapshots, ipo_events RESTART IDENTITY CASCADE"
        ))
        await s.commit()
    try:
        yield sf, PGPredictionRegistry(session_factory=sf)
    finally:
        await engine.dispose()


async def _seed_snapshot(sf, registry, snap):
    """Insert ipo_event then snapshot."""
    async with sf() as s:
        await s.execute(
            text(
                "INSERT INTO ipo_events (id, stock_code, company_name_zh, listing_type, "
                "created_at, updated_at) VALUES (:id, :code, :name, :lt, NOW(), NOW())"
            ),
            {"id": snap.ipo_id, "code": "TEST.HK", "name": "Test", "lt": "mainboard_tech"},
        )
        await s.commit()
    await registry.create_snapshot(snap)


@pytest.mark.asyncio
async def test_track_records_outcome_with_expected_returns(pg_setup) -> None:
    sf, registry = pg_setup
    snap = _build_snapshot()
    await _seed_snapshot(sf, registry, snap)
    tracker = OutcomeTracker(
        session_factory=sf,
        snapshot_resolver=registry,
        benchmarks=_StubBenchmarks(),
        price_fetcher=_StubPrices(t0_close=10.0, tn_close=11.0, stock_code="TEST.HK"),
    )
    listing_d = date(2026, 1, 15)
    result = await tracker.track(
        snapshot_id=snap.id, checkpoint_day=30,
        stock_code="TEST.HK", listing_date=listing_d,
    )
    assert isinstance(result, TrackResult)
    assert not result.skipped
    assert result.outcome_id is not None


@pytest.mark.asyncio
async def test_track_is_idempotent(pg_setup) -> None:
    sf, registry = pg_setup
    snap = _build_snapshot()
    await _seed_snapshot(sf, registry, snap)
    tracker = OutcomeTracker(
        session_factory=sf,
        snapshot_resolver=registry,
        benchmarks=_StubBenchmarks(),
        price_fetcher=_StubPrices(t0_close=10.0, tn_close=11.0, stock_code="TEST.HK"),
    )
    listing_d = date(2026, 1, 15)
    r1 = await tracker.track(snapshot_id=snap.id, checkpoint_day=30, stock_code="TEST.HK", listing_date=listing_d)
    r2 = await tracker.track(snapshot_id=snap.id, checkpoint_day=30, stock_code="TEST.HK", listing_date=listing_d)
    assert not r1.skipped
    assert r2.skipped
    assert r2.reason == "already_recorded"


@pytest.mark.asyncio
async def test_track_rejects_invalid_checkpoint_day(pg_setup) -> None:
    sf, registry = pg_setup
    snap = _build_snapshot()
    await _seed_snapshot(sf, registry, snap)
    tracker = OutcomeTracker(
        session_factory=sf, snapshot_resolver=registry,
        benchmarks=_StubBenchmarks(), price_fetcher=_StubPrices(10.0, 11.0, "TEST.HK"),
    )
    with pytest.raises(ValueError, match="not in spec"):
        await tracker.track(
            snapshot_id=snap.id, checkpoint_day=42,
            stock_code="TEST.HK", listing_date=date(2026, 1, 15),
        )


@pytest.mark.asyncio
async def test_track_skips_when_price_fetch_fails(pg_setup) -> None:
    sf, registry = pg_setup
    snap = _build_snapshot()
    await _seed_snapshot(sf, registry, snap)
    tracker = OutcomeTracker(
        session_factory=sf, snapshot_resolver=registry,
        benchmarks=_StubBenchmarks(), price_fetcher=_FailingPrices(),
    )
    result = await tracker.track(
        snapshot_id=snap.id, checkpoint_day=30,
        stock_code="TEST.HK", listing_date=date(2026, 1, 15),
    )
    assert result.skipped
    assert "price_fetch_failed" in (result.reason or "")
