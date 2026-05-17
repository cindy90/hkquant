"""Weight calibration for the valuation ensemble.

Per PROJECT_SPEC.md §3.9 + ADR 0005 §3 + ADR 0013 §8c.

Two-stage approach (mirrors NACS v8 5-iteration calibration archive):

1. **Constrained grid search** over candidate weight vectors per
   ``ListingType``. Constraints:
   - **Sum-to-1** (renormalized on emit so the YAML stays well-formed)
   - **Sample size ≥ 20 / slice** (avoid over-fitting; n≈400 total)
   - **Monotonicity vs NACS v8 baseline** — candidate must not drop IC
     by more than ``DEFAULT_IC_TOLERANCE`` or t-stat by more than
     ``DEFAULT_T_TOLERANCE`` on the same samples (the strict ADR 0005
     §3 binding).

2. **Selection**: highest *mean IC across horizons on the
   ``regime_pass`` slice* (the subsample regime≥0 IC is what NACS v8
   optimized for; main_board IC is checked but not the objective).

ADR 0013 explicitly allows falling back from Bayesian optimization to
grid search if a new dep would be needed. We use **grid search** here
to keep the dependency surface minimal (numpy / pandas only, both
already in pyproject).

The output is a candidate ``valuation_weights.yaml`` payload + a
``CalibrationResult`` audit envelope. **The caller is responsible**
for routing it through ``learning_loop/version_manager.bump_version()``
— this module never writes config directly (CLAUDE.md "absolutely no
auto-apply" + lifecycle constraint).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path
from typing import Any

import yaml

from ..common.enums import ListingType
from ..common.logging import get_logger
from .metrics import (
    DEFAULT_IC_TOLERANCE,
    DEFAULT_T_TOLERANCE,
    MetricsReport,
    compute_report,
    get_baseline_iteration,
    monotonicity_constraint,
)
from .runner import DEFAULT_HORIZONS, BacktestSample

logger = get_logger(__name__)

# Where the production weights live (referenced by ensemble.py).
_WEIGHTS_PATH: Path = Path(__file__).resolve().parents[3] / "config" / "valuation_weights.yaml"

# Default grid for each model weight — coarse but covers a 0.05 step
# in the 0.10..0.60 band. With 4 models per listing_type that's 6**4 =
# 1,296 combinations max before sum-to-1 filtering — fast in pure Python.
DEFAULT_WEIGHT_GRID: tuple[float, ...] = (0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50)

# Sample-size floor per ListingType slice. Below this we keep the v8
# baseline weight untouched (avoid over-fitting on tiny samples).
MIN_SLICE_N: int = 20


# ===========================================================================
# Dataclasses
# ===========================================================================


@dataclass(frozen=True)
class WeightCandidate:
    """One candidate weight vector for a single listing_type."""

    listing_type: ListingType
    weights: dict[str, float]  # model_name → weight (sums to 1.0)

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(sorted(self.weights.keys()))


@dataclass(frozen=True)
class SliceCalibration:
    """Calibration outcome for one listing_type slice."""

    listing_type: ListingType
    n_samples: int
    chosen_weights: dict[str, float]
    baseline_weights: dict[str, float]
    chosen_metrics: MetricsReport
    monotonicity_passed: bool
    monotonicity_notes: tuple[str, ...]
    objective_value: float  # mean IC across horizons, regime_pass slice
    reason: str  # human-readable why this candidate won
    # R3-3 — explicit "this calibration was a no-op" marker. Under V8LiteScorer
    # the decision_score is a single signal and weights collapse to a scalar
    # multiplier on Rank IC (invariant to monotonic transforms). The grid
    # search therefore yields the first valid candidate (dict-ordering),
    # which is informationally equivalent to the baseline. Reports must
    # flag this so reviewers don't read "calibrated" as "weights changed".
    # See docs/PLAN_post_v1.0.md §5 R3-3.
    is_placebo: bool = False
    placebo_reason: str | None = None


@dataclass(frozen=True)
class CalibrationResult:
    """Aggregate calibration output across all listing_types."""

    per_listing_type: dict[ListingType, SliceCalibration]
    candidate_weights_yaml: dict[str, dict[str, float]]
    baseline_weights_yaml: dict[str, dict[str, float]]
    notes: tuple[str, ...] = field(default_factory=tuple)

    def passed_all_monotonicity(self) -> bool:
        return all(s.monotonicity_passed for s in self.per_listing_type.values())


# ===========================================================================
# Weight loading
# ===========================================================================


def load_current_weights(path: Path | None = None) -> dict[str, dict[str, float]]:
    """Load ``config/valuation_weights.yaml`` keyed by listing_type value."""
    p = path or _WEIGHTS_PATH
    if not p.exists():
        return {}
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    weights_map = raw.get("weights", {}) or {}
    return {lt: {k: float(v) for k, v in (vals or {}).items()} for lt, vals in weights_map.items()}


def dump_weights_yaml(
    weights: dict[str, dict[str, float]],
    *,
    header_comment: str = "",
) -> str:
    """Render a weights dict as a YAML string (matches ensemble.py format)."""
    body = {"weights": weights}
    text = yaml.safe_dump(body, sort_keys=False, default_flow_style=False, indent=2)
    if header_comment:
        return f"{header_comment.rstrip()}\n{text}"
    return text


# ===========================================================================
# Grid generation
# ===========================================================================


def _enumerate_weight_grid(
    model_names: Iterable[str],
    *,
    grid: tuple[float, ...] = DEFAULT_WEIGHT_GRID,
    tol: float = 1e-6,
) -> Iterator[dict[str, float]]:
    """Yield every weight vector on ``grid`` that sums to 1.0 (within tol).

    Renormalization happens at emit time so each yielded vector is
    already normalized — callers can use directly.
    """
    names = list(model_names)
    n_models = len(names)
    if n_models == 0:
        return
    for combo in product(grid, repeat=n_models):
        s = sum(combo)
        # Skip if sum is non-positive, or far enough from 1 that we'd
        # be cheating to call it "normalized". Allow ±10% wiggle then
        # renormalize.
        if s <= 0 or (abs(s - 1.0) > tol and abs(s - 1.0) > 0.10):
            continue
        normalized = {n: c / s for n, c in zip(names, combo, strict=True)}
        yield normalized


# ===========================================================================
# Scoring: weights → metrics
# ===========================================================================


def _score_samples_with_weights(
    samples: list[BacktestSample],
    weights: dict[str, float],
    *,
    horizons: tuple[str, ...],
) -> MetricsReport:
    """Recompute MetricsReport applying ``weights`` as a linear blend.

    Backtest scorer's decision_score is treated as a single signal; we
    add a perturbation from weights by computing an *aggregated* score
    = sum(weights[k] * (decision_score + listing_type_offset[k])). For
    Phase 8c we simplify: weights act on the per-listing-type subsample
    by scaling decision_score by sum(weights.values()) (which is 1.0
    post-normalization — so the metric is identical regardless of split).

    This keeps calibration honest: with V8LiteScorer the only signal is
    decision_score, so grid search degenerates to "weights don't move
    the metrics within a listing_type." The wider story (per-listing
    optimization) requires the full per-model valuation signals — which
    only the LangGraph pipeline produces. In Phase 8c we therefore
    calibrate by *choosing the listing-type-base score that best fits
    the v8 baseline*, treating each candidate's weights as a
    re-parameterization of that base.

    Returns the per-horizon MetricsReport for the regime_pass slice
    (the calibration objective).
    """
    weight_scale = sum(weights.values()) or 1.0
    per_horizon: dict[str, tuple[list[float], list[float]]] = {}
    for h in horizons:
        predicted: list[float] = []
        realized: list[float] = []
        for s in samples:
            if not s.regime_pass:
                continue
            r = s.realized_returns.get(h)
            if r is None:
                continue
            predicted.append(s.decision_score * weight_scale)
            realized.append(r)
        if predicted:
            per_horizon[h] = (predicted, realized)
    return compute_report(label="regime_pass", per_horizon=per_horizon)


def _mean_ic_across_horizons(report: MetricsReport) -> float:
    """Objective: mean IC across non-empty horizon slices."""
    ics = [m.ic for m in report.horizons.values() if m.n > 0]
    return sum(ics) / len(ics) if ics else 0.0


# ===========================================================================
# Per-listing-type calibration
# ===========================================================================


def calibrate_one_listing_type(
    listing_type: ListingType,
    samples: list[BacktestSample],
    baseline_weights: dict[str, float],
    *,
    horizons: tuple[str, ...] = DEFAULT_HORIZONS,
    ic_tolerance: float = DEFAULT_IC_TOLERANCE,
    t_tolerance: float = DEFAULT_T_TOLERANCE,
    grid: tuple[float, ...] = DEFAULT_WEIGHT_GRID,
    min_slice_n: int = MIN_SLICE_N,
) -> SliceCalibration:
    """Grid-search the best weight vector for one listing_type slice."""
    # Filter samples to this listing_type.
    slice_samples = [s for s in samples if s.listing_type == listing_type]
    if len(slice_samples) < min_slice_n:
        # Insufficient data → keep baseline.
        notes = (
            f"slice n={len(slice_samples)} < min {min_slice_n}; keeping baseline weights untouched",
        )
        baseline_metrics = _score_samples_with_weights(
            slice_samples,
            baseline_weights,
            horizons=horizons,
        )
        return SliceCalibration(
            listing_type=listing_type,
            n_samples=len(slice_samples),
            chosen_weights=dict(baseline_weights),
            baseline_weights=dict(baseline_weights),
            chosen_metrics=baseline_metrics,
            monotonicity_passed=True,
            monotonicity_notes=notes,
            objective_value=_mean_ic_across_horizons(baseline_metrics),
            reason="insufficient samples; baseline retained",
        )

    # v8 baseline (used by monotonicity_constraint).
    v8_baseline = get_baseline_iteration()

    # R3-3 — V8LiteScorer placebo detection. Under that scorer the only
    # signal driving decision_score is listing_type + cluster_bonus
    # (single scalar), so multiplying by sum(weights) is a monotonic
    # transform that does NOT change Rank IC. We record the baseline
    # mean IC and detect later if every candidate's IC ≈ baseline.
    baseline_report = _score_samples_with_weights(
        slice_samples, baseline_weights, horizons=horizons
    )
    baseline_objective = _mean_ic_across_horizons(baseline_report)

    best: SliceCalibration | None = None
    candidate_objectives: list[float] = []
    for candidate_weights in _enumerate_weight_grid(
        baseline_weights.keys(),
        grid=grid,
    ):
        report = _score_samples_with_weights(
            slice_samples,
            candidate_weights,
            horizons=horizons,
        )
        # R3-4 — use the report's actual ``regime_pass`` label against the
        # ``regime_pass`` baseline (v8 fixture has both main_board and
        # regime_pass entries). Pre-fix the report's label was forcibly
        # rewritten to ``main_board``, comparing regime-pass slice metrics
        # against the wider main_board baseline. main_board has a lower
        # ceiling (regime gate selects the more-bullish subsample), so
        # ANY candidate trivially passed monotonicity. This is the
        # "偷换概念" problem flagged in the 2026-05-17 review.
        passed, violations = monotonicity_constraint(
            report,  # label is already "regime_pass"
            v8_baseline,
            ic_tolerance=ic_tolerance,
            t_tolerance=t_tolerance,
        )
        if not passed:
            continue
        objective = _mean_ic_across_horizons(report)
        candidate_objectives.append(objective)
        if best is None or objective > best.objective_value:
            best = SliceCalibration(
                listing_type=listing_type,
                n_samples=len(slice_samples),
                chosen_weights=candidate_weights,
                baseline_weights=dict(baseline_weights),
                chosen_metrics=report,
                monotonicity_passed=True,
                monotonicity_notes=tuple(violations),
                objective_value=objective,
                reason=(
                    f"grid_search_best mean_IC={objective:.4f} over {len(slice_samples)} samples"
                ),
            )

    if best is None:
        # No candidate passed monotonicity → keep baseline.
        return SliceCalibration(
            listing_type=listing_type,
            n_samples=len(slice_samples),
            chosen_weights=dict(baseline_weights),
            baseline_weights=dict(baseline_weights),
            chosen_metrics=baseline_report,
            monotonicity_passed=False,
            monotonicity_notes=("no candidate cleared monotonicity vs v8; baseline retained",),
            objective_value=baseline_objective,
            reason="no candidate passed monotonicity; baseline retained",
        )

    # R3-3 — placebo detection. If every candidate that passed monotonicity
    # produced the same mean IC (within float noise), weights weren't
    # actually moving the metric — that's the V8LiteScorer no-op pattern.
    is_placebo = False
    placebo_reason = None
    if candidate_objectives:
        spread = max(candidate_objectives) - min(candidate_objectives)
        if spread < 1e-9:
            is_placebo = True
            placebo_reason = (
                f"All {len(candidate_objectives)} passing candidates produced identical "
                f"mean IC = {best.objective_value:.6f} (spread {spread:.2e}). Under "
                "V8LiteScorer the decision_score is a single signal and weight "
                "renormalization is a monotonic transform that leaves Rank IC "
                "invariant — the calibration is informational only. Real per-model "
                "calibration requires FullPipelineScorer."
            )
    return SliceCalibration(
        listing_type=best.listing_type,
        n_samples=best.n_samples,
        chosen_weights=best.chosen_weights,
        baseline_weights=best.baseline_weights,
        chosen_metrics=best.chosen_metrics,
        monotonicity_passed=best.monotonicity_passed,
        monotonicity_notes=best.monotonicity_notes,
        objective_value=best.objective_value,
        reason=best.reason,
        is_placebo=is_placebo,
        placebo_reason=placebo_reason,
    )


# ===========================================================================
# Top-level entry point
# ===========================================================================


def calibrate(
    samples: list[BacktestSample],
    *,
    current_weights: dict[str, dict[str, float]] | None = None,
    horizons: tuple[str, ...] = DEFAULT_HORIZONS,
    ic_tolerance: float = DEFAULT_IC_TOLERANCE,
    t_tolerance: float = DEFAULT_T_TOLERANCE,
    grid: tuple[float, ...] = DEFAULT_WEIGHT_GRID,
    min_slice_n: int = MIN_SLICE_N,
) -> CalibrationResult:
    """Calibrate per-listing-type weights against backtest samples.

    Args:
        samples: BacktestSample iterable from ``runner.run_walk_forward``.
        current_weights: starting weights (yaml-shaped); defaults to
            ``config/valuation_weights.yaml``.
        horizons: which horizons to evaluate.
        ic_tolerance / t_tolerance / grid / min_slice_n: see
            ``calibrate_one_listing_type``.

    Returns:
        ``CalibrationResult`` — caller routes ``candidate_weights_yaml``
        through ``learning_loop/version_manager.bump_version()`` (NOT
        directly written to disk; lifecycle constraint).
    """
    baseline_yaml = current_weights or load_current_weights()
    per_lt: dict[ListingType, SliceCalibration] = {}
    candidate_yaml: dict[str, dict[str, float]] = {}
    overall_notes: list[str] = []

    for lt in ListingType:
        baseline_weights = baseline_yaml.get(lt.value, {})
        if not baseline_weights:
            overall_notes.append(
                f"{lt.value}: no baseline weights configured; skipping calibration"
            )
            continue
        slice_result = calibrate_one_listing_type(
            lt,
            samples,
            baseline_weights,
            horizons=horizons,
            ic_tolerance=ic_tolerance,
            t_tolerance=t_tolerance,
            grid=grid,
            min_slice_n=min_slice_n,
        )
        per_lt[lt] = slice_result
        candidate_yaml[lt.value] = dict(slice_result.chosen_weights)

    return CalibrationResult(
        per_listing_type=per_lt,
        candidate_weights_yaml=candidate_yaml,
        baseline_weights_yaml=baseline_yaml,
        notes=tuple(overall_notes),
    )


__all__ = (
    "DEFAULT_WEIGHT_GRID",
    "MIN_SLICE_N",
    "CalibrationResult",
    "SliceCalibration",
    "WeightCandidate",
    "calibrate",
    "calibrate_one_listing_type",
    "dump_weights_yaml",
    "load_current_weights",
)

# Suppress unused-import warning for type-hint-only imports.
_ = Any
