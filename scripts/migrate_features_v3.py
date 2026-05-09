"""
migrate_features_v3.py — nacs_predictions 加 theme/premium 字段, 完整 audit.

幂等; 按 db_metadata 中的 migration_v3_M* 标志判断.

迁移项:
  M13  nacs_predictions 加 5 列:
        - theme_id                TEXT     (classify 出的主题; None=未识别)
        - theme_confidence        TEXT     ('high'/'medium'/'low'/'none')
        - theme_heat_score        INTEGER  (0-100, 评估时点的当日值)
        - ai_revenue_pct_used     REAL     (用于 premium_estimate 的 pct)
        - themes_provenance_json  TEXT     (5 个 themes/ 文件 + classifier 的 audit)

  M14  panel_snapshots 加 1 列:
        - themes_provenance_json  TEXT     (回测时也快照 themes 数据来源)

设计目的: 后续复盘 "这只 deal 当时的 theme heat 是多少 / premium 用的什么 r²"
        全部从 DB 读, 不依赖 themes/heat_today.json 的当时版本是否还能 git 找到.

使用:
    python scripts/migrate_features_v3.py [--db data/nacs_real.db] [--dry-run]
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
# M13: nacs_predictions theme columns
# =============================================================================

def migrate_M13_predictions_themes(conn: sqlite3.Connection) -> dict:
    """加 5 列到 nacs_predictions."""
    if _migration_done(conn, "migration_v3_M13"):
        return {"status": "already_done"}

    new_cols = [
        ("theme_id", "TEXT"),
        ("theme_confidence", "TEXT"),
        ("theme_heat_score", "INTEGER"),
        ("ai_revenue_pct_used", "REAL"),
        ("themes_provenance_json", "TEXT"),
    ]
    added = []
    for col, ddl in new_cols:
        if not _table_has_column(conn, "nacs_predictions", col):
            conn.execute(f"ALTER TABLE nacs_predictions ADD COLUMN {col} {ddl}")
            added.append(col)
    _mark_done(conn, "migration_v3_M13")
    return {"status": "applied", "columns_added": added}


# =============================================================================
# M14: panel_snapshots theme provenance column
# =============================================================================

def migrate_M14_panel_themes(conn: sqlite3.Connection) -> dict:
    """加 1 列到 panel_snapshots."""
    if _migration_done(conn, "migration_v3_M14"):
        return {"status": "already_done"}

    if not _table_has_column(conn, "panel_snapshots", "themes_provenance_json"):
        conn.execute(
            "ALTER TABLE panel_snapshots ADD COLUMN themes_provenance_json TEXT"
        )
    _mark_done(conn, "migration_v3_M14")
    return {"status": "applied"}


# =============================================================================
# Driver
# =============================================================================

STEPS = [
    ("M13 nacs_predictions theme cols", "migrate_M13_predictions_themes"),
    ("M14 panel_snapshots theme provenance", "migrate_M14_panel_themes"),
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
        print("\nALL STEPS COMMITTED ✓")
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
            "SELECT key FROM db_metadata WHERE key LIKE 'migration_v3_M%'"
        )}
        c.close()
    except sqlite3.Error:
        return True
    needed = {f"migration_v3_M{i}" for i in (13, 14)}
    return bool(needed - existing)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(_ROOT / "data" / "nacs_real.db"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src = Path(args.db)
    if args.dry_run:
        target = src.with_suffix(".dryrun_v3.db")
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
            backup = src.with_name(f"{src.name}.bak_migrate_v3_{ts}")
            shutil.copy(src, backup)
            print(f"AUTO-BACKUP: {backup}")
        else:
            print("All v3 migrations already done, skipping backup.")
        return run(src)


if __name__ == "__main__":
    raise SystemExit(main())
