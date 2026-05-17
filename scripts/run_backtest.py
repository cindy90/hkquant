"""Phase 8c CLI driver — load PG IPOs, run walk-forward, calibrate, write report.

Per ADR 0013 §8c DONE conditions:
- Loads all historical IPOs from ``ipo_events`` with realized returns
  from ``ipo_postmarket`` (full coverage JSONB or scalar fallback)
- Walks forward with ``V8LiteScorer`` (NACS-v8-compatible, no LLM cost)
- Calibrates weights via constrained grid search vs v8 baseline
- Writes ``reports/backtest/{date}_{run_id}.md`` + emits candidate
  weights YAML to stdout / file

Usage::

    uv run python scripts/run_backtest.py
    uv run python scripts/run_backtest.py --min-date 2024-01-01
    uv run python scripts/run_backtest.py --output /tmp/cand.yaml

The candidate weights YAML is NOT written to ``config/`` directly —
the lifecycle (CLAUDE.md) requires routing through
``learning_loop/version_manager.bump_version()`` + human review.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from hk_ipo_agent.backtest.calibration import (
    calibrate,
    dump_weights_yaml,
    load_current_weights,
)
from hk_ipo_agent.backtest.reports import write_report
from hk_ipo_agent.backtest.runner import (
    DEFAULT_HORIZONS,
    V8LiteScorer,
    load_backtest_inputs_from_pg,
    persist_run_to_pg,
    run_walk_forward,
)
from hk_ipo_agent.common.settings import get_settings


async def _amain(args: argparse.Namespace) -> int:
    engine = create_async_engine(get_settings().database.url, poolclass=NullPool)
    sf = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    try:
        print(f"[backtest] loading inputs from PG (min_date={args.min_date})...")
        inputs = await load_backtest_inputs_from_pg(
            sf,
            min_pricing_date=args.min_date,
            horizons=DEFAULT_HORIZONS,
        )
        print(f"[backtest] loaded {len(inputs)} eligible IPOs")
        if not inputs:
            print(
                "[backtest] no eligible IPOs found — check that ipo_events / "
                "ipo_postmarket are populated (Phase 2 ETL)",
                file=sys.stderr,
            )
            return 2

        scorer = V8LiteScorer()
        current_weights = load_current_weights()
        run = await run_walk_forward(
            inputs,
            scorer=scorer,
            session_factory=sf,
            horizons=DEFAULT_HORIZONS,
            config_snapshot={
                "scorer": "V8LiteScorer",
                "horizons": list(DEFAULT_HORIZONS),
                "weights_baseline_keys": sorted(current_weights.keys()),
            },
        )
        print(
            f"[backtest] walk-forward complete: {run.n_total} samples, "
            f"{run.n_regime_pass} regime-pass"
        )

        cal = calibrate(
            list(run.samples),
            current_weights=current_weights,
            horizons=DEFAULT_HORIZONS,
        )
        print(
            f"[backtest] calibration done — monotonicity passed: "
            f"{cal.passed_all_monotonicity()}"
        )
        for note in cal.notes:
            print(f"  - {note}")

        report_path = write_report(
            run, calibration=cal,
            out_dir=Path(args.report_dir),
        )
        print(f"[backtest] report → {report_path}")

        if args.persist:
            n = await persist_run_to_pg(run, sf)
            print(
                f"[backtest] persisted {n} prediction_snapshots rows for "
                f"run_id={run.run_id} — visible via /api/backtest/runs"
            )

        if args.output is not None:
            yaml_text = dump_weights_yaml(
                cal.candidate_weights_yaml,
                header_comment=(
                    "# Candidate weights from Phase 8c calibration\n"
                    f"# Run: {run.run_id}\n"
                    f"# Samples: {run.n_total}\n"
                    "# DO NOT copy directly into config/; route through "
                    "learning_loop/version_manager.bump_version()"
                ),
            )
            Path(args.output).write_text(yaml_text, encoding="utf-8")
            print(f"[backtest] candidate weights → {args.output}")

        return 0
    finally:
        await engine.dispose()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Phase 8c walk-forward backtest")
    p.add_argument(
        "--min-date",
        type=date.fromisoformat,
        default=None,
        help="Earliest pricing_date to include (ISO format). Default: all.",
    )
    p.add_argument(
        "--report-dir",
        type=Path,
        default=Path("reports/backtest"),
        help="Where to write the markdown report. Default: reports/backtest/",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to write candidate weights YAML.",
    )
    p.add_argument(
        "--persist",
        action="store_true",
        help=(
            "Persist BacktestRun samples to prediction_snapshots so the "
            "/api/backtest/runs endpoint can surface them."
        ),
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
