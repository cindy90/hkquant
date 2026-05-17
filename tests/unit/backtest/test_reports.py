"""reports.py tests — Phase 8c per ADR 0013.

DONE-conditions covered:
- Markdown report contains run header, metrics tables, baseline comparison,
  case study, and (if provided) calibration outcome.
- Report writer creates the directory tree and writes a deterministic
  filename ``{date}_{run_id}.md``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from pathlib import Path

from hk_ipo_agent.backtest.metrics import (
    MetricsReport,
    SliceMetrics,
)
from hk_ipo_agent.backtest.reports import render_markdown, write_report
from hk_ipo_agent.backtest.runner import BacktestRun, BacktestSample
from hk_ipo_agent.common.enums import ListingType, RegulatoryRegime

# ===========================================================================
# Fixtures
# ===========================================================================


def _make_sample(score: float, ret_60d: float) -> BacktestSample:
    return BacktestSample(
        ipo_id=uuid.uuid4(),
        stock_code="0001.HK",
        listing_type=ListingType.MAINBOARD_TECH,
        pricing_date=date(2024, 6, 14),
        as_of_date=date(2024, 6, 13),
        decision_score=score,
        realized_returns={"5d": ret_60d * 0.5, "60d": ret_60d},
        regime_score=0.1,
        regulatory_regime=RegulatoryRegime.PRE_20250804,
        notes=(),
    )


def _make_run() -> BacktestRun:
    samples = [_make_sample(float(i), float(i) * 0.05) for i in range(5)]
    metrics_main = MetricsReport(
        label="main_board",
        n_total=5,
        horizons={
            "5d": SliceMetrics(
                horizon="5d",
                n=5,
                ic=0.12,
                ls_spread=0.03,
                ls_t_stat=0.5,
            ),
            "60d": SliceMetrics(
                horizon="60d",
                n=5,
                ic=0.18,
                ls_spread=0.05,
                ls_t_stat=1.1,
            ),
        },
    )
    metrics_regime = MetricsReport(
        label="regime_pass",
        n_total=5,
        horizons={
            "60d": SliceMetrics(
                horizon="60d",
                n=5,
                ic=0.20,
                ls_spread=0.06,
                ls_t_stat=1.3,
            ),
        },
    )
    return BacktestRun(
        run_id=uuid.uuid4(),
        started_at=datetime(2026, 5, 17, 10, 0, 0, tzinfo=UTC),
        finished_at=datetime(2026, 5, 17, 10, 5, 0, tzinfo=UTC),
        samples=tuple(samples),
        metrics_by_label={
            "main_board": metrics_main,
            "regime_pass": metrics_regime,
        },
        config_snapshot={"scorer": "V8LiteScorer", "horizons": ["5d", "60d"]},
    )


# ===========================================================================
# render_markdown
# ===========================================================================


def test_render_markdown_contains_header() -> None:
    run = _make_run()
    md = render_markdown(run)
    assert "# Backtest Run" in md
    assert str(run.run_id) in md
    assert "Samples: **5** total" in md


def test_render_markdown_contains_metrics_tables() -> None:
    md = render_markdown(_make_run())
    assert "## Metrics by slice × horizon" in md
    assert "### main_board" in md
    assert "### regime_pass" in md
    assert "+0.1200" in md or "0.1200" in md  # main_board 5d IC


def test_render_markdown_contains_baseline_comparison() -> None:
    md = render_markdown(_make_run())
    assert "## NACS v8 baseline comparison" in md
    assert "IC Δ" in md


def test_render_markdown_contains_case_study() -> None:
    md = render_markdown(_make_run(), case_study_horizon="60d", case_study_k=2)
    assert "## Case study" in md
    assert "Top decile" in md
    assert "Bottom decile" in md


def test_render_markdown_no_calibration_section_when_absent() -> None:
    md = render_markdown(_make_run(), calibration=None)
    assert "## Calibration outcome" not in md


# ===========================================================================
# write_report
# ===========================================================================


def test_write_report_creates_file(tmp_path: Path) -> None:
    run = _make_run()
    out_path = write_report(run, out_dir=tmp_path / "reports")
    assert out_path.exists()
    assert out_path.suffix == ".md"
    # Filename: {date}_{run_id}.md
    assert str(run.run_id) in out_path.name
    assert out_path.name.startswith("2026-05-17_")
    content = out_path.read_text(encoding="utf-8")
    assert "# Backtest Run" in content


def test_write_report_creates_missing_directories(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "c"
    assert not nested.exists()
    write_report(_make_run(), out_dir=nested)
    assert nested.exists()


def test_write_report_with_calibration(tmp_path: Path) -> None:
    from hk_ipo_agent.backtest.calibration import (
        CalibrationResult,
        SliceCalibration,
    )

    run = _make_run()
    slice_cal = SliceCalibration(
        listing_type=ListingType.MAINBOARD_TECH,
        n_samples=25,
        chosen_weights={"dcf": 0.4, "comparable": 0.6},
        baseline_weights={"dcf": 0.5, "comparable": 0.5},
        chosen_metrics=run.metrics_by_label["main_board"],
        monotonicity_passed=True,
        monotonicity_notes=(),
        objective_value=0.15,
        reason="test",
    )
    cal = CalibrationResult(
        per_listing_type={ListingType.MAINBOARD_TECH: slice_cal},
        candidate_weights_yaml={"MB-TECH": {"dcf": 0.4, "comparable": 0.6}},
        baseline_weights_yaml={"MB-TECH": {"dcf": 0.5, "comparable": 0.5}},
        notes=(),
    )
    out_path = write_report(run, calibration=cal, out_dir=tmp_path / "reports")
    content = out_path.read_text(encoding="utf-8")
    assert "## Calibration outcome" in content
    assert "MB-TECH" in content
    assert "+0.4000" in content or "0.400" in content or "0.4 " in content
