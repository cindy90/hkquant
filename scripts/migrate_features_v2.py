"""
migrate_features_v2.py — deal pipeline 与预测落盘的 schema 改造

幂等; 按 db_metadata 中的 migration_v2_M* 标志判断是否已跑过.

迁移项:
  M9   ipo_master 加 status / prospectus_pdf_path / expected_listing_date 列
       + 回填 (现有 384 行: 退市 → 'delisted', 其余 → 'listed')
  M10  panel_snapshots 表 (回测面板的可还原快照)
  M11  nacs_predictions 表 (单 deal 评估结果落盘 + audit trail)
  M12  重建 mv_ipo_full 视图加 status / expected_listing_date 列

使用:
    python scripts/migrate_features_v2.py [--db data/nacs_real.db] [--dry-run]
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


# =============================================================================
# Helpers (与 v1 一致)
# =============================================================================

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
# M9 — ipo_master.status
# =============================================================================

def migrate_M9_ipo_master_status(conn: sqlite3.Connection) -> dict:
    """加 3 列 + 按数据完整度回填 status.

    回填规则:
        is_delisted=1                 → 'delisted'
        listing_date 是过去 + 有 oversub 数据 → 'listed' (默认大多数)
        listing_date 是未来 + oversub 已有  → 'pricing'
        listing_date 是未来 + oversub 全 NULL → 'prospectus'
        其它 (兜底)                    → 'listed'

    SQLite 不支持 ALTER ADD COLUMN with CHECK; 我们靠 Python 层 + ETL 保证.
    """
    if _migration_done(conn, "migration_v2_M9"):
        return {"status": "already_done"}

    if not _table_has_column(conn, "ipo_master", "status"):
        conn.execute(
            "ALTER TABLE ipo_master ADD COLUMN status TEXT NOT NULL DEFAULT 'listed'"
        )
    if not _table_has_column(conn, "ipo_master", "prospectus_pdf_path"):
        conn.execute("ALTER TABLE ipo_master ADD COLUMN prospectus_pdf_path TEXT")
    if not _table_has_column(conn, "ipo_master", "expected_listing_date"):
        conn.execute("ALTER TABLE ipo_master ADD COLUMN expected_listing_date DATE")

    # 回填: 退市
    cur1 = conn.execute(
        "UPDATE ipo_master SET status = 'delisted' "
        "WHERE COALESCE(is_delisted, 0) = 1 AND status != 'delisted'"
    )
    n_delisted = cur1.rowcount

    # 回填: 未来上市但已有定价 (intl_oversub 不空)
    cur2 = conn.execute("""
        UPDATE ipo_master
        SET status = 'pricing'
        WHERE listing_date > date('now')
          AND intl_oversub IS NOT NULL
          AND status NOT IN ('delisted', 'pricing')
    """)
    n_pricing = cur2.rowcount

    # 回填: 未来上市且 oversub 还空 (典型 prospectus 状态)
    cur3 = conn.execute("""
        UPDATE ipo_master
        SET status = 'prospectus'
        WHERE listing_date > date('now')
          AND intl_oversub IS NULL
          AND status NOT IN ('delisted', 'pricing', 'prospectus')
    """)
    n_prospectus = cur3.rowcount

    _mark_done(conn, "migration_v2_M9")
    return {
        "status": "applied",
        "marked_delisted": n_delisted,
        "marked_pricing": n_pricing,
        "marked_prospectus": n_prospectus,
    }


# =============================================================================
# M10 — panel_snapshots
# =============================================================================

PANEL_SNAPSHOTS_DDL = """
CREATE TABLE IF NOT EXISTS panel_snapshots (
    snapshot_id          TEXT PRIMARY KEY,                -- e.g. PANEL_2026-05-09_a3f2
    asof_date            DATE NOT NULL,
    n_ipos_in_universe   INTEGER NOT NULL,                -- panel 成员数量
    -- 当时整体 panel 的派生指标
    market_env_json      TEXT NOT NULL,                   -- MarketEnvironment 8 字段
    regime_score         REAL,                            -- panel 整体 regime
    -- panel 成员清单 (复盘时还原 pe_peer_median 等)
    member_ipo_ids_json  TEXT NOT NULL,                   -- JSON array of ipo_id
    -- 跨章节聚合 (单 case 报告里直接拿用)
    aggregates_json      TEXT,                            -- {"main_board": {"n":269, "median_d30":...}}
    -- 可还原性
    config_version       TEXT,
    config_hash          TEXT,
    config_yaml_snapshot TEXT,                            -- 完整 YAML 嵌入
    code_git_sha         TEXT,
    db_schema_version    TEXT,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notes                TEXT
);
CREATE INDEX IF NOT EXISTS idx_panel_asof ON panel_snapshots(asof_date);
"""


def migrate_M10_panel_snapshots(conn: sqlite3.Connection) -> dict:
    if _migration_done(conn, "migration_v2_M10"):
        return {"status": "already_done"}
    conn.executescript(PANEL_SNAPSHOTS_DDL)
    _mark_done(conn, "migration_v2_M10")
    return {"status": "applied"}


# =============================================================================
# M11 — nacs_predictions
# =============================================================================

NACS_PREDICTIONS_DDL = """
CREATE TABLE IF NOT EXISTS nacs_predictions (
    case_id              TEXT PRIMARY KEY,                -- e.g. PRED_1187.HK_2025-08-15_mid_a3f2
    stock_code           TEXT NOT NULL,                   -- 拟上市/已上市代码 (核心查询键)
    asof_date            DATE NOT NULL,                   -- 分析切点
    panel_snapshot_id    TEXT NOT NULL,                   -- → panel_snapshots
    -- 评估时 deal 的 ipo_master.status (复盘时知道是哪期)
    deal_status_at_analysis TEXT,
    -- 多场景定价: low / mid / high / final
    price_scenario       TEXT,
    offer_price_used     REAL,
    -- 模型输出
    nacs_raw             REAL,
    nacs_adjusted        REAL,
    Q_company            REAL,
    Q_ecosystem          REAL,
    R_lockup             REAL,
    decision             TEXT,
    position_pct         REAL,
    cluster_count        INTEGER,
    -- 完整诊断 (NACSResult.to_dict 的 components 部分)
    layer1_components_json TEXT,
    layer2_components_json TEXT,
    layer3_components_json TEXT,
    adjustments_json     TEXT,
    warnings_json        TEXT,
    -- 输入快照: 锁定"分析当时知道什么" (IPOOffering 完整 dict)
    inputs_json          TEXT NOT NULL,
    -- 同伴比对
    nacs_pct_in_panel    REAL,                            -- 0..1
    nacs_pct_in_chapter  REAL,
    similar_cases_json   TEXT,                            -- 最相似 5 只 listed IPO
    -- 元数据
    run_at               TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notes                TEXT,
    FOREIGN KEY (panel_snapshot_id) REFERENCES panel_snapshots(snapshot_id)
);
CREATE INDEX IF NOT EXISTS idx_pred_code ON nacs_predictions(stock_code, asof_date);
CREATE INDEX IF NOT EXISTS idx_pred_panel ON nacs_predictions(panel_snapshot_id);
CREATE INDEX IF NOT EXISTS idx_pred_decision ON nacs_predictions(decision);
"""


def migrate_M11_nacs_predictions(conn: sqlite3.Connection) -> dict:
    if _migration_done(conn, "migration_v2_M11"):
        return {"status": "already_done"}
    conn.executescript(NACS_PREDICTIONS_DDL)
    _mark_done(conn, "migration_v2_M11")
    return {"status": "applied"}


# =============================================================================
# M12 — 重建 mv_ipo_full 暴露 status / expected_listing_date
# =============================================================================

MV_IPO_FULL_V2_DDL = """
DROP VIEW IF EXISTS mv_ipo_full;
CREATE VIEW mv_ipo_full AS
SELECT
    m.ipo_id, m.stock_code, m.company_name_zh,
    m.status, m.listing_date, m.expected_listing_date, m.pricing_date,
    m.listing_chapter, m.gics_l2,
    m.offer_price_hkd, m.offer_price_low, m.offer_price_high,
    m.offering_size_hkd, m.gross_proceeds_excl_greenshoe, m.total_offer_shares,
    m.cornerstone_coverage, m.cornerstone_count,
    m.lockup_months,
    m.pe_at_offer, m.pe_peer_median,
    m.pre_ipo_shares, m.post_ipo_shares, m.overhang_ratio,
    m.is_delisted, m.delisting_date, m.is_acquired,
    m.data_quality_score,
    r.return_d1_close, r.return_d30, r.return_m3, r.return_m6, r.return_m12,
    r.return_unlock_d30, r.return_unlock_d90,
    r.max_drawdown_m6, r.avg_daily_volume_hkd,
    r.is_d30_due, r.is_m6_due, r.is_m12_due, r.is_unlock_due,
    (SELECT COUNT(*) FROM ipo_cornerstone_link WHERE ipo_id = m.ipo_id) AS n_cs,
    (SELECT SUM(ticket_size_hkd) FROM ipo_cornerstone_link
        WHERE ipo_id = m.ipo_id) AS cs_total_hkd,
    (SELECT GROUP_CONCAT(DISTINCT currency) FROM ipo_cornerstone_link
        WHERE ipo_id = m.ipo_id) AS cs_currencies
FROM ipo_master m
LEFT JOIN ipo_returns r ON r.ipo_id = m.ipo_id;
"""


def migrate_M12_mv_view_v2(conn: sqlite3.Connection) -> dict:
    if _migration_done(conn, "migration_v2_M12"):
        return {"status": "already_done"}
    conn.executescript(MV_IPO_FULL_V2_DDL)
    _mark_done(conn, "migration_v2_M12")
    return {"status": "applied"}


# =============================================================================
# Driver
# =============================================================================

STEPS = [
    ("M9  ipo_master.status",       "migrate_M9_ipo_master_status"),
    ("M10 panel_snapshots",         "migrate_M10_panel_snapshots"),
    ("M11 nacs_predictions",        "migrate_M11_nacs_predictions"),
    ("M12 mv_ipo_full v2",          "migrate_M12_mv_view_v2"),
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
            "SELECT key FROM db_metadata WHERE key LIKE 'migration_v2_M%'"
        )}
        c.close()
    except sqlite3.Error:
        return True
    needed = {f"migration_v2_M{i}" for i in (9, 10, 11, 12)}
    return bool(needed - existing)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(_ROOT / "data" / "nacs_real.db"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src = Path(args.db)
    if args.dry_run:
        target = src.with_suffix(".dryrun_v2.db")
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
            backup = src.with_name(f"{src.name}.bak_migrate_v2_{ts}")
            shutil.copy(src, backup)
            print(f"AUTO-BACKUP: {backup}")
        else:
            print("All v2 migrations already done, skipping backup.")
        return run(src)


if __name__ == "__main__":
    raise SystemExit(main())
