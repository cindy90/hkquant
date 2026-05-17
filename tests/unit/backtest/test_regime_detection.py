"""regime_detection.py tests — Phase 8a per ADR 0013."""

from __future__ import annotations

from datetime import date

import pytest

from hk_ipo_agent.backtest.regime_detection import (
    CH18C_THRESHOLD_REVISION,
    REGULATORY_CHANGE_POINTS,
    market_env_for,
    regime_score_from_cache,
    regime_score_from_window,
    regulatory_regime_for,
    reset_cache,
    slice_by_regulatory_regime,
)
from hk_ipo_agent.common.enums import RegulatoryRegime

# ===========================================================================
# Regulatory regime
# ===========================================================================


def test_regulatory_regime_pre_2025_08_04() -> None:
    assert regulatory_regime_for(date(2024, 1, 1)) is RegulatoryRegime.PRE_20250804
    assert regulatory_regime_for(date(2025, 8, 3)) is RegulatoryRegime.PRE_20250804


def test_regulatory_regime_post_2025_08_04() -> None:
    """The change point itself is inclusive of the new regime."""
    assert regulatory_regime_for(date(2025, 8, 4)) is RegulatoryRegime.POST_20250804
    assert regulatory_regime_for(date(2026, 5, 16)) is RegulatoryRegime.POST_20250804


def test_change_points_are_sorted_ascending() -> None:
    """Future-proof: if multiple change points are added they must be
    in date order so ``regulatory_regime_for`` resolves correctly."""
    dates = [d for d, _ in REGULATORY_CHANGE_POINTS]
    assert dates == sorted(dates)


def test_ch18c_threshold_revision_constant() -> None:
    assert date(2024, 9, 1) == CH18C_THRESHOLD_REVISION


# ===========================================================================
# Market environment cache (NACS v8 fixture)
# ===========================================================================


def test_market_env_for_returns_closest_prior_snapshot() -> None:
    reset_cache()
    # 2024-06-13 should resolve to the 2024-06-01 monthly snapshot.
    env = market_env_for(date(2024, 6, 13))
    assert env is not None
    assert env.asof_month == date(2024, 6, 1)


def test_market_env_for_returns_none_before_history() -> None:
    """Pre-2021 anchor → no snapshot exists yet."""
    reset_cache()
    env = market_env_for(date(2000, 1, 1))
    assert env is None


def test_regime_score_from_cache_matches_snapshot_field() -> None:
    """Sanity: the convenience accessor returns the cached field directly."""
    reset_cache()
    env = market_env_for(date(2024, 6, 13))
    score = regime_score_from_cache(date(2024, 6, 13))
    assert env is not None
    assert score == env.hk_ipo_30d_avg_d30


# ===========================================================================
# Regime score from caller-provided window
# ===========================================================================


def test_regime_score_from_window_odd_count_is_middle_value() -> None:
    # sorted: [-0.15, -0.05, 0.03, 0.10, 0.20]; median = 0.03.
    score = regime_score_from_window(ipo_returns_30d=[0.10, -0.05, 0.20, 0.03, -0.15])
    assert score == pytest.approx(0.03)


def test_regime_score_from_window_even_count_averages_two_middle() -> None:
    # sorted: [-0.1, 0.0, 0.1, 0.2]; median = (0.0 + 0.1) / 2 = 0.05.
    score = regime_score_from_window(ipo_returns_30d=[-0.1, 0.1, 0.0, 0.2])
    assert score == pytest.approx(0.05)


def test_regime_score_from_window_empty_returns_zero() -> None:
    assert regime_score_from_window(ipo_returns_30d=[]) == 0.0


# ===========================================================================
# Sample slicing
# ===========================================================================


def test_slice_by_regulatory_regime_groups_samples() -> None:
    samples = [
        ("ipo-A", date(2023, 5, 1)),
        ("ipo-B", date(2024, 12, 1)),
        ("ipo-C", date(2025, 8, 4)),
        ("ipo-D", date(2025, 9, 1)),
        ("ipo-E", date(2025, 1, 1)),
    ]
    slices = slice_by_regulatory_regime(samples)
    by_regime = {s.regime: set(s.sample_ipo_ids) for s in slices}
    assert by_regime[RegulatoryRegime.PRE_20250804] == {"ipo-A", "ipo-B", "ipo-E"}
    assert by_regime[RegulatoryRegime.POST_20250804] == {"ipo-C", "ipo-D"}


def test_slice_by_regulatory_regime_deterministic_order() -> None:
    """Slices are returned in enum-value order so callers can rely on it."""
    samples = [
        ("ipo-A", date(2025, 12, 1)),  # post
        ("ipo-B", date(2023, 1, 1)),  # pre
    ]
    slices = slice_by_regulatory_regime(samples)
    regimes_in_order = [s.regime for s in slices]
    assert regimes_in_order == sorted(regimes_in_order, key=lambda r: r.value)
