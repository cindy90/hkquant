"""
migrate_features_v4.py — ipo_master 加 a_share_adv_cny 列 (A+H 对冲分桶数据接入).

幂等; 按 db_metadata 中的 migration_v4_M15 标志判断.

迁移项:
  M15  ipo_master 加 1 列:
        - a_share_adv_cny    REAL    A 股近 60 个交易日日均成交额 (CNY); 用于
                                     PostAdjustments.ah_hedge tier 分档 (high/mid/low).

设计目的: 模型层 (src/nacs_model.py:226 + ah_hedge tier) 早已支持按 ADV 分四档,
        但数据通路缺这一列, 导致所有 A+H deal 都走 fallback × 1.10. 接入此列后,
        ifind 拉数 (THS_BD ths_daily_avg_amt_int_stock) 可以把 A 股流动性写进 DB,
        模型读到非 None 值即激活分桶逻辑.

使用:
    python scripts/migrate_features_v4.py [--db data/nacs_real.db] [--dry-run]
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _table_has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def _migration_done(conn: sqlite3.Connection, key: str) -> bool:
    row = conn.execute(
        "SELECT value FROM db_metadata WHERE key = ?", (key,)
    ).fetchone()
    return row is not None and row[0] == "done"


def _mark_done(conn: sqlite3.Connection, key: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO db_metadata(key, value) VALUES(?, ?)",
        (key, "done"),
    )


# =============================================================================
# M15: ipo_master.a_share_adv_cny
# =============================================================================

def migrate_M15_a_share_adv(conn: sqlite3.Connection) -> dict:
    """ipo_master 加 a_share_adv_cny 列 (NULL = 未拉到, 模型走 fallback)."""
    if _migration_done(conn, "migration_v4_M15"):
        return {"status": "already_done"}

    added = False
    if not _table_has_column(conn, "ipo_master", "a_share_adv_cny"):
        conn.execute("ALTER TABLE ipo_master ADD COLUMN a_share_adv_cny REAL")
        added = True

    _mark_done(conn, "migration_v4_M15")
    return {"status": "applied", "column_added": added}


# =============================================================================
# Driver
# =============================================================================

STEPS = [
    ("M15 ipo_master.a_share_adv_cny", "migrate_M15_a_share_adv"),
]


def run(db_path: Path) -> int:
    if not db_path.exists():
        print(f"ERROR: DB not found: {db_path}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        for name, fn_name in STEPS:
            print(f"\n=== {name} ===")
            res = globals()[fn_name](conn)
            for k, v in res.items():
                print(f"  {k}: {v}")
            conn.commit()
        print("\nALL STEPS COMMITTED")
        return 0
    except Exception as e:
        conn.rollback()
        print(f"\nFAILED: {type(e).__name__}: {e}", file=sys.stderr)
        raise
    finally:
        conn.close()


def _any_pending(db_path: Path) -> bool:
    try:
        c = sqlite3.connect(str(db_path))
        existing = {r[0] for r in c.execute(
            "SELECT key FROM db_metadata WHERE key LIKE 'migration_v4_M%'"
        )}
        c.close()
    except sqlite3.Error:
        return True
    needed = {"migration_v4_M15"}
    return bool(needed - existing)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(_ROOT / "data" / "nacs_real.db"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src = Path(args.db)
    if args.dry_run:
        target = src.with_suffix(".dryrun_v4.db")
        shutil.copy(src, target)
        print(f"[DRY-RUN] working on copy: {target}")
        try:
            return run(target)
        finally:
            if target.exists():
                target.unlink()
    else:
        if _any_pending(src):
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = src.with_name(f"{src.name}.bak_migrate_v4_{ts}")
            shutil.copy(src, backup)
            print(f"AUTO-BACKUP: {backup}")
        else:
            print("All v4 migrations already done, skipping backup.")
        return run(src)


if __name__ == "__main__":
    raise SystemExit(main())
