"""Export NACS v8 ``market_environment_cache`` to a JSON fixture.

Per ADR 0005 §1 + §3 and ADR 0013 §8a: the NACS v8 SQLite has 54
monthly market-environment snapshots (HSI 60d return / vol / valuation
percentile, HK IPO 30d trajectory, southbound flows). Phase 8
``regime_detection.py`` consumes these as the initial training set —
explicitly NOT migrated into a PG table because this is reference data,
not the source of truth.

Re-runnable:
    python scripts/export_market_env_cache.py

Reads:  ``data/nacs_real.db`` (legacy NACS SQLite)
Writes: ``data/fixtures/market_environment_cache.json``

The output is checked into git (small JSON file, deterministic).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
NACS_DB = REPO_ROOT / "data" / "nacs_real.db"
OUT_PATH = REPO_ROOT / "data" / "fixtures" / "market_environment_cache.json"


def export() -> int:
    if not NACS_DB.exists():
        print(f"ERROR: NACS DB not found at {NACS_DB}", file=sys.stderr)
        return 1

    with sqlite3.connect(NACS_DB) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            "SELECT asof_month, hsi_60d_return, hsi_60d_vol_annualized, "
            "       hsi_60d_vol_pct_rank, hsi_valuation_pct, "
            "       hk_ipo_30d_avg_d30, hk_ipo_30d_breakage_rate, "
            "       southbound_30d_net_normalized, sector_60d_vol_annualized, "
            "       source "
            "FROM market_environment_cache "
            "ORDER BY asof_month ASC"
        )
        rows = [dict(r) for r in cur.fetchall()]

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": "nacs_v8 / market_environment_cache (Phase 8a fixture)",
        "row_count": len(rows),
        "schema_version": "1.0",
        "fields": {
            "asof_month": "YYYY-MM-01 (start of month, ISO date)",
            "hsi_60d_return": "trailing 60-day HSI return (decimal)",
            "hsi_60d_vol_annualized": "trailing 60-day HSI volatility, annualised",
            "hsi_60d_vol_pct_rank": "percentile rank of vol over rolling window",
            "hsi_valuation_pct": "HSI P/E percentile (forward-looking proxy)",
            "hk_ipo_30d_avg_d30": "rolling 30-day avg of recently-listed IPO 30d return",
            "hk_ipo_30d_breakage_rate": "% IPOs listed in trailing 30d that broke offer",
            "southbound_30d_net_normalized": "30-day southbound flow (HKD, normalised)",
            "sector_60d_vol_annualized": "sector-weighted vol (mirrors hsi_60d_vol when missing)",
            "source": "data source tag — 'ifind_partial' / 'manual' / etc.",
        },
        "rows": rows,
    }
    OUT_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(f"OK: exported {len(rows)} rows → {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(export())
