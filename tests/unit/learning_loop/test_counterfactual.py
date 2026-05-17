"""counterfactual.py tests — Phase 10a per ADR 0015."""

from __future__ import annotations

import uuid

import pytest

from hk_ipo_agent.common.enums import DecisionType
from hk_ipo_agent.learning_loop.counterfactual import (
    CounterfactualSample,
    if_bear_followed,
    if_single_model_used,
    run_counterfactual,
)


def _sample(
    *,
    actual: DecisionType = DecisionType.PARTICIPATE,
    bull: DecisionType | None = DecisionType.PARTICIPATE,
    bear: DecisionType | None = DecisionType.SKIP,
    realized_return: float = 0.05,
    single_models: dict[str, float] | None = None,
    ensemble_fair: float | None = 100.0,
    realized_price: float | None = 100.0,
) -> CounterfactualSample:
    return CounterfactualSample(
        snapshot_id=uuid.uuid4(),
        actual_decision=actual,
        bull_decision=bull,
        bear_decision=bear,
        realized_return=realized_return,
        realized_in_predicted_range=True,
        single_model_fair_prices=single_models or {},
        ensemble_fair_price=ensemble_fair,
        realized_price_at_60d=realized_price,
    )


# ---------------------------------------------------------------------------
# if_bear_followed
# ---------------------------------------------------------------------------


def test_if_bear_followed_empty_returns_zero_advantage() -> None:
    report = if_bear_followed([])
    assert report.n_total == 0
    assert report.bear_advantage == 0.0


def test_if_bear_followed_high_advantage_when_bull_won_and_lost() -> None:
    """5 bull-won-bad cases, Bear said SKIP every time → 100% advantage."""
    samples = [
        _sample(
            actual=DecisionType.PARTICIPATE,
            bull=DecisionType.PARTICIPATE,
            bear=DecisionType.SKIP,
            realized_return=-0.10,
        )
        for _ in range(5)
    ]
    report = if_bear_followed(samples)
    assert report.n_bull_won == 5
    assert report.n_bull_won_bad == 5
    assert report.bear_advantage == 1.0


def test_if_bear_followed_low_advantage_when_bull_was_right() -> None:
    """5 bull-won cases, all positive return → 0 bad outcomes → 0 advantage."""
    samples = [
        _sample(
            actual=DecisionType.PARTICIPATE,
            bull=DecisionType.PARTICIPATE,
            bear=DecisionType.SKIP,
            realized_return=+0.10,
        )
        for _ in range(5)
    ]
    report = if_bear_followed(samples)
    assert report.n_bull_won == 5
    assert report.n_bull_won_bad == 0
    assert report.bear_advantage == 0.0


def test_if_bear_followed_ignores_actual_matches_bear() -> None:
    """Cases where actual == bear (synthesizer agreed) don't count as 'bull won'."""
    samples = [
        _sample(
            actual=DecisionType.SKIP,
            bull=DecisionType.PARTICIPATE,
            bear=DecisionType.SKIP,
        )
        for _ in range(5)
    ]
    report = if_bear_followed(samples)
    assert report.n_bull_won == 0


# ---------------------------------------------------------------------------
# if_single_model_used
# ---------------------------------------------------------------------------


def test_if_single_model_empty_returns_zero() -> None:
    report = if_single_model_used([])
    assert report.n_samples == 0
    assert report.best_single_model is None


def test_if_single_model_picks_best_single() -> None:
    """DCF predicts perfectly (100→100); comparable is off (300→100).
    Best single = dcf with hit_rate=1.0."""
    samples = [
        _sample(
            single_models={"dcf": 100.0, "comparable": 300.0},
            ensemble_fair=200.0,  # ensemble avg → far off
            realized_price=100.0,
        )
        for _ in range(10)
    ]
    report = if_single_model_used(samples, hit_tolerance=0.10)
    assert report.best_single_model == "dcf"
    assert report.best_single_hit_rate == pytest.approx(1.0)
    assert report.ensemble_hit_rate < report.best_single_hit_rate
    # Negative advantage → ensemble worse than best single
    assert report.ensemble_advantage < 0


def test_if_single_model_ensemble_advantage_positive_when_ensemble_better() -> None:
    """Ensemble nails it (100→100), individual models scattered → ensemble wins."""
    samples = [
        _sample(
            single_models={"dcf": 80.0, "comparable": 130.0},
            ensemble_fair=100.0,
            realized_price=100.0,
        )
        for _ in range(10)
    ]
    report = if_single_model_used(samples, hit_tolerance=0.05)
    assert report.ensemble_hit_rate > report.best_single_hit_rate
    assert report.ensemble_advantage > 0


# ---------------------------------------------------------------------------
# run_counterfactual composite
# ---------------------------------------------------------------------------


def test_run_counterfactual_combines_both_analyses() -> None:
    samples = [
        _sample(
            actual=DecisionType.PARTICIPATE,
            bull=DecisionType.PARTICIPATE,
            bear=DecisionType.SKIP,
            realized_return=-0.10,
            single_models={"dcf": 100.0},
            ensemble_fair=120.0,
            realized_price=100.0,
        )
        for _ in range(10)
    ]
    report = run_counterfactual(samples)
    assert report.if_bear.bear_advantage == 1.0
    assert report.if_single_model.best_single_model == "dcf"
    assert "Bear" in report.summary or "advantage" in report.summary


def test_run_counterfactual_summary_flags_synthesizer_bull_bias() -> None:
    """High bear advantage (>50%) → summary flags bull bias."""
    samples = [
        _sample(
            actual=DecisionType.PARTICIPATE,
            bull=DecisionType.PARTICIPATE,
            bear=DecisionType.SKIP,
            realized_return=-0.10,
        )
        for _ in range(8)
    ]
    report = run_counterfactual(samples)
    assert "bull bias" in report.summary or "bull-bad" in report.summary


def test_counterfactual_report_to_dict_roundtrip() -> None:
    report = run_counterfactual([])
    d = report.to_dict()
    assert "if_bear" in d
    assert "if_single_model" in d
    assert "summary" in d
