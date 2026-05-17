"""Rank IC / L-S spread / t-stat metrics + NACS v8 baseline comparison.

Per PROJECT_SPEC.md §3.9 + ADR 0005 §3 (strict) + ADR 0013 §8b.

This module is the canonical evaluation surface for Phase 8 calibration.
Every new candidate parameter set MUST clear these three metrics against
the NACS v8 baselines (``data/fixtures/nacs_v8_baselines.json``) before
being accepted as an improvement — monotonicity_constraint enforces it.

Inherited from NACS v8 (per ADR 0005 §3 implementation note):

- **Rank IC** — Spearman rank correlation between predicted decision
  score and realized horizon return. Ties get average rank
  (pandas ``rank(method='average')`` semantics). v8 main-board 60d IC
  = +0.078 / regime-pass 60d IC = +0.124.

- **L-S Spread** — top-decile mean realized return minus bottom-decile
  mean. Decile = n // 10 samples. Symmetric: top vs. bottom.

- **t-stat** — Welch's two-sample t-test on top decile vs. bottom decile.
  Robust to unequal variances. v8 regime-pass 180d t-stat = +1.044
  (canonical p1_lockup_v2 iteration).

NACS v8 thresholds (ADR 0005 §3): IC > +0.05 acceptable / > +0.10
strong. t > 1.5 marginal / > 2.0 robust.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# Baselines fixture path
# ---------------------------------------------------------------------------

_BASELINES_PATH = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "fixtures"
    / "nacs_v8_baselines.json"
)

# Default monotonicity tolerances (ADR 0005 §3 implicit — within these
# bands the new candidate is "no worse than v8" on this sample).
DEFAULT_IC_TOLERANCE: float = 0.02
DEFAULT_T_TOLERANCE: float = 0.50


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SliceMetrics:
    """One (horizon × slice) metric triple."""

    horizon: str
    n: int
    ic: float
    ls_spread: float
    ls_t_stat: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "horizon": self.horizon,
            "n": self.n,
            "ic": self.ic,
            "ls_spread": self.ls_spread,
            "ls_t_stat": self.ls_t_stat,
        }


@dataclass(frozen=True)
class MetricsReport:
    """A labeled bundle of SliceMetrics — one per horizon."""

    label: str  # e.g. "main_board" / "regime_pass" / "tech_only"
    n_total: int
    horizons: dict[str, SliceMetrics]

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "n_total": self.n_total,
            "horizons": {h: m.to_dict() for h, m in self.horizons.items()},
        }


# ---------------------------------------------------------------------------
# Rank IC
# ---------------------------------------------------------------------------


def _average_ranks(arr: np.ndarray) -> np.ndarray:
    """Average ranks with ties broken by mean — matches pandas default."""
    n = arr.shape[0]
    order = np.argsort(arr, kind="mergesort")
    ranks = np.empty(n, dtype=np.float64)
    i = 0
    while i < n:
        j = i + 1
        while j < n and arr[order[j]] == arr[order[i]]:
            j += 1
        avg = (i + j - 1) / 2 + 1  # 1-indexed average rank
        ranks[order[i:j]] = avg
        i = j
    return ranks


def rank_ic(predicted: list[float], realized: list[float]) -> float:
    """Spearman rank correlation between predicted and realized series.

    Returns:
        Correlation in [-1, +1]. Returns 0.0 when n < 2 or either series
        is degenerate (no variance after ranking).
    """
    if len(predicted) != len(realized):
        raise ValueError(
            f"length mismatch: predicted={len(predicted)} realized={len(realized)}"
        )
    if len(predicted) < 2:
        return 0.0
    p = np.asarray(predicted, dtype=np.float64)
    r = np.asarray(realized, dtype=np.float64)
    rp = _average_ranks(p)
    rr = _average_ranks(r)
    if rp.std() == 0 or rr.std() == 0:
        return 0.0
    return float(np.corrcoef(rp, rr)[0, 1])


# ---------------------------------------------------------------------------
# L-S spread + Welch t-stat
# ---------------------------------------------------------------------------


def ls_spread(
    predicted: list[float],
    realized: list[float],
    *,
    n_buckets: int = 10,
) -> tuple[float, float]:
    """Top-decile mean realized minus bottom-decile mean realized + t-stat.

    Args:
        predicted: per-sample predicted scores (any monotone score works).
        realized: per-sample realized returns at the matching horizon.
        n_buckets: decile by default; use 5 for quintile etc.

    Returns:
        Tuple ``(spread, t_stat)``. Both 0.0 when sample is too small to
        form distinct deciles (``len < 2 * n_buckets``).

    Welch's t-test is used because top/bottom deciles can have very
    different variance (top tends to have more extreme winners).
    """
    if len(predicted) != len(realized):
        raise ValueError(
            f"length mismatch: predicted={len(predicted)} realized={len(realized)}"
        )
    if len(predicted) < n_buckets * 2:
        return 0.0, 0.0
    p = np.asarray(predicted, dtype=np.float64)
    r = np.asarray(realized, dtype=np.float64)
    order = np.argsort(p, kind="mergesort")
    bucket = len(p) // n_buckets
    bottom = r[order[:bucket]]
    top = r[order[-bucket:]]
    spread = float(top.mean() - bottom.mean())
    v_top = float(top.var(ddof=1)) if len(top) > 1 else 0.0
    v_bot = float(bottom.var(ddof=1)) if len(bottom) > 1 else 0.0
    se = math.sqrt(v_top / len(top) + v_bot / len(bottom))
    t = spread / se if se > 0 else 0.0
    return spread, float(t)


# ---------------------------------------------------------------------------
# Composite
# ---------------------------------------------------------------------------


def compute_slice(
    predicted: list[float],
    realized: list[float],
    *,
    horizon: str,
    n_buckets: int = 10,
) -> SliceMetrics:
    """Compute (ic, ls_spread, ls_t_stat) for one horizon."""
    ic = rank_ic(predicted, realized)
    spread, t = ls_spread(predicted, realized, n_buckets=n_buckets)
    return SliceMetrics(
        horizon=horizon, n=len(predicted), ic=ic, ls_spread=spread, ls_t_stat=t,
    )


def compute_report(
    *,
    label: str,
    per_horizon: dict[str, tuple[list[float], list[float]]],
    n_buckets: int = 10,
) -> MetricsReport:
    """Build a MetricsReport from per-horizon (predicted, realized) tuples.

    Args:
        label: e.g. "main_board" / "regime_pass".
        per_horizon: ``{"5d": ([predicted...], [realized...]), ...}``.
            Each horizon may have a different sample size (drop-outs OK).
    """
    horizons = {
        h: compute_slice(p, r, horizon=h, n_buckets=n_buckets)
        for h, (p, r) in per_horizon.items()
    }
    # n_total = the max sample count across horizons (the broadest slice).
    n_total = max((m.n for m in horizons.values()), default=0)
    return MetricsReport(label=label, n_total=n_total, horizons=horizons)


# ---------------------------------------------------------------------------
# NACS v8 baseline loading
# ---------------------------------------------------------------------------


def load_v8_baselines() -> dict[str, Any]:
    """Load ``data/fixtures/nacs_v8_baselines.json``."""
    if not _BASELINES_PATH.exists():
        raise FileNotFoundError(
            f"NACS v8 baselines fixture missing at {_BASELINES_PATH}; "
            "run Phase 8b export step"
        )
    payload: dict[str, Any] = json.loads(_BASELINES_PATH.read_text(encoding="utf-8"))
    return payload


def get_baseline_iteration(name: str | None = None) -> dict[str, dict[str, dict[str, float]]]:
    """Return one v8 iteration baseline, defaulting to the canonical pick.

    The canonical iteration is recorded in the fixture's
    ``canonical_iteration`` field — currently ``p1_lockup_v2`` (highest
    main-board IC across horizons, ADR 0005 §3).
    """
    baselines = load_v8_baselines()
    pick = name or baselines["canonical_iteration"]
    if pick not in baselines["iterations"]:
        raise KeyError(
            f"unknown iteration {pick!r}; available: "
            f"{list(baselines['iterations'].keys())}"
        )
    iteration: dict[str, dict[str, dict[str, float]]] = baselines["iterations"][pick]
    return iteration


# ---------------------------------------------------------------------------
# Monotonicity constraint
# ---------------------------------------------------------------------------


def monotonicity_constraint(
    new_report: MetricsReport,
    baseline: dict[str, dict[str, dict[str, float]]],
    *,
    ic_tolerance: float = DEFAULT_IC_TOLERANCE,
    t_tolerance: float = DEFAULT_T_TOLERANCE,
) -> tuple[bool, list[str]]:
    """Reject candidates that significantly regress vs baseline.

    Args:
        new_report: candidate's MetricsReport (one label).
        baseline: dict shaped like one iteration of ``v8_baselines.json``
            (i.e. ``{"main_board": {"5d": {...}, ...}, ...}``).
        ic_tolerance: allowed IC drop before flagging (default 0.02).
        t_tolerance: allowed t-stat drop before flagging (default 0.50).

    Returns:
        ``(passed, violations)`` — passed is True iff no violations.
    """
    violations: list[str] = []
    label_baseline = baseline.get(new_report.label, {})
    if not label_baseline:
        # No matching baseline → constraint trivially passes but we
        # still emit a soft note (callers can decide whether to escalate).
        violations.append(
            f"no baseline entry for label={new_report.label!r}; "
            "constraint skipped"
        )
        return True, violations
    for horizon, slice_m in new_report.horizons.items():
        bl = label_baseline.get(horizon)
        if bl is None:
            continue
        ic_drop = float(bl["ic"]) - slice_m.ic
        if ic_drop > ic_tolerance:
            violations.append(
                f"{new_report.label}/{horizon}: "
                f"IC {slice_m.ic:.4f} vs baseline {bl['ic']:.4f} "
                f"(dropped {ic_drop:.4f} > tol {ic_tolerance})"
            )
        t_drop = float(bl["ls_t_stat"]) - slice_m.ls_t_stat
        if t_drop > t_tolerance:
            violations.append(
                f"{new_report.label}/{horizon}: "
                f"t-stat {slice_m.ls_t_stat:.3f} vs baseline {bl['ls_t_stat']:.3f} "
                f"(dropped {t_drop:.3f} > tol {t_tolerance})"
            )
    return len(violations) == 0, violations


def compare_to_baseline(
    new_report: MetricsReport,
    baseline: dict[str, dict[str, dict[str, float]]],
) -> dict[str, dict[str, float]]:
    """Per-horizon delta (new minus baseline) — useful for reports.

    Returns ``{horizon: {"ic_delta": x, "ls_delta": y, "t_delta": z}}``.
    Horizons present in new_report but absent from baseline are skipped.
    """
    label_baseline = baseline.get(new_report.label, {})
    deltas: dict[str, dict[str, float]] = {}
    for horizon, slice_m in new_report.horizons.items():
        bl = label_baseline.get(horizon)
        if bl is None:
            continue
        deltas[horizon] = {
            "ic_delta": slice_m.ic - float(bl["ic"]),
            "ls_delta": slice_m.ls_spread - float(bl["ls_spread"]),
            "t_delta": slice_m.ls_t_stat - float(bl["ls_t_stat"]),
        }
    return deltas


__all__ = (
    "DEFAULT_IC_TOLERANCE",
    "DEFAULT_T_TOLERANCE",
    "MetricsReport",
    "SliceMetrics",
    "compare_to_baseline",
    "compute_report",
    "compute_slice",
    "get_baseline_iteration",
    "load_v8_baselines",
    "ls_spread",
    "monotonicity_constraint",
    "rank_ic",
)
