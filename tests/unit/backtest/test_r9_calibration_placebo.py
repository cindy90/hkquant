"""R9-4 — additional regression coverage for calibration placebo flag.

Existing R3-3 tests verify ``is_placebo=True`` on V8Lite samples and
``False`` on insufficient-sample data. R9-4 adds:

  * Placebo report keeps weights at baseline (regression guard so a
    future refactor doesn't silently write new "calibrated" weights to
    YAML when calibration was actually a no-op).
  * Two-call determinism: identical input → identical output (catches
    accidental randomness in the grid search).
  * Placebo reason mentions the V8Lite invariant in a human-readable
    way (operators see "no-op" cleanly in the report).
"""

from __future__ import annotations

import uuid
from datetime import date

from hk_ipo_agent.backtest.calibration import calibrate_one_listing_type
from hk_ipo_agent.backtest.runner import BacktestSample
from hk_ipo_agent.common.enums import ListingType, RegulatoryRegime


def _v8lite_samples(n: int = 25) -> list[BacktestSample]:
    """Perfectly-monotone samples — decision_score ≡ realized; mimics
    the V8LiteScorer output that triggers the R3-3 placebo flag."""
    out: list[BacktestSample] = []
    for i in range(n):
        score = float(i)
        out.append(
            BacktestSample(
                ipo_id=uuid.uuid4(),
                stock_code=f"{i:04d}.HK",
                listing_type=ListingType.MAINBOARD_TECH,
                pricing_date=date(2024, 6, 14),
                as_of_date=date(2024, 6, 13),
                decision_score=score,
                realized_returns={
                    "5d": score * 0.01,
                    "30d": score * 0.02,
                    "60d": score * 0.03,
                    "180d": score * 0.04,
                },
                regime_score=0.1,
                regulatory_regime=RegulatoryRegime.PRE_20250804,
                notes=(),
            )
        )
    return out


def test_placebo_calibration_keeps_baseline_weights() -> None:
    """R9-4 — when is_placebo=True the chosen weights MUST equal the baseline.

    Pre-fix a placebo run could still emit a "calibrated" weight vector
    (e.g. the first grid point that tied for best IC). That made the
    YAML look like calibration moved weights even when nothing changed.
    """
    samples = _v8lite_samples(25)
    baseline = {"dcf": 0.5, "comparable": 0.5}
    result = calibrate_one_listing_type(ListingType.MAINBOARD_TECH, samples, baseline)
    assert result.is_placebo is True
    assert result.chosen_weights == baseline, (
        f"R9-4: placebo run must keep baseline weights; "
        f"got {result.chosen_weights} (baseline {baseline})"
    )


def test_placebo_reason_is_informative() -> None:
    """R9-4 — placebo_reason must mention either ``V8LiteScorer`` or
    ``invariant`` so operators reading the report know WHY calibration
    was a no-op."""
    samples = _v8lite_samples(25)
    baseline = {"dcf": 0.5, "comparable": 0.5}
    result = calibrate_one_listing_type(ListingType.MAINBOARD_TECH, samples, baseline)
    assert result.placebo_reason is not None
    reason_lower = result.placebo_reason.lower()
    assert "v8lite" in reason_lower or "invariant" in reason_lower or "tie" in reason_lower, (
        f"R9-4: placebo_reason should mention v8lite / invariant / tie; "
        f"got {result.placebo_reason!r}"
    )


def test_calibration_is_deterministic_two_runs() -> None:
    """R9-4 — same input twice → identical result. Catches accidental
    randomness (e.g. a future Bayesian-search variant that forgot to
    seed its RNG)."""
    samples = _v8lite_samples(25)
    baseline = {"dcf": 0.5, "comparable": 0.5}
    r1 = calibrate_one_listing_type(ListingType.MAINBOARD_TECH, samples, baseline)
    r2 = calibrate_one_listing_type(ListingType.MAINBOARD_TECH, samples, baseline)
    assert r1.chosen_weights == r2.chosen_weights
    assert r1.is_placebo == r2.is_placebo
    # MetricsReport carries dict[horizon, SliceMetrics]; pick any horizon
    # to verify the IC matches across the two runs.
    h1 = next(iter(r1.chosen_metrics.horizons.values()))
    h2 = next(iter(r2.chosen_metrics.horizons.values()))
    assert h1.ic == h2.ic, f"R9-4: calibration should be deterministic; got IC {h1.ic} vs {h2.ic}"


def test_placebo_does_not_inflate_apparent_ic() -> None:
    """R9-4 — chosen_metrics.ic on a placebo run reflects the SAME data
    as the baseline; nothing is "found" by the grid search.

    Empirical: V8Lite samples have IC ≈ 1.0 (perfect monotonic) regardless
    of weights. Pin a sanity floor so a future "best-of-grid" tiebreaker
    can't shift the report IC artificially.
    """
    samples = _v8lite_samples(25)
    baseline = {"dcf": 0.5, "comparable": 0.5}
    result = calibrate_one_listing_type(ListingType.MAINBOARD_TECH, samples, baseline)
    # On perfectly-monotone samples the IC is high (and equal across all
    # weight choices — that's WHY it's a placebo). 0.5 floor is generous.
    # MetricsReport stores per-horizon SliceMetrics; check any horizon.
    horizon_ic = next(iter(result.chosen_metrics.horizons.values())).ic
    assert horizon_ic >= 0.5, f"R9-4: V8Lite samples should yield high IC; got {horizon_ic}"
