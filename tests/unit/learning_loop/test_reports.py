"""reports.py tests — Phase 10b per ADR 0015."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from pathlib import Path

from hk_ipo_agent.common.enums import AlertLevel, DriftSignalType
from hk_ipo_agent.common.schemas import DriftSignal
from hk_ipo_agent.learning_loop.attribution_aggregator import AggregatedFinding
from hk_ipo_agent.learning_loop.counterfactual import (
    CounterfactualReport,
    IfBearReport,
    IfSingleModelReport,
)
from hk_ipo_agent.learning_loop.reports import (
    AppliedAdjustmentRow,
    CalibrationStateRow,
    LearningReport,
    PendingProposalRow,
    period_label_for,
    render_markdown,
    write_report,
)


def _calibration_rows() -> list[CalibrationStateRow]:
    return [
        CalibrationStateRow(window="30d", n_samples=20, accuracy=0.85, avg_decision_correct=0.85),
        CalibrationStateRow(window="60d", n_samples=40, accuracy=0.80, avg_decision_correct=0.80),
        CalibrationStateRow(window="90d", n_samples=80, accuracy=0.78, avg_decision_correct=0.78),
    ]


def _drift_signals() -> list[DriftSignal]:
    return [
        DriftSignal(
            detection_time=datetime.now(UTC),
            signal_type=DriftSignalType.ACCURACY_DROP,
            severity=AlertLevel.WARNING,
            affected_dimensions={"regulatory_regime": "pre_new_pricing"},
            metric_value=5.0,
            threshold=4.0,
            sample_count=30,
            evidence="test",
            related_snapshot_ids=[],
        )
    ]


def _findings() -> list[AggregatedFinding]:
    return [
        AggregatedFinding(
            attribution_key="all|valuation_model",
            primary_attribution="valuation_model",
            slice_dimension="all",
            slice_value="all",
            occurrences=8,
            share=0.5,
            related_review_ids=(uuid.uuid4(),),
            related_snapshot_ids=(uuid.uuid4(),),
            severity="warning",
        )
    ]


def _cf() -> CounterfactualReport:
    return CounterfactualReport(
        if_bear=IfBearReport(
            n_total=10,
            n_bull_won=10,
            n_bull_won_bad=5,
            bull_won_bad_rate=0.5,
            n_bear_would_have_avoided=4,
            bear_advantage=0.8,
        ),
        if_single_model=IfSingleModelReport(
            n_samples=10,
            ensemble_hit_rate=0.6,
            model_hit_rates={"dcf": 0.7},
            best_single_model="dcf",
            best_single_hit_rate=0.7,
            ensemble_advantage=-0.1,
        ),
        summary="Bear bias detected",
    )


def _pending() -> list[PendingProposalRow]:
    return [
        PendingProposalRow(
            review_id=str(uuid.uuid4()),
            snapshot_id=str(uuid.uuid4()),
            proposal_count=3,
            primary_attribution="valuation_model",
            created_at=datetime.now(UTC),
        )
    ]


def _applied() -> list[AppliedAdjustmentRow]:
    return [
        AppliedAdjustmentRow(
            review_id=str(uuid.uuid4()),
            applied_version="1.0.1",
            applied_at=datetime.now(UTC),
            target_path="config/synthesizer.yaml",
            effect="positive",
        )
    ]


def _report(**overrides) -> LearningReport:
    defaults = {
        "period_label": "2026-05",
        "calibration_rows": _calibration_rows(),
        "drift_signals": _drift_signals(),
        "findings": _findings(),
        "counterfactual": _cf(),
        "pending_proposals": _pending(),
        "applied_adjustments": _applied(),
    }
    defaults.update(overrides)
    return LearningReport(**defaults)


# ---------------------------------------------------------------------------
# render_markdown
# ---------------------------------------------------------------------------


def test_render_includes_header() -> None:
    md = render_markdown(_report())
    assert "Learning loop monthly report" in md
    assert "2026-05" in md


def test_render_includes_all_sections() -> None:
    md = render_markdown(_report())
    assert "## Calibration state" in md
    assert "## Drift detector" in md
    assert "## Attribution findings" in md
    assert "## Counterfactual analysis" in md
    assert "## Pending proposals" in md
    assert "## Applied adjustments" in md


def test_render_calibration_table_has_rows() -> None:
    md = render_markdown(_report())
    assert "30d" in md
    assert "85.00%" in md or "0.85" in md


def test_render_empty_sections_render_placeholders() -> None:
    md = render_markdown(
        LearningReport(
            period_label="2026-05",
            calibration_rows=[],
            drift_signals=[],
            findings=[],
            counterfactual=None,
            pending_proposals=[],
            applied_adjustments=[],
        )
    )
    assert "no completed predictions" in md
    assert "no signals fired" in md
    assert "no systemic patterns" in md
    assert "reviewer queue empty" in md
    assert "none applied" in md


def test_render_skips_counterfactual_when_none() -> None:
    md = render_markdown(
        LearningReport(
            period_label="2026-05",
            counterfactual=None,
        )
    )
    assert "## Counterfactual analysis" not in md


def test_render_notes_section_when_present() -> None:
    md = render_markdown(_report(notes=["test note 1", "test note 2"]))
    assert "## Notes" in md
    assert "test note 1" in md


# ---------------------------------------------------------------------------
# write_report
# ---------------------------------------------------------------------------


def test_write_report_creates_file(tmp_path: Path) -> None:
    out_path = write_report(_report(), out_dir=tmp_path)
    assert out_path.exists()
    assert "2026-05" in out_path.name
    content = out_path.read_text(encoding="utf-8")
    assert "Learning loop monthly report" in content


def test_write_report_creates_dir(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "c"
    write_report(_report(), out_dir=nested)
    assert nested.exists()


def test_period_label_for_formats_year_month() -> None:
    assert period_label_for(date(2026, 5, 17)) == "2026-05"
    assert period_label_for(date(2025, 12, 1)) == "2025-12"
