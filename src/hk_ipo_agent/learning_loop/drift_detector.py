"""Drift detector — CUSUM + PSI per PROJECT_SPEC.md §3.12 + ADR 0015 §10a.

Maintains a sliding window of recently-completed predictions (those with
a 6-month checkpoint in ``prediction_outcomes``) and scans for:

- **Accuracy drop** — CUSUM on the rolling decision_correct mean
- **Valuation bias** — CUSUM on the log-ratio of predicted vs. realized
  median price
- **Agent calibration drift** — high-confidence agents whose realized
  hit-rate falls below threshold
- **Bear miss rate** — % of NEGATIVE realized outcomes where the Bear
  agent did NOT flag the risk

Slices by ``ListingType`` / ``RegulatoryRegime`` so a drift in one
sub-population can fire even when overall metrics look fine.

All output goes into ``DriftSignal`` (common.schemas) — this module is
pure analysis, no mutation. The downstream ``AdjustmentProposer``
turns DriftSignals into ProposedAdjustments.

CLAUDE.md "no auto-apply" binding: this module CANNOT modify any
config, prompt, or registry state — it only reads and emits signals.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np

from ..common.enums import AlertLevel, DriftSignalType, ListingType, RegulatoryRegime
from ..common.logging import get_logger
from ..common.schemas import DriftSignal

logger = get_logger(__name__)


# Default thresholds — every one is tunable via DriftDetectorConfig and
# documented in docs/LEARNING_PROTOCOL.md once 10c lands.
DEFAULT_CUSUM_THRESHOLD: float = 4.0  # tabular value for k=0.5, h=4
DEFAULT_CUSUM_K: float = 0.5  # half-stdev slack
DEFAULT_PSI_THRESHOLD: float = 0.20  # PSI > 0.2 = "significant shift"
DEFAULT_BEAR_MISS_RATE: float = 0.40  # >40% missed negatives = drift
DEFAULT_AGENT_CALIBRATION_DROP: float = 0.15
DEFAULT_WINDOW_MIN_N: int = 10  # need at least 10 samples to fire
DEFAULT_ACCURACY_BASELINE: float = 0.70  # expected decision_correct rate;
# CUSUM detects drops below this


@dataclass(frozen=True)
class DriftDetectorConfig:
    """Knobs for the drift detector — all defaults from spec §3.12."""

    cusum_threshold: float = DEFAULT_CUSUM_THRESHOLD
    cusum_k: float = DEFAULT_CUSUM_K
    psi_threshold: float = DEFAULT_PSI_THRESHOLD
    bear_miss_rate: float = DEFAULT_BEAR_MISS_RATE
    agent_calibration_drop: float = DEFAULT_AGENT_CALIBRATION_DROP
    window_min_n: int = DEFAULT_WINDOW_MIN_N
    accuracy_baseline: float = DEFAULT_ACCURACY_BASELINE


@dataclass(frozen=True)
class OutcomeWindowSample:
    """One completed prediction observation fed into drift detection.

    Decoupling input loading from the detector means tests build
    synthetic samples without PG. The CLI loads them from
    ``prediction_outcomes`` + ``prediction_snapshots`` JOIN.
    """

    snapshot_id: str  # UUID as str for ergonomics
    listing_type: ListingType | None
    regulatory_regime: RegulatoryRegime
    decision_correct: bool | None
    predicted_median_price: float | None
    realized_price_at_60d: float | None
    bear_flagged_risk: bool | None  # did debate_output.bear flag risk?
    realized_outcome_negative: bool | None  # did realized return < 0?
    agent_scores: dict[str, float] = field(default_factory=dict)
    agent_realized_hits: dict[str, bool] = field(default_factory=dict)


# ===========================================================================
# CUSUM
# ===========================================================================


def cusum_max_excursion(
    series: list[float],
    *,
    target: float | None = None,
    k: float = DEFAULT_CUSUM_K,
) -> float:
    """Standard tabular CUSUM — returns the maximum absolute excursion.

    Args:
        series: ordered observations; assumed unit-scale (i.e. already
            standardized to z-scores or in [0, 1]).
        target: in-control mean; defaults to series mean.
        k: reference value (half-stdev slack); 0.5 detects 1-stdev shifts.

    Returns:
        max(|S+|, |S-|) where S+ and S- are the cumulative sums.
        Compare to ``cusum_threshold`` (default 4.0) to decide drift.
    """
    if not series:
        return 0.0
    mu = target if target is not None else float(np.mean(series))
    s_pos = 0.0
    s_neg = 0.0
    max_excursion = 0.0
    for x in series:
        s_pos = max(0.0, s_pos + (x - mu) - k)
        s_neg = min(0.0, s_neg + (x - mu) + k)
        max_excursion = max(max_excursion, s_pos, abs(s_neg))
    return max_excursion


# ===========================================================================
# PSI (population stability index)
# ===========================================================================


def population_stability_index(
    expected: list[float],
    actual: list[float],
    *,
    n_bins: int = 10,
) -> float:
    """PSI = sum((actual_pct - expected_pct) * ln(actual_pct / expected_pct)).

    Bins are deciles of the combined distribution (so the test is
    distribution-free). Returns 0.0 when either input is empty / too
    small to bin.

    Conventional reading: < 0.1 stable / 0.1-0.2 slight / > 0.2 significant.
    """
    if len(expected) < n_bins or len(actual) < n_bins:
        return 0.0
    combined = np.concatenate([expected, actual])
    edges = np.quantile(combined, np.linspace(0, 1, n_bins + 1))
    # Make sure edges are unique to avoid empty bins.
    edges = np.unique(edges)
    if len(edges) - 1 < 2:
        return 0.0
    e_hist, _ = np.histogram(expected, bins=edges)
    a_hist, _ = np.histogram(actual, bins=edges)
    e_pct = (e_hist / max(e_hist.sum(), 1)).astype(float)
    a_pct = (a_hist / max(a_hist.sum(), 1)).astype(float)
    # Replace zero pct with tiny epsilon to keep log defined.
    eps = 1e-6
    e_pct = np.where(e_pct == 0, eps, e_pct)
    a_pct = np.where(a_pct == 0, eps, a_pct)
    return float(np.sum((a_pct - e_pct) * np.log(a_pct / e_pct)))


# ===========================================================================
# Main detector
# ===========================================================================


class DriftDetector:
    """Scans a window of completed predictions for drift signals.

    Stateless across calls — callers pass the full window each time. We
    do NOT cache or accumulate state because the lifecycle constraint
    (CLAUDE.md) requires every signal to be evidence-traceable to a
    specific set of snapshot_ids.
    """

    def __init__(self, config: DriftDetectorConfig | None = None) -> None:
        self._cfg = config or DriftDetectorConfig()

    def detect(self, samples: list[OutcomeWindowSample]) -> list[DriftSignal]:
        """Run all 4 detectors over ``samples`` and return signals."""
        if len(samples) < self._cfg.window_min_n:
            logger.info(
                "drift_window_too_small",
                n=len(samples),
                min=self._cfg.window_min_n,
            )
            return []
        signals: list[DriftSignal] = []
        signals.extend(self._accuracy_drop(samples))
        signals.extend(self._valuation_bias(samples))
        signals.extend(self._bear_miss_rate(samples))
        signals.extend(self._agent_calibration_drift(samples))
        return signals

    # ------------------------------------------------------------------
    # Sub-detectors
    # ------------------------------------------------------------------

    def _accuracy_drop(self, samples: list[OutcomeWindowSample]) -> list[DriftSignal]:
        """CUSUM on rolling decision_correct mean per regulatory regime."""
        out: list[DriftSignal] = []
        for regime in (RegulatoryRegime.PRE_20250804, RegulatoryRegime.POST_20250804):
            slice_ = [s for s in samples if s.regulatory_regime == regime]
            series = [
                1.0 if s.decision_correct else 0.0 for s in slice_ if s.decision_correct is not None
            ]
            if len(series) < self._cfg.window_min_n:
                continue
            excursion = cusum_max_excursion(
                series,
                target=self._cfg.accuracy_baseline,
                k=self._cfg.cusum_k,
            )
            if excursion > self._cfg.cusum_threshold:
                out.append(
                    DriftSignal(
                        detection_time=datetime.now(UTC),
                        signal_type=DriftSignalType.ACCURACY_DROP,
                        severity=AlertLevel.WARNING,
                        affected_dimensions={"regulatory_regime": regime.value},
                        metric_value=excursion,
                        threshold=self._cfg.cusum_threshold,
                        sample_count=len(series),
                        evidence=(
                            f"CUSUM excursion {excursion:.2f} on "
                            f"decision_correct over {len(series)} samples"
                        ),
                        related_snapshot_ids=_uuids(slice_),
                    )
                )
        return out

    def _valuation_bias(self, samples: list[OutcomeWindowSample]) -> list[DriftSignal]:
        """PSI on log(predicted/realized) distribution per listing_type."""
        out: list[DriftSignal] = []
        ratios_by_lt: dict[ListingType, list[float]] = {}
        for s in samples:
            pred = s.predicted_median_price
            real = s.realized_price_at_60d
            if s.listing_type is None or pred is None or pred == 0.0 or real is None or real == 0.0:
                continue
            ratios_by_lt.setdefault(s.listing_type, []).append(float(np.log(pred / real)))
        # Compare each listing_type's distribution to the cross-LT pooled
        # baseline — if a single slice has drifted vs the population it's
        # signal.
        all_ratios = [r for lst in ratios_by_lt.values() for r in lst]
        if len(all_ratios) < self._cfg.window_min_n:
            return out
        for lt, slice_ratios in ratios_by_lt.items():
            if len(slice_ratios) < self._cfg.window_min_n // 2:
                continue
            psi = population_stability_index(all_ratios, slice_ratios)
            if psi > self._cfg.psi_threshold:
                out.append(
                    DriftSignal(
                        detection_time=datetime.now(UTC),
                        signal_type=DriftSignalType.VALUATION_BIAS,
                        severity=(
                            AlertLevel.CRITICAL
                            if psi > 2 * self._cfg.psi_threshold
                            else AlertLevel.WARNING
                        ),
                        affected_dimensions={"listing_type": lt.value},
                        metric_value=psi,
                        threshold=self._cfg.psi_threshold,
                        sample_count=len(slice_ratios),
                        evidence=(
                            f"PSI={psi:.3f} for log(pred/real) of "
                            f"{lt.value}; baseline n={len(all_ratios)}, "
                            f"slice n={len(slice_ratios)}"
                        ),
                        related_snapshot_ids=[],
                    )
                )
        return out

    def _bear_miss_rate(self, samples: list[OutcomeWindowSample]) -> list[DriftSignal]:
        """% of negative-realized outcomes where Bear did NOT flag risk."""
        negatives = [s for s in samples if s.realized_outcome_negative]
        if len(negatives) < self._cfg.window_min_n // 2:
            return []
        missed = [s for s in negatives if s.bear_flagged_risk is False]
        miss_rate = len(missed) / len(negatives) if negatives else 0.0
        if miss_rate > self._cfg.bear_miss_rate:
            return [
                DriftSignal(
                    detection_time=datetime.now(UTC),
                    signal_type=DriftSignalType.BEAR_MISS_RATE_HIGH,
                    severity=AlertLevel.WARNING,
                    affected_dimensions={"scope": "all"},
                    metric_value=miss_rate,
                    threshold=self._cfg.bear_miss_rate,
                    sample_count=len(negatives),
                    evidence=(
                        f"{len(missed)}/{len(negatives)} negative outcomes were NOT flagged by Bear"
                    ),
                    related_snapshot_ids=_uuids(missed),
                )
            ]
        return []

    def _agent_calibration_drift(
        self,
        samples: list[OutcomeWindowSample],
    ) -> list[DriftSignal]:
        """High-confidence (score ≥ 70) agents whose realized hit-rate dropped."""
        # Build (agent, hit_rate) from samples that have hit data.
        by_agent: dict[str, list[bool]] = {}
        for s in samples:
            for agent, score in s.agent_scores.items():
                if score < 70.0:
                    continue
                hit = s.agent_realized_hits.get(agent)
                if hit is None:
                    continue
                by_agent.setdefault(agent, []).append(hit)
        out: list[DriftSignal] = []
        for agent, hits in by_agent.items():
            if len(hits) < self._cfg.window_min_n // 2:
                continue
            hit_rate = sum(hits) / len(hits)
            # An agent that gives high scores should be calibrated to a
            # high realized hit rate; "drop" = hit_rate well below 1 - cfg.
            expected = 1.0 - self._cfg.agent_calibration_drop
            if hit_rate < expected:
                out.append(
                    DriftSignal(
                        detection_time=datetime.now(UTC),
                        signal_type=DriftSignalType.AGENT_CALIBRATION_DRIFT,
                        severity=AlertLevel.WARNING,
                        affected_dimensions={"agent_role": agent},
                        metric_value=hit_rate,
                        threshold=expected,
                        sample_count=len(hits),
                        evidence=(
                            f"agent={agent} gave high (≥70) scores; realized "
                            f"hit-rate {hit_rate:.2%} < expected {expected:.2%}"
                        ),
                        related_snapshot_ids=[],
                    )
                )
        return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _uuids(samples: Iterable[OutcomeWindowSample]) -> list[Any]:
    from uuid import UUID

    out: list[Any] = []
    for s in samples:
        try:
            out.append(UUID(s.snapshot_id))
        except (TypeError, ValueError):
            continue
    return out


__all__ = (
    "DEFAULT_BEAR_MISS_RATE",
    "DEFAULT_CUSUM_K",
    "DEFAULT_CUSUM_THRESHOLD",
    "DEFAULT_PSI_THRESHOLD",
    "DEFAULT_WINDOW_MIN_N",
    "DriftDetector",
    "DriftDetectorConfig",
    "OutcomeWindowSample",
    "cusum_max_excursion",
    "population_stability_index",
)

# Suppress unused-import noise.
_ = statistics
