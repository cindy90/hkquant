"""Phase 9b performance probe — V8LiteScorer wall-clock SLO check.

Per PROJECT_SPEC.md §13: a complete analysis must finish within 30
minutes. V8LiteScorer is a deliberately simple fixture and should run
in well under 1 second per IPO; this script confirms that + emits a
machine-readable markdown report for the case-study pack.

Usage::

    uv run python scripts/perf_smoke.py
    uv run python scripts/perf_smoke.py --n 50

Reads from PG (Phase 2 + 9a ETL). Writes:
``reports/perf/{date}_v8lite_smoke.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from hk_ipo_agent.backtest.runner import (
    V8LiteScorer,
    load_backtest_inputs_from_pg,
    run_walk_forward,
)
from hk_ipo_agent.common.settings import get_settings

DEFAULT_N: int = 10
SLO_SECONDS_PER_IPO: float = 30 * 60  # PROJECT_SPEC.md §13


async def _probe(n: int) -> dict[str, float | int]:
    engine = create_async_engine(get_settings().database.url, poolclass=NullPool)
    sf = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    try:
        inputs = await load_backtest_inputs_from_pg(sf)
        if not inputs:
            return {"n_total": 0, "n_eligible": 0, "elapsed_total_s": 0.0,
                    "elapsed_per_ipo_s": 0.0, "max_per_ipo_s": 0.0}
        subset = inputs[:n]
        scorer = V8LiteScorer()

        per_ipo: list[float] = []
        for sample in subset:
            t0 = time.perf_counter()
            await run_walk_forward(
                [sample], scorer=scorer, session_factory=sf,
            )
            per_ipo.append(time.perf_counter() - t0)

        return {
            "n_total": len(inputs),
            "n_eligible": len(subset),
            "elapsed_total_s": sum(per_ipo),
            "elapsed_per_ipo_s": statistics.median(per_ipo),
            "max_per_ipo_s": max(per_ipo),
        }
    finally:
        await engine.dispose()


def _write_report(metrics: dict[str, float | int], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    date_part = date.today().isoformat()
    out_path = out_dir / f"{date_part}_v8lite_smoke.md"
    body = (
        f"# V8LiteScorer Performance Probe — {date_part}\n\n"
        f"- Probed at: `{datetime.now(UTC).isoformat()}`\n"
        f"- IPOs eligible in PG: **{metrics['n_total']}**\n"
        f"- IPOs probed: **{metrics['n_eligible']}**\n"
        f"- Median wall clock per IPO: **{metrics['elapsed_per_ipo_s']:.4f} s**\n"
        f"- Worst-case wall clock per IPO: **{metrics['max_per_ipo_s']:.4f} s**\n"
        f"- SLO budget per IPO: **{SLO_SECONDS_PER_IPO:.0f} s** "
        f"(PROJECT_SPEC.md §13)\n"
        f"- SLO headroom: **{(SLO_SECONDS_PER_IPO / max(metrics['max_per_ipo_s'], 1e-6)):.0f}x**\n"
    )
    out_path.write_text(body, encoding="utf-8")
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="V8LiteScorer wall-clock probe (Phase 9b)"
    )
    parser.add_argument("--n", type=int, default=DEFAULT_N)
    parser.add_argument(
        "--report-dir", type=Path, default=Path("reports/perf"),
    )
    args = parser.parse_args(argv)

    metrics = asyncio.run(_probe(args.n))
    if metrics["n_eligible"] == 0:
        print(
            "[perf-smoke] no eligible IPOs in PG; run "
            "scripts/migrate_sqlite_to_pg.py first",
            file=sys.stderr,
        )
        return 2

    out_path = _write_report(metrics, args.report_dir)
    print(f"[perf-smoke] median per-IPO: {metrics['elapsed_per_ipo_s']:.4f}s")
    print(f"[perf-smoke] worst per-IPO: {metrics['max_per_ipo_s']:.4f}s")
    print(f"[perf-smoke] SLO headroom: "
          f"{SLO_SECONDS_PER_IPO / max(float(metrics['max_per_ipo_s']), 1e-6):.0f}x")
    print(f"[perf-smoke] report → {out_path}")

    # SLO assertion (return non-zero exit if smoke breaks SLO).
    if float(metrics["max_per_ipo_s"]) > SLO_SECONDS_PER_IPO:
        print(
            f"[perf-smoke] FAIL: worst case {metrics['max_per_ipo_s']:.2f}s "
            f"exceeds {SLO_SECONDS_PER_IPO:.0f}s SLO",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
