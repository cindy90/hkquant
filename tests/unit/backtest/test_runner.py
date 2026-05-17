"""runner.py tests — Phase 8c per ADR 0013.

DONE-conditions covered:
- ``V8LiteScorer`` produces deterministic decision_score from listing_type
  + cornerstone_count + regime_score.
- Regime Gate hard semantics: ``regime_pass = regime_score >= 0``.
- ``run_walk_forward`` iterates inputs, sets as_of = pricing_date - 1,
  builds AsOfDataProvider, calls scorer, collects samples.
- Future-dated pricing_date is logged + skipped (no crash).
- Slice metrics: main_board vs regime_pass partitioning correct.
- PG loader ``_coerce_returns`` reads JSONB preferred, scalar fallback.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from hk_ipo_agent.backtest.runner import (
    DEFAULT_HORIZONS,
    BacktestInput,
    BacktestSample,
    V8LiteScorer,
    _coerce_returns,
    _compute_metrics_per_slice,
    run_walk_forward,
)
from hk_ipo_agent.common.enums import ListingType, RegulatoryRegime

# ===========================================================================
# V8LiteScorer
# ===========================================================================


@pytest.mark.asyncio
async def test_v8lite_scorer_base_score_by_listing_type(fresh_sf) -> None:
    """18C-COMM (commercialized) > 18A (biotech pre-com)."""
    from hk_ipo_agent.backtest.as_of_data import AsOfDataProvider  # noqa: PLC0415

    provider = AsOfDataProvider(
        as_of_date=date(2024, 6, 13), session_factory=fresh_sf,
    )
    scorer = V8LiteScorer()
    out_com = await scorer.score(
        provider,
        BacktestInput(
            ipo_id=uuid.uuid4(), pricing_date=date(2024, 6, 14),
            stock_code="0001.HK", listing_type=ListingType.CH18C_COMMERCIALIZED,
            realized_returns={}, cornerstone_count=0,
        ),
    )
    out_bio = await scorer.score(
        provider,
        BacktestInput(
            ipo_id=uuid.uuid4(), pricing_date=date(2024, 6, 14),
            stock_code="0002.HK", listing_type=ListingType.CH18A_BIOTECH,
            realized_returns={}, cornerstone_count=0,
        ),
    )
    assert out_com.decision_score > out_bio.decision_score


@pytest.mark.asyncio
async def test_v8lite_scorer_cornerstone_bonus_capped(fresh_sf) -> None:
    """20 cornerstones × 0.05 = 1.0 — but capped at 0.20."""
    from hk_ipo_agent.backtest.as_of_data import AsOfDataProvider  # noqa: PLC0415

    provider = AsOfDataProvider(
        as_of_date=date(2024, 6, 13), session_factory=fresh_sf,
    )
    scorer = V8LiteScorer(
        cluster_bonus_per_investor=0.05, cluster_bonus_cap=0.20,
    )
    out_zero = await scorer.score(
        provider,
        BacktestInput(
            ipo_id=uuid.uuid4(), pricing_date=date(2024, 6, 14),
            stock_code="X", listing_type=ListingType.MAINBOARD_TECH,
            realized_returns={}, cornerstone_count=0,
        ),
    )
    out_many = await scorer.score(
        provider,
        BacktestInput(
            ipo_id=uuid.uuid4(), pricing_date=date(2024, 6, 14),
            stock_code="Y", listing_type=ListingType.MAINBOARD_TECH,
            realized_returns={}, cornerstone_count=20,
        ),
    )
    assert out_many.decision_score - out_zero.decision_score == pytest.approx(0.20)


@pytest.mark.asyncio
async def test_v8lite_scorer_unknown_listing_type_uses_default(fresh_sf) -> None:
    from hk_ipo_agent.backtest.as_of_data import AsOfDataProvider  # noqa: PLC0415

    provider = AsOfDataProvider(
        as_of_date=date(2024, 6, 13), session_factory=fresh_sf,
    )
    scorer = V8LiteScorer()
    out = await scorer.score(
        provider,
        BacktestInput(
            ipo_id=uuid.uuid4(), pricing_date=date(2024, 6, 14),
            stock_code="Z", listing_type=None,
            realized_returns={}, cornerstone_count=0,
        ),
    )
    assert out.listing_type is None
    # Default base = 0.30; regime depends on cache state at 2024-06-12.
    # Just check the score is sane.
    assert -1.0 < out.decision_score < 1.0


# ===========================================================================
# run_walk_forward
# ===========================================================================


@pytest.mark.asyncio
async def test_run_walk_forward_collects_samples(fresh_sf) -> None:
    inputs = [
        BacktestInput(
            ipo_id=uuid.uuid4(),
            pricing_date=date(2024, 6, 14),
            stock_code=f"{i:04d}.HK",
            listing_type=ListingType.MAINBOARD_TECH,
            realized_returns={"5d": 0.05 + i * 0.01, "30d": 0.10 + i * 0.02},
            cornerstone_count=i,
        )
        for i in range(5)
    ]
    run = await run_walk_forward(
        inputs, scorer=V8LiteScorer(), session_factory=fresh_sf,
        horizons=("5d", "30d"),
    )
    assert run.n_total == 5
    assert all(
        s.as_of_date == s.pricing_date - timedelta(days=1) for s in run.samples
    )
    assert "main_board" in run.metrics_by_label
    assert "regime_pass" in run.metrics_by_label


@pytest.mark.asyncio
async def test_run_walk_forward_skips_future_pricing(fresh_sf) -> None:
    """An IPO with pricing_date > today shouldn't crash the runner."""
    future_pricing = date.today() + timedelta(days=7)
    inputs = [
        BacktestInput(
            ipo_id=uuid.uuid4(),
            pricing_date=future_pricing,
            stock_code="FUTR.HK",
            listing_type=ListingType.MAINBOARD_TECH,
            realized_returns={"5d": 0.05},
            cornerstone_count=0,
        ),
    ]
    run = await run_walk_forward(
        inputs, scorer=V8LiteScorer(), session_factory=fresh_sf,
    )
    assert run.n_total == 0


@pytest.mark.asyncio
async def test_run_walk_forward_horizons_propagate(fresh_sf) -> None:
    """Only requested horizons populate metrics; absent ones empty."""
    inputs = [
        BacktestInput(
            ipo_id=uuid.uuid4(),
            pricing_date=date(2024, 6, 14),
            stock_code="A.HK",
            listing_type=ListingType.MAINBOARD_TECH,
            realized_returns={"5d": 0.05},  # only 5d
            cornerstone_count=0,
        ),
    ]
    run = await run_walk_forward(
        inputs, scorer=V8LiteScorer(), session_factory=fresh_sf,
        horizons=("5d", "30d"),
    )
    mb = run.metrics_by_label["main_board"]
    assert "5d" in mb.horizons
    # 30d empty → still has a SliceMetrics with n=0
    assert mb.horizons.get("30d") is None or mb.horizons["30d"].n == 0


# ===========================================================================
# _compute_metrics_per_slice — slice partitioning
# ===========================================================================


def _make_sample(
    score: float, regime: float, returns: dict[str, float],
) -> BacktestSample:
    return BacktestSample(
        ipo_id=uuid.uuid4(),
        stock_code="X.HK",
        listing_type=ListingType.MAINBOARD_TECH,
        pricing_date=date(2024, 6, 14),
        as_of_date=date(2024, 6, 13),
        decision_score=score,
        realized_returns=returns,
        regime_score=regime,
        regulatory_regime=RegulatoryRegime.PRE_20250804,
        notes=(),
    )


def test_compute_metrics_per_slice_partitions_regime_pass() -> None:
    """regime_pass slice has only samples with regime_score >= 0."""
    samples = [
        _make_sample(0.5, regime=0.1, returns={"5d": 0.05}),  # pass
        _make_sample(0.3, regime=-0.1, returns={"5d": -0.02}),  # fail
        _make_sample(0.7, regime=0.0, returns={"5d": 0.10}),  # pass (boundary)
    ]
    out = _compute_metrics_per_slice(samples, horizons=("5d",))
    assert out["main_board"].horizons["5d"].n == 3
    assert out["regime_pass"].horizons["5d"].n == 2  # only pass


def test_compute_metrics_per_slice_drops_missing_returns() -> None:
    """Samples with no realized return for a horizon are dropped."""
    samples = [
        _make_sample(0.5, regime=0.1, returns={"5d": 0.05}),
        _make_sample(0.3, regime=0.1, returns={}),  # no 5d return
    ]
    out = _compute_metrics_per_slice(samples, horizons=("5d",))
    assert out["main_board"].horizons["5d"].n == 1


# ===========================================================================
# _coerce_returns — PG postmarket coercion
# ===========================================================================


def test_coerce_returns_prefers_jsonb_returns_by_day() -> None:
    pm = MagicMock()
    pm.returns_by_day = {"5": "0.05", "30": "0.12", "60": "0.08"}
    out = _coerce_returns(pm, ("5d", "30d", "60d"))
    assert out == {"5d": 0.05, "30d": 0.12, "60d": 0.08}


def test_coerce_returns_falls_back_to_scalars() -> None:
    pm = MagicMock()
    pm.returns_by_day = None
    pm.day5_return = 0.04
    pm.day22_return = 0.10  # used for both 30d and 22d
    pm.day126_return = 0.15
    pm.day252_return = None
    out = _coerce_returns(pm, ("5d", "30d", "180d"))
    assert out["5d"] == 0.04
    assert out["30d"] == 0.10
    assert out["180d"] == 0.15


def test_coerce_returns_empty_when_no_data() -> None:
    pm = MagicMock()
    pm.returns_by_day = None
    pm.day5_return = None
    pm.day22_return = None
    pm.day126_return = None
    pm.day252_return = None
    out = _coerce_returns(pm, DEFAULT_HORIZONS)
    assert out == {}
