"""drift_detector.py tests — Phase 10a per ADR 0015."""

from __future__ import annotations

import uuid

import pytest

from hk_ipo_agent.common.enums import AlertLevel, DriftSignalType, ListingType, RegulatoryRegime
from hk_ipo_agent.learning_loop.drift_detector import (
    DEFAULT_CUSUM_THRESHOLD,
    DriftDetector,
    DriftDetectorConfig,
    OutcomeWindowSample,
    cusum_max_excursion,
    population_stability_index,
)

# ---------------------------------------------------------------------------
# CUSUM
# ---------------------------------------------------------------------------


def test_cusum_zero_for_constant_series() -> None:
    """Constant series → 0 excursion (mean = signal, no deviation)."""
    assert cusum_max_excursion([1.0] * 20) == 0.0


def test_cusum_detects_step_change() -> None:
    """Series that jumps from 0 to 1 mid-window → large excursion when
    target is the *historical* mean (0.0) rather than the series mean."""
    series = [0.0] * 10 + [1.0] * 10
    excursion = cusum_max_excursion(series, target=0.0)
    assert excursion > DEFAULT_CUSUM_THRESHOLD


def test_cusum_empty_returns_zero() -> None:
    assert cusum_max_excursion([]) == 0.0


def test_cusum_k_parameter_changes_sensitivity() -> None:
    """Larger k (slack) → smaller excursion for same series."""
    series = [0.0] * 5 + [1.0] * 5
    small_k = cusum_max_excursion(series, k=0.1)
    large_k = cusum_max_excursion(series, k=0.6)
    assert small_k > large_k


# ---------------------------------------------------------------------------
# PSI
# ---------------------------------------------------------------------------


def test_psi_zero_for_same_distribution() -> None:
    """Identical distributions → PSI ≈ 0."""
    import random  # noqa: PLC0415

    rng = random.Random(42)
    data = [rng.gauss(0, 1) for _ in range(100)]
    psi = population_stability_index(data, data.copy())
    assert psi < 0.01


def test_psi_high_for_shifted_distribution() -> None:
    """Mean-shifted distribution → PSI > 0.2."""
    import random  # noqa: PLC0415

    rng = random.Random(42)
    expected = [rng.gauss(0, 1) for _ in range(200)]
    actual = [rng.gauss(2, 1) for _ in range(200)]
    psi = population_stability_index(expected, actual)
    assert psi > 0.2


def test_psi_small_input_returns_zero() -> None:
    assert population_stability_index([1.0], [2.0]) == 0.0


# ---------------------------------------------------------------------------
# DriftDetector — sub-detectors
# ---------------------------------------------------------------------------


def _sample(
    *,
    decision_correct: bool = True,
    regime: RegulatoryRegime = RegulatoryRegime.PRE_20250804,
    listing_type: ListingType | None = ListingType.MAINBOARD_TECH,
    predicted: float | None = 100.0,
    realized: float | None = 100.0,
    bear_flag: bool | None = True,
    negative_realized: bool | None = False,
    agent_scores: dict[str, float] | None = None,
    agent_hits: dict[str, bool] | None = None,
) -> OutcomeWindowSample:
    return OutcomeWindowSample(
        snapshot_id=str(uuid.uuid4()),
        listing_type=listing_type,
        regulatory_regime=regime,
        decision_correct=decision_correct,
        predicted_median_price=predicted,
        realized_price_at_60d=realized,
        bear_flagged_risk=bear_flag,
        realized_outcome_negative=negative_realized,
        agent_scores=agent_scores or {},
        agent_realized_hits=agent_hits or {},
    )


def test_detector_empty_window_returns_no_signals() -> None:
    detector = DriftDetector()
    assert detector.detect([]) == []


def test_detector_small_window_returns_no_signals() -> None:
    detector = DriftDetector(DriftDetectorConfig(window_min_n=10))
    samples = [_sample() for _ in range(5)]
    assert detector.detect(samples) == []


def test_detector_fires_accuracy_drop_on_cusum_excursion() -> None:
    """Constructed series with mean-shift in decision_correct → fires."""
    detector = DriftDetector(DriftDetectorConfig(window_min_n=10, cusum_threshold=2.0))
    # 15 correct + 15 incorrect → strong CUSUM signal
    samples = [_sample(decision_correct=True) for _ in range(15)]
    samples += [_sample(decision_correct=False) for _ in range(15)]
    signals = detector.detect(samples)
    assert any(s.signal_type == DriftSignalType.ACCURACY_DROP for s in signals)


def test_detector_fires_bear_miss_when_most_negatives_unflagged() -> None:
    """80% of negative outcomes NOT flagged by Bear → fires."""
    detector = DriftDetector(DriftDetectorConfig(window_min_n=10, bear_miss_rate=0.4))
    # 10 negatives, 8 missed by Bear (bear_flag=False)
    samples = [
        _sample(negative_realized=True, bear_flag=False) for _ in range(8)
    ] + [
        _sample(negative_realized=True, bear_flag=True) for _ in range(2)
    ] + [
        _sample(negative_realized=False, bear_flag=True) for _ in range(5)
    ]
    signals = detector.detect(samples)
    bear_signals = [s for s in signals if s.signal_type == DriftSignalType.BEAR_MISS_RATE_HIGH]
    assert len(bear_signals) == 1
    assert bear_signals[0].metric_value == pytest.approx(0.8)


def test_detector_fires_agent_calibration_when_high_scores_miss() -> None:
    """Agent that gives score=85 but misses 5/10 times → fires."""
    detector = DriftDetector(
        DriftDetectorConfig(window_min_n=10, agent_calibration_drop=0.15)
    )
    samples = [
        _sample(
            agent_scores={"fundamental": 85.0},
            agent_hits={"fundamental": (i % 2 == 0)},  # 50% hit rate
        )
        for i in range(20)
    ]
    signals = detector.detect(samples)
    calib_signals = [
        s for s in signals if s.signal_type == DriftSignalType.AGENT_CALIBRATION_DRIFT
    ]
    assert len(calib_signals) >= 1
    assert calib_signals[0].affected_dimensions == {"agent_role": "fundamental"}


def test_detector_fires_valuation_bias_on_skewed_slice() -> None:
    """One listing_type with systematically biased predictions → fires PSI."""
    import random  # noqa: PLC0415

    rng = random.Random(42)
    detector = DriftDetector(
        DriftDetectorConfig(window_min_n=10, psi_threshold=0.15)
    )
    # MB-TECH samples have well-calibrated predictions
    mb_tech = [
        _sample(
            listing_type=ListingType.MAINBOARD_TECH,
            predicted=100 + rng.gauss(0, 1),
            realized=100 + rng.gauss(0, 1),
        )
        for _ in range(50)
    ]
    # 18A samples have systematically over-predicted (3x bias)
    biotech = [
        _sample(
            listing_type=ListingType.CH18A_BIOTECH,
            predicted=300 + rng.gauss(0, 1),
            realized=100 + rng.gauss(0, 1),
        )
        for _ in range(20)
    ]
    signals = detector.detect(mb_tech + biotech)
    val_signals = [
        s for s in signals if s.signal_type == DriftSignalType.VALUATION_BIAS
    ]
    assert val_signals  # at least one PSI-flagged slice


def test_detector_severity_critical_when_psi_doubles_threshold() -> None:
    import random  # noqa: PLC0415

    rng = random.Random(42)
    detector = DriftDetector(
        DriftDetectorConfig(window_min_n=10, psi_threshold=0.10)
    )
    mb_tech = [
        _sample(
            listing_type=ListingType.MAINBOARD_TECH,
            predicted=100,
            realized=100,
        )
        for _ in range(50)
    ]
    biotech = [
        _sample(
            listing_type=ListingType.CH18A_BIOTECH,
            predicted=300 + rng.gauss(0, 0.5),
            realized=100 + rng.gauss(0, 0.5),
        )
        for _ in range(30)
    ]
    signals = detector.detect(mb_tech + biotech)
    val_signals = [
        s for s in signals if s.signal_type == DriftSignalType.VALUATION_BIAS
    ]
    assert any(s.severity == AlertLevel.CRITICAL for s in val_signals)
