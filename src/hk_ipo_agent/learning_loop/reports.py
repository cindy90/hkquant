"""Monthly learning-loop report — Phase 10b per ADR 0015 + spec §3.12.

Renders a markdown report with the canonical sections:

- Calibration state: accuracy over trailing 30/60/90 days
- Drift detector summary (DriftSignal[])
- Pending proposals: reviews with adjustment_status=proposed
- Already-applied adjustments + their post-hoc effect

Pure renderer. No mutation. Writes to ``reports/learning/{YYYY-MM}.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from ..common.logging import get_logger
from ..common.schemas import DriftSignal
from .attribution_aggregator import AggregatedFinding
from .counterfactual import CounterfactualReport

logger = get_logger(__name__)

DEFAULT_REPORT_DIR: Path = Path("reports") / "learning"


@dataclass(frozen=True)
class CalibrationStateRow:
    """One row of the calibration-state table."""

    window: str  # e.g. "30d" / "60d" / "90d"
    n_samples: int
    accuracy: float
    avg_decision_correct: float


@dataclass(frozen=True)
class PendingProposalRow:
    review_id: str
    snapshot_id: str
    proposal_count: int
    primary_attribution: str | None
    created_at: datetime


@dataclass(frozen=True)
class AppliedAdjustmentRow:
    review_id: str
    applied_version: str
    applied_at: datetime
    target_path: str
    effect: str  # "positive" / "negative" / "neutral"


@dataclass(frozen=True)
class LearningReport:
    """All inputs to the renderer."""

    period_label: str  # e.g. "2026-05"
    calibration_rows: list[CalibrationStateRow] = field(default_factory=list)
    drift_signals: list[DriftSignal] = field(default_factory=list)
    findings: list[AggregatedFinding] = field(default_factory=list)
    counterfactual: CounterfactualReport | None = None
    pending_proposals: list[PendingProposalRow] = field(default_factory=list)
    applied_adjustments: list[AppliedAdjustmentRow] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ===========================================================================
# Section renderers
# ===========================================================================


def _header(report: LearningReport) -> str:
    return (
        f"# Learning loop monthly report — {report.period_label}\n\n"
        f"- Generated: `{datetime.now(UTC).isoformat()}`\n"
        f"- Period: **{report.period_label}**\n"
    )


def _calibration_section(rows: list[CalibrationStateRow]) -> str:
    if not rows:
        return "## Calibration state\n\n_no completed predictions in period_\n"
    body = [
        "## Calibration state\n",
        "| window | n | accuracy | avg_decision_correct |",
        "|---|---:|---:|---:|",
    ]
    for r in rows:
        body.append(
            f"| {r.window} | {r.n_samples} | {r.accuracy:.2%} | {r.avg_decision_correct:.3f} |"
        )
    return "\n".join(body) + "\n"


def _drift_section(signals: list[DriftSignal]) -> str:
    if not signals:
        return "## Drift detector\n\n_no signals fired_\n"
    body = [
        "## Drift detector\n",
        "| type | severity | slice | metric | threshold | n |",
        "|---|---|---|---:|---:|---:|",
    ]
    for s in signals:
        slice_repr = ", ".join(f"{k}={v}" for k, v in s.affected_dimensions.items())
        body.append(
            f"| {s.signal_type.value} | {s.severity.value} | "
            f"{slice_repr or '—'} | {s.metric_value:.3f} | "
            f"{s.threshold:.3f} | {s.sample_count} |"
        )
    return "\n".join(body) + "\n"


def _findings_section(findings: list[AggregatedFinding]) -> str:
    if not findings:
        return "## Attribution findings\n\n_no systemic patterns_\n"
    body = [
        "## Attribution findings\n",
        "| slice | primary | occurrences | share | severity |",
        "|---|---|---:|---:|:---:|",
    ]
    for f in findings:
        body.append(
            f"| {f.slice_dimension}={f.slice_value} | "
            f"{f.primary_attribution} | {f.occurrences} | "
            f"{f.share:.0%} | {f.severity} |"
        )
    return "\n".join(body) + "\n"


def _counterfactual_section(cf: CounterfactualReport | None) -> str:
    if cf is None:
        return ""
    body = [
        "## Counterfactual analysis\n",
        f"- If Bear followed: {cf.if_bear.bear_advantage:.0%} of "
        f"{cf.if_bear.n_bull_won_bad} bull-bad outcomes would have been "
        "avoided.\n",
        f"- If single best model: hit-rate "
        f"{cf.if_single_model.best_single_hit_rate:.0%} "
        f"(ensemble = {cf.if_single_model.ensemble_hit_rate:.0%}, "
        f"advantage {cf.if_single_model.ensemble_advantage:+.1%})\n",
        f"- Summary: **{cf.summary}**\n",
    ]
    return "\n".join(body) + "\n"


def _pending_section(rows: list[PendingProposalRow]) -> str:
    if not rows:
        return "## Pending proposals\n\n_none — reviewer queue empty_\n"
    body = [
        "## Pending proposals\n",
        "| review_id | snapshot | proposals | primary | created |",
        "|---|---|---:|---|---|",
    ]
    for r in rows:
        body.append(
            f"| `{r.review_id[:8]}...` | `{r.snapshot_id[:8]}...` | "
            f"{r.proposal_count} | {r.primary_attribution or '—'} | "
            f"{r.created_at.date().isoformat()} |"
        )
    return "\n".join(body) + "\n"


def _applied_section(rows: list[AppliedAdjustmentRow]) -> str:
    if not rows:
        return "## Applied adjustments\n\n_none applied in this period_\n"
    body = [
        "## Applied adjustments\n",
        "| review | target | version | applied_at | effect |",
        "|---|---|---|---|:---:|",
    ]
    for r in rows:
        body.append(
            f"| `{r.review_id[:8]}...` | {r.target_path} | "
            f"{r.applied_version} | {r.applied_at.date().isoformat()} | "
            f"{r.effect} |"
        )
    return "\n".join(body) + "\n"


# ===========================================================================
# Top-level renderer
# ===========================================================================


def render_markdown(report: LearningReport) -> str:
    sections = [
        _header(report),
        _calibration_section(report.calibration_rows),
        _drift_section(report.drift_signals),
        _findings_section(report.findings),
        _counterfactual_section(report.counterfactual),
        _pending_section(report.pending_proposals),
        _applied_section(report.applied_adjustments),
    ]
    if report.notes:
        notes_md = "\n".join(f"- {n}" for n in report.notes)
        sections.append(f"## Notes\n\n{notes_md}\n")
    return "\n".join(filter(None, sections))


def write_report(
    report: LearningReport,
    *,
    out_dir: Path = DEFAULT_REPORT_DIR,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{report.period_label}_learning_report.md"
    content = render_markdown(report)
    out_path.write_text(content, encoding="utf-8")
    logger.info(
        "learning_report_written",
        period=report.period_label,
        path=str(out_path),
    )
    return out_path


def period_label_for(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _placeholder_to_satisfy_type_checker(_: Any) -> Any:
    """Mypy: avoid 'Any' being unused."""
    return _


__all__ = (
    "DEFAULT_REPORT_DIR",
    "AppliedAdjustmentRow",
    "CalibrationStateRow",
    "LearningReport",
    "PendingProposalRow",
    "period_label_for",
    "render_markdown",
    "write_report",
)
