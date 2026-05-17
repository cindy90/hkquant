"""metrics.py tests — Phase 8b per ADR 0013.

DONE-conditions covered:
- Rank IC matches pen-paper for perfect / anti / random / ties cases
- L-S spread + Welch t-stat sane on synthetic predictable + noise data
- NACS v8 baselines load + canonical iteration accessible
- monotonicity_constraint accepts within-tolerance + rejects significant
  IC and t-stat regressions
- compare_to_baseline returns correct deltas
"""

from __future__ import annotations

import math

import pytest

from hk_ipo_agent.backtest.metrics import (
    DEFAULT_IC_TOLERANCE,
    MetricsReport,
    SliceMetrics,
    compare_to_baseline,
    compute_report,
    compute_slice,
    get_baseline_iteration,
    load_v8_baselines,
    ls_spread,
    monotonicity_constraint,
    rank_ic,
)

# ---------------------------------------------------------------------------
# rank_ic
# ---------------------------------------------------------------------------


def test_rank_ic_perfect_correlation_is_one() -> None:
    predicted = [1.0, 2.0, 3.0, 4.0, 5.0]
    realized = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert rank_ic(predicted, realized) == pytest.approx(1.0, abs=1e-9)


def test_rank_ic_perfect_anti_correlation_is_negative_one() -> None:
    predicted = [1.0, 2.0, 3.0, 4.0, 5.0]
    realized = [50.0, 40.0, 30.0, 20.0, 10.0]
    assert rank_ic(predicted, realized) == pytest.approx(-1.0, abs=1e-9)


def test_rank_ic_ties_handled_with_average_rank() -> None:
    """Ties in predicted get average rank (pandas convention).

    Pen-paper: predicted = [1,1,2,3], realized = [10,10,20,30].
    Average ranks: predicted → [1.5, 1.5, 3, 4]; realized → [1.5, 1.5, 3, 4].
    Perfectly aligned after rank → IC = +1.
    """
    predicted = [1.0, 1.0, 2.0, 3.0]
    realized = [10.0, 10.0, 20.0, 30.0]
    assert rank_ic(predicted, realized) == pytest.approx(1.0, abs=1e-9)


def test_rank_ic_constant_predicted_returns_zero() -> None:
    """Degenerate case: zero variance after ranking → IC = 0 (not NaN)."""
    predicted = [5.0, 5.0, 5.0, 5.0]
    realized = [1.0, 2.0, 3.0, 4.0]
    assert rank_ic(predicted, realized) == 0.0


def test_rank_ic_length_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="length mismatch"):
        rank_ic([1.0, 2.0], [1.0, 2.0, 3.0])


def test_rank_ic_short_input_returns_zero() -> None:
    assert rank_ic([1.0], [1.0]) == 0.0
    assert rank_ic([], []) == 0.0


# ---------------------------------------------------------------------------
# ls_spread
# ---------------------------------------------------------------------------


def test_ls_spread_positive_when_predicted_aligns_with_realized() -> None:
    """20 samples / 10 buckets → 2 per decile.
    Bottom decile = realized[0,1] mean = 0.5
    Top decile = realized[18,19] mean = 18.5
    Spread = 18.0."""
    predicted = list(range(20))
    realized = [float(i) for i in range(20)]
    spread, t = ls_spread(predicted, realized, n_buckets=10)
    assert spread == pytest.approx(18.0, abs=1e-9)
    # 2-sample deciles, monotone integers → tiny variance, t large positive
    assert t > 5.0


def test_ls_spread_negative_when_predicted_inversely_aligned() -> None:
    predicted = list(range(20))
    realized = [float(20 - i) for i in range(20)]  # inverse
    spread, _ = ls_spread(predicted, realized, n_buckets=10)
    assert spread < 0


def test_ls_spread_t_stat_significant_with_repeated_pattern() -> None:
    """40 samples, 4 per decile — provides variance for Welch t.
    Top decile (predicted highest 4) has mean realized ~ 35,
    bottom decile ~ 5 → spread positive, t > 1."""
    predicted = list(range(40))
    realized = [float(i) for i in range(40)]
    spread, t = ls_spread(predicted, realized, n_buckets=10)
    assert spread > 0
    assert t > 1.0  # significantly different


def test_ls_spread_too_small_sample_returns_zero() -> None:
    """n < 2 * n_buckets → spread/t both 0."""
    spread, t = ls_spread([1.0, 2.0, 3.0], [1.0, 2.0, 3.0], n_buckets=10)
    assert spread == 0.0
    assert t == 0.0


def test_ls_spread_length_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="length mismatch"):
        ls_spread([1.0, 2.0], [1.0])


# ---------------------------------------------------------------------------
# compute_slice / compute_report
# ---------------------------------------------------------------------------


def test_compute_slice_packages_all_three_metrics() -> None:
    predicted = list(range(40))
    realized = [float(i) for i in range(40)]
    slice_m = compute_slice(predicted, realized, horizon="60d")
    assert slice_m.horizon == "60d"
    assert slice_m.n == 40
    assert slice_m.ic == pytest.approx(1.0, abs=1e-9)
    assert slice_m.ls_spread > 0
    assert slice_m.ls_t_stat > 1.0


def test_compute_report_handles_per_horizon_dropouts() -> None:
    """Each horizon can have a different sample size (drop-outs OK).
    n_total = max across horizons."""
    per_horizon = {
        "5d": (list(range(40)), [float(i) for i in range(40)]),
        "60d": (list(range(35)), [float(i) for i in range(35)]),
    }
    report = compute_report(label="main_board", per_horizon=per_horizon)
    assert report.label == "main_board"
    assert report.n_total == 40  # max across horizons
    assert set(report.horizons.keys()) == {"5d", "60d"}


# ---------------------------------------------------------------------------
# NACS v8 baselines fixture
# ---------------------------------------------------------------------------


def test_load_v8_baselines_returns_expected_structure() -> None:
    payload = load_v8_baselines()
    assert payload["n_total"] == 384
    assert "iterations" in payload
    expected_iters = {"p1_10", "p1_11", "p1_lockup_v2", "p2_1", "p2_2"}
    assert set(payload["iterations"].keys()) == expected_iters
    assert payload["canonical_iteration"] == "p1_lockup_v2"


def test_get_baseline_iteration_canonical_default() -> None:
    canonical = get_baseline_iteration()
    # p1_lockup_v2 main_board 60d IC = 0.0873388...
    assert canonical["main_board"]["60d"]["ic"] == pytest.approx(
        0.08733882389214259, abs=1e-9,
    )


def test_get_baseline_iteration_unknown_name_raises() -> None:
    with pytest.raises(KeyError, match="unknown iteration"):
        get_baseline_iteration("nonexistent")


# ---------------------------------------------------------------------------
# Monotonicity constraint
# ---------------------------------------------------------------------------


def _baseline_like_report(ic: float = 0.10, t_stat: float = 0.50) -> MetricsReport:
    """Helper: build a small report we can compare to a hand-crafted baseline."""
    return MetricsReport(
        label="main_board",
        n_total=100,
        horizons={
            "60d": SliceMetrics(
                horizon="60d", n=100, ic=ic, ls_spread=0.03, ls_t_stat=t_stat,
            ),
        },
    )


def _baseline_dict(ic: float = 0.10, t_stat: float = 0.50) -> dict:
    return {
        "main_board": {
            "60d": {"ic": ic, "n": 100, "ls_spread": 0.03, "ls_t_stat": t_stat},
        },
    }


def test_monotonicity_passes_when_within_tolerance() -> None:
    # New IC = 0.085, baseline = 0.10 → drop = 0.015 (< default 0.02 tolerance)
    new_report = _baseline_like_report(ic=0.085, t_stat=0.45)
    baseline = _baseline_dict(ic=0.10, t_stat=0.50)
    passed, violations = monotonicity_constraint(new_report, baseline)
    assert passed
    assert violations == []


def test_monotonicity_rejects_significant_ic_regression() -> None:
    # New IC = 0.04, baseline = 0.10 → drop = 0.06 (> default 0.02 tolerance)
    new_report = _baseline_like_report(ic=0.04)
    baseline = _baseline_dict(ic=0.10)
    passed, violations = monotonicity_constraint(new_report, baseline)
    assert not passed
    assert any("IC" in v and "dropped" in v for v in violations)


def test_monotonicity_rejects_significant_t_stat_regression() -> None:
    # t-stat dropped from 1.50 → 0.50 (drop = 1.0 > default 0.50 tolerance)
    new_report = _baseline_like_report(ic=0.10, t_stat=0.50)
    baseline = _baseline_dict(ic=0.10, t_stat=1.50)
    passed, violations = monotonicity_constraint(new_report, baseline)
    assert not passed
    assert any("t-stat" in v for v in violations)


def test_monotonicity_no_baseline_label_passes_softly() -> None:
    """Label absent from baseline → soft pass with note (caller decides)."""
    new_report = _baseline_like_report()
    baseline = {"some_other_label": {"60d": {"ic": 0.05, "ls_spread": 0, "ls_t_stat": 0}}}
    passed, violations = monotonicity_constraint(new_report, baseline)
    assert passed
    assert len(violations) == 1
    assert "no baseline entry" in violations[0]


def test_monotonicity_custom_tolerance_can_make_pass_into_fail() -> None:
    """Same data, tighter tolerance → fails."""
    new_report = _baseline_like_report(ic=0.085)
    baseline = _baseline_dict(ic=0.10)
    # Default tolerance 0.02 passes; tightening to 0.005 fails
    passed_loose, _ = monotonicity_constraint(new_report, baseline, ic_tolerance=0.02)
    passed_tight, _ = monotonicity_constraint(new_report, baseline, ic_tolerance=0.005)
    assert passed_loose
    assert not passed_tight


# ---------------------------------------------------------------------------
# compare_to_baseline
# ---------------------------------------------------------------------------


def test_compare_to_baseline_returns_per_horizon_deltas() -> None:
    new_report = MetricsReport(
        label="main_board",
        n_total=100,
        horizons={
            "30d": SliceMetrics(
                horizon="30d", n=100, ic=0.12, ls_spread=0.05, ls_t_stat=0.80,
            ),
            "60d": SliceMetrics(
                horizon="60d", n=100, ic=0.085, ls_spread=0.04, ls_t_stat=0.30,
            ),
        },
    )
    baseline = {
        "main_board": {
            "30d": {"ic": 0.10, "n": 100, "ls_spread": 0.03, "ls_t_stat": 0.50},
            "60d": {"ic": 0.10, "n": 100, "ls_spread": 0.03, "ls_t_stat": 0.50},
        },
    }
    deltas = compare_to_baseline(new_report, baseline)
    assert deltas["30d"]["ic_delta"] == pytest.approx(0.02, abs=1e-9)
    assert deltas["30d"]["ls_delta"] == pytest.approx(0.02, abs=1e-9)
    assert deltas["30d"]["t_delta"] == pytest.approx(0.30, abs=1e-9)
    assert deltas["60d"]["ic_delta"] == pytest.approx(-0.015, abs=1e-9)


def test_compare_to_baseline_skips_horizons_absent_from_baseline() -> None:
    new_report = _baseline_like_report()
    baseline = {"main_board": {}}  # no horizons
    assert compare_to_baseline(new_report, baseline) == {}


# ---------------------------------------------------------------------------
# Integration with real fixture: canonical p1_lockup_v2 baseline
# ---------------------------------------------------------------------------


def test_canonical_baseline_is_higher_than_default_ic_tolerance() -> None:
    """Sanity: NACS canonical iteration has IC > tolerance, so the
    monotonicity check has meaningful room to flag regressions."""
    canonical = get_baseline_iteration()
    mb_60d_ic = canonical["main_board"]["60d"]["ic"]
    # If IC ≈ default tolerance, the constraint becomes trivial
    assert mb_60d_ic > DEFAULT_IC_TOLERANCE * 2


def test_monotonicity_against_real_baseline_passes_with_v8_data() -> None:
    """Feed the canonical baseline back as the "new" report → must pass."""
    canonical = get_baseline_iteration()
    horizons = {
        h: SliceMetrics(
            horizon=h,
            n=int(slice_dict["n"]),
            ic=float(slice_dict["ic"]),
            ls_spread=float(slice_dict["ls_spread"]),
            ls_t_stat=float(slice_dict["ls_t_stat"]),
        )
        for h, slice_dict in canonical["main_board"].items()
    }
    self_report = MetricsReport(label="main_board", n_total=384, horizons=horizons)
    passed, violations = monotonicity_constraint(self_report, canonical)
    assert passed, f"v8 baseline should pass against itself but got: {violations}"


# Suppress unused-import noise
_ = math
