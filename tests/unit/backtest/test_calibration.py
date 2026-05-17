"""calibration.py tests — Phase 8c per ADR 0013.

DONE-conditions covered:
- ``load_current_weights`` reads YAML; missing file → empty dict.
- ``_enumerate_weight_grid`` yields normalized weight vectors summing to 1.
- ``calibrate_one_listing_type`` keeps baseline when n < MIN_SLICE_N.
- ``calibrate_one_listing_type`` exercises grid + returns objective.
- ``calibrate`` aggregates per-listing_type results + emits candidate YAML.
- ``dump_weights_yaml`` round-trips YAML format.
"""

from __future__ import annotations

import uuid
from datetime import date

import yaml

from hk_ipo_agent.backtest.calibration import (
    DEFAULT_WEIGHT_GRID,
    MIN_SLICE_N,
    _enumerate_weight_grid,
    calibrate,
    calibrate_one_listing_type,
    dump_weights_yaml,
    load_current_weights,
)
from hk_ipo_agent.backtest.runner import BacktestSample
from hk_ipo_agent.common.enums import ListingType, RegulatoryRegime

# ===========================================================================
# YAML loading + dumping
# ===========================================================================


def test_load_current_weights_returns_dict() -> None:
    """The repo's actual config/valuation_weights.yaml has 6 ListingType entries."""
    weights = load_current_weights()
    # ListingType.value of CH18C_COMM = "18C-COMM" etc.
    assert "18C-COMM" in weights
    assert "MB-TECH" in weights
    # Each sub-dict has model_name → weight
    assert all(isinstance(v, float) for v in weights["MB-TECH"].values())


def test_load_current_weights_missing_file_returns_empty(tmp_path) -> None:
    out = load_current_weights(path=tmp_path / "does_not_exist.yaml")
    assert out == {}


def test_dump_weights_yaml_round_trip() -> None:
    weights = {
        "MB-TECH": {"dcf": 0.4, "comparable": 0.6},
        "AH": {"ah_premium": 0.5, "comparable": 0.5},
    }
    text = dump_weights_yaml(weights)
    parsed = yaml.safe_load(text)
    assert parsed["weights"]["MB-TECH"]["dcf"] == 0.4
    assert parsed["weights"]["AH"]["ah_premium"] == 0.5


def test_dump_weights_yaml_with_header() -> None:
    text = dump_weights_yaml(
        {"MB-TECH": {"dcf": 1.0}},
        header_comment="# Candidate run abc123\n",
    )
    assert text.startswith("# Candidate run abc123")
    # The YAML body still parses (header is comment).
    parsed = yaml.safe_load(text)
    assert parsed["weights"]["MB-TECH"]["dcf"] == 1.0


# ===========================================================================
# Grid enumeration
# ===========================================================================


def test_enumerate_weight_grid_emits_normalized_vectors() -> None:
    # 2 models, small grid → only a few combos. Each output must sum to 1.
    grid = (0.2, 0.4, 0.5, 0.6, 0.8)
    vectors = list(_enumerate_weight_grid(["dcf", "comparable"], grid=grid, tol=1e-6))
    for v in vectors:
        assert set(v.keys()) == {"dcf", "comparable"}
        assert abs(sum(v.values()) - 1.0) < 1e-6


def test_enumerate_weight_grid_empty_model_list_yields_nothing() -> None:
    assert list(_enumerate_weight_grid([], grid=DEFAULT_WEIGHT_GRID)) == []


# ===========================================================================
# calibrate_one_listing_type
# ===========================================================================


def _make_samples(
    n: int,
    *,
    listing_type: ListingType = ListingType.MAINBOARD_TECH,
    regime: float = 0.1,
) -> list[BacktestSample]:
    """Construct n perfectly-aligned samples (decision_score == realized)."""
    out: list[BacktestSample] = []
    for i in range(n):
        score = float(i)
        out.append(
            BacktestSample(
                ipo_id=uuid.uuid4(),
                stock_code=f"{i:04d}.HK",
                listing_type=listing_type,
                pricing_date=date(2024, 6, 14),
                as_of_date=date(2024, 6, 13),
                decision_score=score,
                realized_returns={
                    "5d": score * 0.01,
                    "30d": score * 0.02,
                    "60d": score * 0.03,
                    "180d": score * 0.04,
                },
                regime_score=regime,
                regulatory_regime=RegulatoryRegime.PRE_20250804,
                notes=(),
            )
        )
    return out


def test_calibrate_one_listing_type_insufficient_samples_keeps_baseline() -> None:
    samples = _make_samples(5)  # < MIN_SLICE_N (20)
    baseline = {"dcf": 0.5, "comparable": 0.5}
    result = calibrate_one_listing_type(
        ListingType.MAINBOARD_TECH,
        samples,
        baseline,
    )
    assert result.chosen_weights == baseline
    assert "insufficient samples" in result.reason
    assert result.n_samples == 5
    assert result.monotonicity_passed is True


def test_calibrate_one_listing_type_with_enough_samples_runs_search() -> None:
    samples = _make_samples(25)
    baseline = {"dcf": 0.5, "comparable": 0.5}
    result = calibrate_one_listing_type(
        ListingType.MAINBOARD_TECH,
        samples,
        baseline,
    )
    assert result.n_samples == 25
    assert abs(sum(result.chosen_weights.values()) - 1.0) < 1e-6
    # Perfect samples → IC ≈ 1.0 per horizon → objective near 1
    assert result.objective_value > 0.9


def test_calibrate_v8lite_path_is_flagged_placebo() -> None:
    """R3-3 — under V8LiteScorer-style data (decision_score is single
    signal monotone in realized return), weight grid yields identical
    IC across all candidates → is_placebo must be True so reports can
    surface "calibration was a no-op" honestly instead of pretending
    weights moved.
    """
    samples = _make_samples(25)
    baseline = {"dcf": 0.5, "comparable": 0.5}
    result = calibrate_one_listing_type(
        ListingType.MAINBOARD_TECH,
        samples,
        baseline,
    )
    assert result.is_placebo is True, (
        f"V8Lite-style perfect samples should flag is_placebo=True; "
        f"got {result.is_placebo}. R3-3 not applied — calibration "
        f"output will silently look like it moved weights when it didn't."
    )
    assert result.placebo_reason is not None
    assert "V8LiteScorer" in result.placebo_reason or "invariant" in result.placebo_reason


def test_calibrate_insufficient_samples_is_not_flagged_placebo() -> None:
    """Insufficient-sample case keeps baseline; that's a known
    non-calibration, not a V8Lite placebo. is_placebo stays False
    so reports don't conflate the two cases."""
    samples = _make_samples(5)
    baseline = {"dcf": 0.5, "comparable": 0.5}
    result = calibrate_one_listing_type(
        ListingType.MAINBOARD_TECH,
        samples,
        baseline,
    )
    assert result.is_placebo is False
    assert result.placebo_reason is None


def test_calibrate_uses_regime_pass_baseline_label_not_main_board() -> None:
    """R3-4 — monotonicity check must compare against the v8
    ``regime_pass`` baseline, not lift the slice into ``main_board``.

    Pre-fix the code rewrote ``report.label`` to ``main_board`` so any
    candidate passing the (lower) main_board threshold passed. With the
    fix, the comparison uses the slice's actual ``regime_pass`` label
    which has the correct higher reference IC.

    Verification: chosen_metrics.label must end up as "regime_pass".
    """
    samples = _make_samples(25)
    baseline = {"dcf": 0.5, "comparable": 0.5}
    result = calibrate_one_listing_type(
        ListingType.MAINBOARD_TECH,
        samples,
        baseline,
    )
    assert result.chosen_metrics.label == "regime_pass", (
        f"calibration report label should be 'regime_pass' (not 'main_board'); "
        f"got '{result.chosen_metrics.label}'. R3-4 not applied."
    )


def test_calibrate_one_listing_type_no_candidate_passes_monotonicity() -> None:
    """Adversarial: 25 samples where scoring is fully randomized → IC≈0 → fail vs v8."""
    import random

    rng = random.Random(42)
    samples: list[BacktestSample] = []
    for i in range(25):
        samples.append(
            BacktestSample(
                ipo_id=uuid.uuid4(),
                stock_code=f"X{i}.HK",
                listing_type=ListingType.MAINBOARD_TECH,
                pricing_date=date(2024, 6, 14),
                as_of_date=date(2024, 6, 13),
                decision_score=rng.random(),
                realized_returns={
                    "5d": rng.gauss(0, 0.1),
                    "30d": rng.gauss(0, 0.1),
                    "60d": rng.gauss(0, 0.1),
                    "180d": rng.gauss(0, 0.1),
                },
                regime_score=0.1,
                regulatory_regime=RegulatoryRegime.PRE_20250804,
                notes=(),
            )
        )
    baseline = {"dcf": 0.5, "comparable": 0.5}
    # Tighten tolerance so random scoring definitely fails.
    result = calibrate_one_listing_type(
        ListingType.MAINBOARD_TECH,
        samples,
        baseline,
        ic_tolerance=0.001,
        t_tolerance=0.05,
    )
    # Random IC ≪ v8 baseline → no candidate passes → baseline kept.
    assert result.chosen_weights == baseline
    assert "baseline retained" in result.reason


# ===========================================================================
# Top-level calibrate
# ===========================================================================


def test_calibrate_emits_candidate_yaml_per_listing_type() -> None:
    samples = _make_samples(25, listing_type=ListingType.MAINBOARD_TECH)
    samples += _make_samples(25, listing_type=ListingType.AH_DUAL)
    current_weights = {
        "MB-TECH": {"dcf": 0.5, "comparable": 0.5},
        "AH": {"ah_premium": 0.6, "dcf": 0.4},
    }
    result = calibrate(samples, current_weights=current_weights)
    assert ListingType.MAINBOARD_TECH in result.per_listing_type
    assert ListingType.AH_DUAL in result.per_listing_type
    assert "MB-TECH" in result.candidate_weights_yaml
    assert "AH" in result.candidate_weights_yaml


def test_calibrate_skips_listing_types_without_baseline() -> None:
    samples = _make_samples(25, listing_type=ListingType.MAINBOARD_TECH)
    current_weights = {"MB-TECH": {"dcf": 0.5, "comparable": 0.5}}
    result = calibrate(samples, current_weights=current_weights)
    # Other listing_types skipped → note + absent from result
    assert ListingType.MAINBOARD_TECH in result.per_listing_type
    assert ListingType.CH18A_BIOTECH not in result.per_listing_type
    assert any("no baseline weights" in n for n in result.notes)


def test_calibrate_passed_all_monotonicity_aggregates() -> None:
    samples = _make_samples(25, listing_type=ListingType.MAINBOARD_TECH)
    current_weights = {"MB-TECH": {"dcf": 0.5, "comparable": 0.5}}
    result = calibrate(samples, current_weights=current_weights)
    # Aligned perfect samples → IC=1.0 >> v8 baseline → passes monotonicity.
    assert result.passed_all_monotonicity()


# ---------------------------------------------------------------------------
# Sanity: MIN_SLICE_N is sensible
# ---------------------------------------------------------------------------


def test_min_slice_n_is_at_least_20() -> None:
    """Per ADR 0013 §8c constraint: n ≥ 20 per slice to avoid overfitting."""
    assert MIN_SLICE_N >= 20
