"""Backtest report writer — markdown + optional PDF/HTML.

Per PROJECT_SPEC.md §3.9 + ADR 0013 §8c.

Layout (markdown):

    # Backtest Run {run_id}

    - Run window: {started_at} → {finished_at}
    - Samples: {n_total} total / {n_regime_pass} regime-pass
    - Config snapshot summary

    ## Metrics by slice × horizon

    Tables: rows = horizons, cols = (IC, n, L-S spread, t-stat) for
    main_board and regime_pass slices.

    ## NACS v8 baseline comparison

    Diff table vs canonical (default p1_lockup_v2): ic_delta /
    ls_delta / t_delta per horizon.

    ## Top / bottom decile case study

    For the longest horizon, list the 3 highest- and 3 lowest-scoring
    samples with their realized return.

    ## Calibration outcome (optional)

    If a CalibrationResult is attached, render per-listing-type
    chosen vs baseline weights + monotonicity pass/fail.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ..common.logging import get_logger
from .metrics import (
    SliceMetrics,
    compare_to_baseline,
    get_baseline_iteration,
)

if TYPE_CHECKING:
    from .calibration import CalibrationResult
    from .runner import BacktestRun, BacktestSample

logger = get_logger(__name__)

DEFAULT_REPORT_DIR: Path = Path("reports") / "backtest"


# ===========================================================================
# Section writers
# ===========================================================================


def _header(run: BacktestRun) -> str:
    return (
        f"# Backtest Run `{run.run_id}`\n\n"
        f"- Started: `{run.started_at.isoformat()}`\n"
        f"- Finished: `{run.finished_at.isoformat()}`\n"
        f"- Samples: **{run.n_total}** total / **{run.n_regime_pass}** regime-pass "
        f"(regime_score ≥ 0)\n"
        f"- Config snapshot keys: "
        f"{', '.join(sorted(run.config_snapshot.keys())) or '(none)'}\n"
    )


def _metrics_table(label: str, horizons: dict[str, SliceMetrics]) -> str:
    if not horizons:
        return f"### {label}\n\n_no samples in slice_\n"
    rows = [
        "| horizon | n | IC | L-S spread | t-stat |",
        "|---|---:|---:|---:|---:|",
    ]
    for h, m in sorted(horizons.items()):
        rows.append(
            f"| {h} | {m.n} | {m.ic:+.4f} | {m.ls_spread:+.4f} | {m.ls_t_stat:+.3f} |"
        )
    return f"### {label}\n\n" + "\n".join(rows) + "\n"


def _metrics_section(run: BacktestRun) -> str:
    parts = ["## Metrics by slice × horizon\n"]
    for label, report in run.metrics_by_label.items():
        parts.append(_metrics_table(label, report.horizons))
    return "\n".join(parts) + "\n"


def _baseline_comparison_section(run: BacktestRun) -> str:
    try:
        baseline = get_baseline_iteration()
    except (FileNotFoundError, KeyError):
        return "## NACS v8 baseline comparison\n\n_baseline fixture missing_\n"
    parts = ["## NACS v8 baseline comparison (canonical p1_lockup_v2)\n"]
    for label, report in run.metrics_by_label.items():
        deltas = compare_to_baseline(report, baseline)
        if not deltas:
            parts.append(f"### {label}\n\n_no overlap with baseline_\n")
            continue
        rows = [
            "| horizon | IC Δ | L-S Δ | t Δ |",
            "|---|---:|---:|---:|",
        ]
        for h in sorted(deltas.keys()):
            d = deltas[h]
            rows.append(
                f"| {h} | {d['ic_delta']:+.4f} | "
                f"{d['ls_delta']:+.4f} | {d['t_delta']:+.3f} |"
            )
        parts.append(f"### {label}\n\n" + "\n".join(rows) + "\n")
    return "\n".join(parts) + "\n"


def _case_study_section(
    run: BacktestRun, *, horizon: str = "60d", k: int = 3,
) -> str:
    candidates = [
        s for s in run.samples if s.realized_returns.get(horizon) is not None
    ]
    if not candidates:
        return f"## Case study (horizon={horizon})\n\n_no samples with this horizon_\n"
    by_score = sorted(candidates, key=lambda s: s.decision_score)
    bottom = by_score[:k]
    top = list(reversed(by_score[-k:]))
    parts = [
        f"## Case study (horizon={horizon})\n",
        "### Top decile (highest decision_score)\n",
        _samples_table(top, horizon),
        "\n### Bottom decile (lowest decision_score)\n",
        _samples_table(bottom, horizon),
    ]
    return "\n".join(parts) + "\n"


def _samples_table(samples: list[BacktestSample], horizon: str) -> str:
    rows = [
        "| stock_code | listing_type | pricing_date | score | realized |",
        "|---|---|---|---:|---:|",
    ]
    for s in samples:
        realized = s.realized_returns.get(horizon)
        realized_str = f"{realized:+.3f}" if realized is not None else "—"
        rows.append(
            f"| {s.stock_code or '—'} | "
            f"{s.listing_type.value if s.listing_type else '—'} | "
            f"{s.pricing_date.isoformat()} | "
            f"{s.decision_score:+.3f} | "
            f"{realized_str} |"
        )
    return "\n".join(rows)


def _calibration_section(cal: CalibrationResult | None) -> str:
    if cal is None:
        return ""
    parts = ["## Calibration outcome\n"]
    parts.append(
        f"- Passed all monotonicity checks: **{cal.passed_all_monotonicity()}**\n"
    )
    if cal.notes:
        parts.append("- Notes:\n")
        for note in cal.notes:
            parts.append(f"  - {note}")
        parts.append("")
    rows = [
        "| listing_type | n | objective (mean IC) | monotonicity | reason |",
        "|---|---:|---:|:---:|---|",
    ]
    for lt, slc in cal.per_listing_type.items():
        mono = "✓" if slc.monotonicity_passed else "✗"
        rows.append(
            f"| {lt.value} | {slc.n_samples} | {slc.objective_value:+.4f} | "
            f"{mono} | {slc.reason} |"
        )
    parts.append("\n".join(rows) + "\n")

    # Per-listing weight diff
    parts.append("\n### Weight changes\n")
    for lt, slc in cal.per_listing_type.items():
        before = slc.baseline_weights
        after = slc.chosen_weights
        diff_rows = [
            f"#### {lt.value}\n",
            "| model | before | after | Δ |",
            "|---|---:|---:|---:|",
        ]
        keys = sorted(set(before.keys()) | set(after.keys()))
        for k in keys:
            b = before.get(k, 0.0)
            a = after.get(k, 0.0)
            diff_rows.append(f"| {k} | {b:.3f} | {a:.3f} | {a - b:+.3f} |")
        parts.append("\n".join(diff_rows) + "\n")
    return "\n".join(parts) + "\n"


# ===========================================================================
# Top-level renderer
# ===========================================================================


def render_markdown(
    run: BacktestRun,
    *,
    calibration: CalibrationResult | None = None,
    case_study_horizon: str = "60d",
    case_study_k: int = 3,
) -> str:
    """Render the full markdown report."""
    sections = [
        _header(run),
        _metrics_section(run),
        _baseline_comparison_section(run),
        _case_study_section(run, horizon=case_study_horizon, k=case_study_k),
        _calibration_section(calibration),
    ]
    return "\n".join(filter(None, sections))


def write_report(
    run: BacktestRun,
    *,
    calibration: CalibrationResult | None = None,
    out_dir: Path = DEFAULT_REPORT_DIR,
    case_study_horizon: str = "60d",
) -> Path:
    """Render + write to ``{out_dir}/YYYY-MM-DD_{run_id}.md``.

    Returns the path written. Creates the directory tree if missing.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    date_part = run.started_at.date().isoformat()
    out_path = out_dir / f"{date_part}_{run.run_id}.md"
    content = render_markdown(
        run, calibration=calibration, case_study_horizon=case_study_horizon,
    )
    out_path.write_text(content, encoding="utf-8")
    logger.info(
        "backtest_report_written",
        path=str(out_path),
        run_id=str(run.run_id),
        n_samples=run.n_total,
    )
    return out_path


__all__ = (
    "DEFAULT_REPORT_DIR",
    "render_markdown",
    "write_report",
)

# Suppress unused-import noise.
_ = datetime
