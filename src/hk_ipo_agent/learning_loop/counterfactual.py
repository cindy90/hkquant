"""Counterfactual analysis — Phase 10a per ADR 0015.

For each completed prediction, ask:

1. **"If Bear had been followed"** — would the realized outcome have
   been better? Specifically: replay each snapshot where Bull won the
   debate, count how many actually had negative realized outcomes.
   If Bear's recommendation would have produced systematically better
   outcomes, the Synthesizer's bull-bias is real.

2. **"If only model X were used"** — for each single valuation model,
   compute the price-range hit rate. Compare against the ensemble's
   hit rate. If a single model beats the ensemble, the ensemble's
   blending logic is over-fit.

Spec §3.12: "used to discern whether Synthesizer's trade-off logic is
reasonable. Output counterfactual report (does NOT directly modify the
system)."

CLAUDE.md "no auto-apply" — emits a report, never mutates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from ..common.enums import DecisionType
from ..common.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class CounterfactualSample:
    """One snapshot + realized outcome projected for counterfactual analysis.

    Decouples PG loading from analysis. Field names mirror what the CLI
    would join from ``prediction_snapshots`` + ``prediction_outcomes`` +
    ``debate_output``.
    """

    snapshot_id: UUID
    # Actual synthesizer decision + bull/bear sub-decisions.
    actual_decision: DecisionType
    bull_decision: DecisionType | None
    bear_decision: DecisionType | None
    # Realized outcome at the calibration horizon (default 60d).
    realized_return: float | None
    realized_in_predicted_range: bool | None
    # Per-single-model predicted price (lo / fair / hi).
    single_model_fair_prices: dict[str, float] = field(default_factory=dict)
    # Actual ensemble fair price (for comparison).
    ensemble_fair_price: float | None = None
    # Actual realized price at 60d.
    realized_price_at_60d: float | None = None


@dataclass(frozen=True)
class IfBearReport:
    """Outcome of the "if Bear had been followed" replay."""

    n_total: int
    n_bull_won: int  # snapshots where actual decision matched Bull's
    n_bull_won_bad: int  # of those, how many had negative realized
    bull_won_bad_rate: float
    n_bear_would_have_avoided: int
    bear_advantage: float  # bear_would_have_avoided / max(bull_won_bad, 1)


@dataclass(frozen=True)
class IfSingleModelReport:
    """Hit-rate comparison per single valuation model vs the ensemble."""

    n_samples: int
    ensemble_hit_rate: float
    model_hit_rates: dict[str, float]
    best_single_model: str | None
    best_single_hit_rate: float
    ensemble_advantage: float  # ensemble - best_single (positive = ensemble wins)


@dataclass(frozen=True)
class CounterfactualReport:
    """Aggregate result of counterfactual analysis."""

    if_bear: IfBearReport
    if_single_model: IfSingleModelReport
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "if_bear": {
                "n_total": self.if_bear.n_total,
                "n_bull_won": self.if_bear.n_bull_won,
                "n_bull_won_bad": self.if_bear.n_bull_won_bad,
                "bull_won_bad_rate": self.if_bear.bull_won_bad_rate,
                "n_bear_would_have_avoided": self.if_bear.n_bear_would_have_avoided,
                "bear_advantage": self.if_bear.bear_advantage,
            },
            "if_single_model": {
                "n_samples": self.if_single_model.n_samples,
                "ensemble_hit_rate": self.if_single_model.ensemble_hit_rate,
                "model_hit_rates": self.if_single_model.model_hit_rates,
                "best_single_model": self.if_single_model.best_single_model,
                "best_single_hit_rate": self.if_single_model.best_single_hit_rate,
                "ensemble_advantage": self.if_single_model.ensemble_advantage,
            },
            "summary": self.summary,
        }


# ===========================================================================
# Counterfactual analyzers
# ===========================================================================


def if_bear_followed(samples: list[CounterfactualSample]) -> IfBearReport:
    """Replay: in cases where Bull won the debate, how often was the
    realized outcome bad? Of those bad outcomes, how many would Bear
    have correctly avoided?

    "Bad" outcome = realized_return < 0.
    "Bear would have avoided" = bear_decision == SKIP and bull_decision == PARTICIPATE.
    """
    n_total = len(samples)
    bull_won = [
        s
        for s in samples
        if s.bull_decision is not None
        and s.actual_decision == s.bull_decision
        and s.bear_decision is not None
        and s.bear_decision != s.bull_decision
    ]
    bull_won_bad = [s for s in bull_won if s.realized_return is not None and s.realized_return < 0]
    bear_would_have_avoided = [
        s
        for s in bull_won_bad
        if s.bear_decision in (DecisionType.SKIP, DecisionType.WAIT_FOR_SIGNAL)
    ]
    bull_won_bad_rate = len(bull_won_bad) / len(bull_won) if bull_won else 0.0
    bear_advantage = len(bear_would_have_avoided) / max(len(bull_won_bad), 1)
    return IfBearReport(
        n_total=n_total,
        n_bull_won=len(bull_won),
        n_bull_won_bad=len(bull_won_bad),
        bull_won_bad_rate=bull_won_bad_rate,
        n_bear_would_have_avoided=len(bear_would_have_avoided),
        bear_advantage=bear_advantage,
    )


def if_single_model_used(
    samples: list[CounterfactualSample],
    *,
    hit_tolerance: float = 0.15,
) -> IfSingleModelReport:
    """For each single valuation model, what fraction of samples had
    the realized 60d price within ±tol of the model's fair price?

    Compare to the ensemble's hit rate. Returns the best single model
    + the ensemble's advantage (positive = ensemble blending helps).
    """
    if not samples:
        return IfSingleModelReport(
            n_samples=0,
            ensemble_hit_rate=0.0,
            model_hit_rates={},
            best_single_model=None,
            best_single_hit_rate=0.0,
            ensemble_advantage=0.0,
        )

    # Ensemble hit rate
    ens_hits = [
        s
        for s in samples
        if s.ensemble_fair_price is not None
        and s.realized_price_at_60d is not None
        and _within(s.realized_price_at_60d, s.ensemble_fair_price, hit_tolerance)
    ]
    ens_n = sum(
        1
        for s in samples
        if s.ensemble_fair_price is not None and s.realized_price_at_60d is not None
    )
    ensemble_hit_rate = len(ens_hits) / ens_n if ens_n else 0.0

    # Per-model hit rate
    model_names: set[str] = set()
    for s in samples:
        model_names.update(s.single_model_fair_prices.keys())

    model_hit_rates: dict[str, float] = {}
    for model in sorted(model_names):
        n_for_model = 0
        hits = 0
        for s in samples:
            fair = s.single_model_fair_prices.get(model)
            if fair is None or s.realized_price_at_60d is None:
                continue
            n_for_model += 1
            if _within(s.realized_price_at_60d, fair, hit_tolerance):
                hits += 1
        if n_for_model > 0:
            model_hit_rates[model] = hits / n_for_model

    if model_hit_rates:
        best_model, best_rate = max(model_hit_rates.items(), key=lambda kv: kv[1])
    else:
        best_model, best_rate = None, 0.0

    return IfSingleModelReport(
        n_samples=len(samples),
        ensemble_hit_rate=ensemble_hit_rate,
        model_hit_rates=model_hit_rates,
        best_single_model=best_model,
        best_single_hit_rate=best_rate,
        ensemble_advantage=ensemble_hit_rate - best_rate,
    )


def run_counterfactual(
    samples: list[CounterfactualSample],
    *,
    hit_tolerance: float = 0.15,
) -> CounterfactualReport:
    """Compose the two counterfactual analyses + write a one-line summary."""
    bear = if_bear_followed(samples)
    single = if_single_model_used(samples, hit_tolerance=hit_tolerance)

    summary_parts: list[str] = []
    if bear.bear_advantage >= 0.50:
        summary_parts.append(
            f"Bear would have avoided {bear.bear_advantage:.0%} of "
            f"bull-bad outcomes — synthesizer has bull bias"
        )
    else:
        summary_parts.append(
            f"Bear advantage {bear.bear_advantage:.0%} — synthesizer is "
            "appropriately calibrated on Bull/Bear trade-off"
        )
    if single.ensemble_advantage > 0:
        summary_parts.append(
            f"Ensemble beats best single ({single.best_single_model}) "
            f"by {single.ensemble_advantage:+.1%}"
        )
    elif single.best_single_model is not None:
        summary_parts.append(
            f"Single model {single.best_single_model} beats ensemble by "
            f"{-single.ensemble_advantage:.1%} — blending may be over-fit"
        )
    return CounterfactualReport(
        if_bear=bear,
        if_single_model=single,
        summary="; ".join(summary_parts),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _within(realized: float, target: float, tol: float) -> bool:
    """abs(realized - target) / target <= tol — relative band check."""
    if target == 0:
        return False
    return abs(realized - target) / abs(target) <= tol


__all__ = (
    "CounterfactualReport",
    "CounterfactualSample",
    "IfBearReport",
    "IfSingleModelReport",
    "if_bear_followed",
    "if_single_model_used",
    "run_counterfactual",
)
