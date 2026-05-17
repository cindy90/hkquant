"""End-to-end backtest smoke — Phase 8c per ADR 0013.

Mini-run with 5 synthetic IPOs through:
  inputs → run_walk_forward → calibrate → render_markdown → write_report

Validates the wiring; the heavy PG-backed 50+ sample run lives in
``scripts/run_backtest.py`` and is gated behind ``pg_required`` markers.

This integration test deliberately uses an in-memory SQLite engine via
async_sessionmaker so it runs without docker.
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from hk_ipo_agent.backtest.calibration import calibrate
from hk_ipo_agent.backtest.reports import write_report
from hk_ipo_agent.backtest.runner import (
    BacktestInput,
    V8LiteScorer,
    run_walk_forward,
)
from hk_ipo_agent.common.enums import ListingType


@pytest_asyncio.fixture
async def sqlite_sf():
    """In-memory SQLite engine — provider only needs sessionmaker for IPC."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=NullPool)
    sf = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    yield sf
    await engine.dispose()


def _mini_inputs() -> list[BacktestInput]:
    """5 synthetic IPOs with monotone score → realized correlation."""
    return [
        BacktestInput(
            ipo_id=uuid.uuid4(),
            pricing_date=date(2024, 6, 14),
            stock_code=f"{i:04d}.HK",
            listing_type=(
                ListingType.MAINBOARD_TECH
                if i % 2 == 0
                else ListingType.CH18C_COMMERCIALIZED
            ),
            realized_returns={
                "5d": 0.02 * (i + 1),
                "30d": 0.05 * (i + 1),
                "60d": 0.07 * (i + 1),
                "180d": 0.10 * (i + 1),
            },
            cornerstone_count=i,
        )
        for i in range(5)
    ]


@pytest.mark.asyncio
async def test_backtest_end_to_end_smoke(sqlite_sf, tmp_path: Path) -> None:
    inputs = _mini_inputs()

    # Step 1: walk-forward
    run = await run_walk_forward(
        inputs, scorer=V8LiteScorer(), session_factory=sqlite_sf,
    )
    assert run.n_total == 5
    assert run.metrics_by_label["main_board"].n_total > 0

    # Step 2: calibration (with synthetic weights for the listing_types
    # that appear in the input)
    current_weights = {
        "MB-TECH": {"dcf": 0.5, "comparable": 0.5},
        "18C-COMM": {"dcf": 0.4, "comparable": 0.6},
    }
    cal = calibrate(list(run.samples), current_weights=current_weights)
    # 5 samples / listing_type < MIN_SLICE_N=20 → both baselines retained.
    assert all(
        slc.chosen_weights == slc.baseline_weights
        for slc in cal.per_listing_type.values()
    )

    # Step 3: report
    report_path = write_report(run, calibration=cal, out_dir=tmp_path / "reports")
    assert report_path.exists()
    md = report_path.read_text(encoding="utf-8")
    assert "# Backtest Run" in md
    assert "## Metrics by slice × horizon" in md
    assert "## Calibration outcome" in md
    # 60d horizon case study should be present
    assert "## Case study" in md


@pytest.mark.asyncio
async def test_backtest_smoke_skips_future_pricing(sqlite_sf) -> None:
    from datetime import timedelta  # noqa: PLC0415

    inputs = [
        BacktestInput(
            ipo_id=uuid.uuid4(),
            pricing_date=date.today() + timedelta(days=10),  # future
            stock_code="FUTR.HK",
            listing_type=ListingType.MAINBOARD_TECH,
            realized_returns={"5d": 0.05},
            cornerstone_count=0,
        ),
        BacktestInput(
            ipo_id=uuid.uuid4(),
            pricing_date=date(2024, 6, 14),  # past — OK
            stock_code="PAST.HK",
            listing_type=ListingType.MAINBOARD_TECH,
            realized_returns={"5d": 0.05},
            cornerstone_count=0,
        ),
    ]
    run = await run_walk_forward(
        inputs, scorer=V8LiteScorer(), session_factory=sqlite_sf,
    )
    # Only the past-pricing input is scored.
    assert run.n_total == 1
    assert run.samples[0].stock_code == "PAST.HK"
