"""
migrate_features_v5.py — ipo_master 加 a_share_short_borrowable 列.

幂等; 按 db_metadata 中的 migration_v5_M16 标志判断.

迁移项:
  M16  ipo_master 加 1 列:
        - a_share_short_borrowable  INTEGER  1=可融券 / 0=不可 / NULL=未知

设计目的: 修 run_v7_backtest.py:296 把 a_share_short_borrowable 写死等于 is_a_h
        的 bug. 不是所有 A+H 都可融券 (科创板/创业板新股、非两融标的等). NULL
        语义 = 数据未知, 回退到旧行为 (is_a_h 即等于可融券, 保留 P2.1 行为)
        以避免没拉到数据时静默回归.

使用:
    python scripts/migrate_features_v5.py [--db data/nacs_real.db] [--dry-run]
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


def migrate_M16_a_share_short_borrowable(conn: sqlite3.Connection) -> dict:
    """ipo_master 加 a_share_short_borrowable INTEGER 列 (NULL=未知)."""
    if _migration_done(conn, "migration_v5_M16"):
        return {"status": "already_done"}

    added = False
    if not _table_has_column(conn, "ipo_master", "a_share_short_borrowable"):
        conn.execute(
            "ALTER TABLE ipo_master ADD COLUMN a_share_short_borrowable INTEGER"
        )
        added = True

    _mark_done(conn, "migration_v5_M16")
    return {"status": "applied", "column_added": added}


STEPS = [
    ("M16 ipo_master.a_share_short_borrowable", "migrate_M16_a_share_short_borrowable"),
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
            "SELECT key FROM db_metadata WHERE key LIKE 'migration_v5_M%'"
        )}
        c.close()
    except sqlite3.Error:
        return True
    return "migration_v5_M16" not in existing


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(_ROOT / "data" / "nacs_real.db"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src = Path(args.db)
    if args.dry_run:
        target = src.with_suffix(".dryrun_v5.db")
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
            backup = src.with_name(f"{src.name}.bak_migrate_v5_{ts}")
            shutil.copy(src, backup)
            print(f"AUTO-BACKUP: {backup}")
        else:
            print("All v5 migrations already done, skipping backup.")
        return run(src)


if __name__ == "__main__":
    raise SystemExit(main())
