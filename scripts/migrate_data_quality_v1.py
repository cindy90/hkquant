"""
migrate_data_quality_v1.py — 数据质量与可查询性一次性迁移

幂等; 按 db_metadata 中的 migration_v1 标志判断是否已跑过.

迁移项 (按 schema 改动顺序):
  M1  补全 schema 缺失的 ipo_financials / ipo_concepts / ipo_industries 表定义
  M2  增加 5 个高频索引 (ipo_cornerstone_link / ipo_master / ipo_financials)
  M3  ipo_master 加列 gross_proceeds_excl_greenshoe + 回填 (price × shares)
  M4  ipo_returns 加列 is_d30_due / is_m6_due / is_m12_due / is_unlock_due + 回填
  M5  ipo_cornerstone_link 加列 currency / ticket_size_native + HKD 归一
  M6  重建 ipo_cornerstone_link 加 CHECK (affiliation_flag IN (0,1,2))
  M7  share_capital 缺失 13 行从 actual_issued_shares 反推
  M8  创建只读视图 mv_ipo_full

使用:
    python scripts/migrate_data_quality_v1.py [--db data/nacs_real.db] [--dry-run]
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))


# =============================================================================
# 配置: 汇率 (一年常数, 后续可改成按月查 cache)
# 注: HKD = USD × FX_USD_HKD = CNY × FX_CNY_HKD
# =============================================================================
FX_USD_HKD = 7.80
FX_CNY_HKD = 1.10


# =============================================================================
# Schema patches
# =============================================================================

# M1: 补全 schema 缺失的派生表 (CREATE IF NOT EXISTS, 已在则跳过)
SCHEMA_PATCHES_V1 = """
-- ipo_financials: 由 fix_p1_* 脚本创建, 现在补进 schema
CREATE TABLE IF NOT EXISTS ipo_financials (
    stock_code      TEXT NOT NULL,
    report_year     INTEGER NOT NULL,
    revenue_cny     REAL,
    gross_margin    REAL,
    net_margin      REAL,
    roe             REAL,
    PRIMARY KEY (stock_code, report_year)
);

-- ipo_concepts: 概念板块成分
CREATE TABLE IF NOT EXISTS ipo_concepts (
    ipo_id          TEXT NOT NULL,
    stock_code      TEXT NOT NULL,
    concept_id      TEXT NOT NULL,
    concept_name    TEXT,
    data_date       TEXT
);
CREATE INDEX IF NOT EXISTS idx_ipo_concepts_stock ON ipo_concepts(stock_code);
CREATE INDEX IF NOT EXISTS idx_ipo_concepts_concept ON ipo_concepts(concept_id);

-- ipo_industries: 行业分类 (恒生/申万/同花顺多源)
CREATE TABLE IF NOT EXISTS ipo_industries (
    ipo_id          TEXT NOT NULL,
    stock_code      TEXT NOT NULL,
    source          TEXT NOT NULL,
    l1_name         TEXT, l2_name TEXT, l3_name TEXT, l4_name TEXT,
    leaf_bid        TEXT,
    leaf_level      INTEGER,
    data_date       TEXT
);
CREATE INDEX IF NOT EXISTS idx_ipo_industries_stock ON ipo_industries(stock_code);
CREATE INDEX IF NOT EXISTS idx_ipo_industries_leaf ON ipo_industries(leaf_bid);
CREATE INDEX IF NOT EXISTS idx_ipo_industries_l1 ON ipo_industries(l1_name);
"""

# M2: 5 个高频访问索引
INDEX_PATCHES = [
    ("idx_link_ipo",       "ipo_cornerstone_link(ipo_id)"),
    ("idx_link_cs",        "ipo_cornerstone_link(cornerstone_id)"),
    ("idx_link_unique",    "ipo_cornerstone_link(ipo_id, cornerstone_id)", True),  # UNIQUE
    ("idx_ipo_stock_code", "ipo_master(stock_code)"),
    ("idx_fin_code_year",  "ipo_financials(stock_code, report_year)", True),       # UNIQUE
]


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
# Migration steps
# =============================================================================

def migrate_M0_dedupe_links(conn: sqlite3.Connection) -> dict:
    """合并 (ipo_id, cornerstone_id) 重复行 (sum ticket / shares).

    起因: 同一只 IPO 同一基石可能在 raw CSV 里以多个不同文本名出现
    (e.g. "富国基金管理有限公司及富国资产管理(香港)有限公司" vs "富国资产管理(香港)有限公司"),
    经 make_cornerstone_id 归一后得到同一 cornerstone_id, 但是两条都被 INSERT.
    NACS 模型按 cornerstone_id 聚合, 所以合并 = 等价语义.
    """
    if _migration_done(conn, "migration_v1_M0"):
        return {"status": "already_done"}

    dup_groups = conn.execute("""
        SELECT ipo_id, cornerstone_id, COUNT(*) as n
        FROM ipo_cornerstone_link
        GROUP BY ipo_id, cornerstone_id
        HAVING COUNT(*) > 1
    """).fetchall()
    if not dup_groups:
        _mark_done(conn, "migration_v1_M0")
        return {"status": "applied", "groups_merged": 0}

    n_merged = 0
    n_rows_deleted = 0
    for ipo_id, cs_id, _ in dup_groups:
        rows = conn.execute(
            "SELECT * FROM ipo_cornerstone_link "
            "WHERE ipo_id = ? AND cornerstone_id = ? ORDER BY link_id",
            (ipo_id, cs_id),
        ).fetchall()
        if len(rows) < 2:
            continue
        keeper = rows[0]
        # 聚合数值字段
        total_hkd = sum((r["ticket_size_hkd"] or 0) for r in rows)
        total_shares = sum((r["allocation_shares"] or 0) for r in rows)
        total_pct = sum((r["subscribe_pct"] or 0) for r in rows)
        # 文本字段: 以 keeper 为准, 其它人备注到 affiliation_reason
        all_names = " | ".join(
            sorted({r["cornerstone_name"] for r in rows if r["cornerstone_name"]})
        )
        merge_note = f"merged from {len(rows)} duplicate rows: {all_names}"

        # 更新 keeper
        conn.execute("""
            UPDATE ipo_cornerstone_link
            SET ticket_size_hkd = ?,
                allocation_shares = ?,
                subscribe_pct = ?,
                cornerstone_name = ?,
                affiliation_reason = COALESCE(affiliation_reason || ' | ', '') || ?
            WHERE link_id = ?
        """, (total_hkd, total_shares, total_pct, all_names, merge_note,
              keeper["link_id"]))
        # 删 dup
        for r in rows[1:]:
            conn.execute(
                "DELETE FROM ipo_cornerstone_link WHERE link_id = ?", (r["link_id"],)
            )
            n_rows_deleted += 1
        n_merged += 1

    _mark_done(conn, "migration_v1_M0")
    return {"status": "applied",
            "groups_merged": n_merged,
            "rows_deleted": n_rows_deleted}


def migrate_M1_schema_patches(conn: sqlite3.Connection) -> dict:
    """补全 schema 中缺失的派生表."""
    if _migration_done(conn, "migration_v1_M1"):
        return {"status": "already_done"}
    conn.executescript(SCHEMA_PATCHES_V1)
    _mark_done(conn, "migration_v1_M1")
    return {"status": "applied"}


def migrate_M2_indexes(conn: sqlite3.Connection) -> dict:
    """添加 5 个高频访问的索引."""
    if _migration_done(conn, "migration_v1_M2"):
        return {"status": "already_done"}
    created = []
    for spec in INDEX_PATCHES:
        name, target = spec[0], spec[1]
        is_unique = len(spec) > 2 and spec[2]
        ddl = (f"CREATE {'UNIQUE ' if is_unique else ''}INDEX IF NOT EXISTS "
               f"{name} ON {target}")
        conn.execute(ddl)
        created.append(name)
    _mark_done(conn, "migration_v1_M2")
    return {"status": "applied", "indexes": created}


def migrate_M3_gross_proceeds(conn: sqlite3.Connection) -> dict:
    """ipo_master 加列 gross_proceeds_excl_greenshoe + 回填.

    定义: gross_proceeds_excl_greenshoe = offer_price_hkd × total_offer_shares
    (raw CSV 的 offering_size_hkd 含绿鞋, 这两值通常差 ~15%; 我们都保留).
    """
    if _migration_done(conn, "migration_v1_M3"):
        return {"status": "already_done"}

    if not _table_has_column(conn, "ipo_master", "gross_proceeds_excl_greenshoe"):
        conn.execute(
            "ALTER TABLE ipo_master ADD COLUMN gross_proceeds_excl_greenshoe REAL"
        )
    if not _table_has_column(conn, "ipo_master", "total_offer_shares"):
        conn.execute("ALTER TABLE ipo_master ADD COLUMN total_offer_shares REAL")

    # 回填: 从 raw CSV 读 total_offer_shares
    raw_csv = _ROOT / "data" / "raw" / "ifind" / "ifind_ipo_info.csv"
    n_filled_shares = 0
    n_filled_proceeds = 0
    if raw_csv.exists():
        from data_sources.ifind.field_mappings import (
            P05310_IPO_INFO, parse_float,
        )
        with raw_csv.open(encoding="utf-8-sig") as f:
            for raw_row in csv.DictReader(f):
                row = {P05310_IPO_INFO.get(k, k): v for k, v in raw_row.items()}
                code = row.get("stock_code")
                shares = parse_float(row.get("total_offer_shares"))
                if not code or shares is None:
                    continue
                cur = conn.execute(
                    "UPDATE ipo_master SET total_offer_shares = ? "
                    "WHERE stock_code = ? AND total_offer_shares IS NULL",
                    (shares, code),
                )
                n_filled_shares += cur.rowcount

    # 派生 gross_proceeds_excl_greenshoe
    cur = conn.execute("""
        UPDATE ipo_master
        SET gross_proceeds_excl_greenshoe = offer_price_hkd * total_offer_shares
        WHERE offer_price_hkd IS NOT NULL
          AND total_offer_shares IS NOT NULL
          AND gross_proceeds_excl_greenshoe IS NULL
    """)
    n_filled_proceeds = cur.rowcount

    _mark_done(conn, "migration_v1_M3")
    return {
        "status": "applied",
        "shares_filled": n_filled_shares,
        "proceeds_filled": n_filled_proceeds,
    }


def migrate_M4_due_flags(conn: sqlite3.Connection) -> dict:
    """ipo_returns 加 is_*_due 列, 区分"业绩未到期"和"业绩缺失".

    is_d30_due  = listing_date + 30 天 <= today
    is_m6_due   = listing_date + 180 天 <= today
    is_m12_due  = listing_date + 365 天 <= today
    is_unlock_due = listing_date + lockup×30.5 + 30 天 <= today
    """
    if _migration_done(conn, "migration_v1_M4"):
        return {"status": "already_done"}

    new_cols = ["is_d30_due", "is_m6_due", "is_m12_due", "is_unlock_due"]
    for c in new_cols:
        if not _table_has_column(conn, "ipo_returns", c):
            conn.execute(f"ALTER TABLE ipo_returns ADD COLUMN {c} INTEGER DEFAULT 0")

    # 回填: 用 today = max(listing_date) 当作"现在"的近似
    # (这样跑 migration 时不依赖系统时钟, 结果可重复)
    today_row = conn.execute(
        "SELECT MAX(listing_date) FROM ipo_master"
    ).fetchone()
    today_str = today_row[0] if today_row and today_row[0] else "2026-05-09"
    today_d = date.fromisoformat(today_str[:10])

    rows = conn.execute("""
        SELECT m.ipo_id, m.listing_date, COALESCE(m.lockup_months, 6) AS lockup
        FROM ipo_master m
        JOIN ipo_returns r ON r.ipo_id = m.ipo_id
    """).fetchall()
    n_updated = 0
    for r in rows:
        ld = date.fromisoformat(str(r[1])[:10])
        lockup_months = int(r[2] or 6)
        unlock_d = ld + timedelta(days=int(lockup_months * 30.5))
        flags = {
            "is_d30_due":   1 if ld + timedelta(days=30) <= today_d else 0,
            "is_m6_due":    1 if ld + timedelta(days=180) <= today_d else 0,
            "is_m12_due":   1 if ld + timedelta(days=365) <= today_d else 0,
            "is_unlock_due": 1 if unlock_d + timedelta(days=30) <= today_d else 0,
        }
        conn.execute(
            "UPDATE ipo_returns SET is_d30_due=?, is_m6_due=?, is_m12_due=?, "
            "is_unlock_due=? WHERE ipo_id = ?",
            (flags["is_d30_due"], flags["is_m6_due"], flags["is_m12_due"],
             flags["is_unlock_due"], r[0]),
        )
        n_updated += 1

    _mark_done(conn, "migration_v1_M4")
    return {"status": "applied", "rows_updated": n_updated, "asof": today_str}


def migrate_M5_currency(conn: sqlite3.Connection) -> dict:
    """ipo_cornerstone_link 加 currency + ticket_size_native + 把非 HKD 归一为 HKD.

    回填策略:
      1. 加列 currency (默认 'HKD'), ticket_size_native (默认 = ticket_size_hkd)
      2. 从 raw CSV 把 currency 拉过来 join (stock_code + cornerstone_name)
      3. 对 currency != 'HKD' 的行: ticket_size_hkd ← native × FX
    """
    if _migration_done(conn, "migration_v1_M5"):
        return {"status": "already_done"}

    for col, ddl in [
        ("currency", "TEXT DEFAULT 'HKD'"),
        ("ticket_size_native", "REAL"),
        ("fx_to_hkd", "REAL DEFAULT 1.0"),
    ]:
        if not _table_has_column(conn, "ipo_cornerstone_link", col):
            conn.execute(f"ALTER TABLE ipo_cornerstone_link ADD COLUMN {col} {ddl}")

    # 默认 native = 当前的 hkd 值 (兜底)
    conn.execute(
        "UPDATE ipo_cornerstone_link SET ticket_size_native = ticket_size_hkd "
        "WHERE ticket_size_native IS NULL"
    )

    # 从 raw CSV 拉 currency
    raw_csv = _ROOT / "data" / "raw" / "ifind" / "ifind_cornerstones.csv"
    if not raw_csv.exists():
        _mark_done(conn, "migration_v1_M5")
        return {"status": "applied", "currency_updates": 0,
                "note": "raw CSV missing, only default HKD applied"}

    from data_sources.ifind.field_mappings import (
        P05309_CORNERSTONES, parse_str, parse_float,
    )

    n_currency_set = 0
    n_converted = 0
    n_no_match = 0
    n_no_match_non_hkd = 0
    with raw_csv.open(encoding="utf-8-sig") as f:
        for raw_row in csv.DictReader(f):
            row = {P05309_CORNERSTONES.get(k, k): v for k, v in raw_row.items()}
            code = parse_str(row.get("stock_code"))
            cs_name = parse_str(row.get("cornerstone_name"))
            currency = parse_str(row.get("currency")) or "HKD"
            native = parse_float(row.get("ticket_size_hkd"))
            if not code or not cs_name:
                continue
            currency = currency.upper()
            if currency == "HKD":
                fx = 1.0
            elif currency == "USD":
                fx = FX_USD_HKD
            elif currency == "CNY":
                fx = FX_CNY_HKD
            else:
                fx = 1.0
                currency = "HKD"

            new_hkd = native * fx if native is not None else None

            # 多策略匹配: 1) alias 表查 cs_id; 2) (stock_code, cornerstone_name) 直查 link;
            # 3) (stock_code, cornerstone_name 子串) 模糊
            cs_id = None
            row_alias = conn.execute(
                "SELECT cornerstone_id FROM cornerstone_aliases "
                "WHERE alias_text_lower = ? LIMIT 1",
                (cs_name.lower(),),
            ).fetchone()
            if row_alias:
                cs_id = row_alias[0]
                cur = conn.execute(
                    "UPDATE ipo_cornerstone_link "
                    "SET currency = ?, fx_to_hkd = ?, ticket_size_native = ?, "
                    "    ticket_size_hkd = COALESCE(?, ticket_size_hkd) "
                    "WHERE stock_code = ? AND cornerstone_id = ?",
                    (currency, fx, native, new_hkd, code, cs_id),
                )
                rowcount = cur.rowcount
            else:
                # 直接按 (stock_code, cornerstone_name) 精确 / 子串匹配
                cur = conn.execute(
                    "UPDATE ipo_cornerstone_link "
                    "SET currency = ?, fx_to_hkd = ?, ticket_size_native = ?, "
                    "    ticket_size_hkd = COALESCE(?, ticket_size_hkd) "
                    "WHERE stock_code = ? AND LOWER(cornerstone_name) = LOWER(?)",
                    (currency, fx, native, new_hkd, code, cs_name),
                )
                rowcount = cur.rowcount
                if rowcount == 0:
                    cur = conn.execute(
                        "UPDATE ipo_cornerstone_link "
                        "SET currency = ?, fx_to_hkd = ?, ticket_size_native = ?, "
                        "    ticket_size_hkd = COALESCE(?, ticket_size_hkd) "
                        "WHERE stock_code = ? AND cornerstone_name LIKE ?",
                        (currency, fx, native, new_hkd, code, f"%{cs_name[:20]}%"),
                    )
                    rowcount = cur.rowcount
            if rowcount > 0:
                n_currency_set += 1
                if currency != "HKD":
                    n_converted += 1
            else:
                n_no_match += 1
                if currency != "HKD":
                    n_no_match_non_hkd += 1

    _mark_done(conn, "migration_v1_M5")
    return {
        "status": "applied",
        "currency_set_rows": n_currency_set,
        "non_hkd_converted": n_converted,
        "no_link_match": n_no_match,
        "no_link_match_non_hkd": n_no_match_non_hkd,
    }


def migrate_M6_check_constraints(conn: sqlite3.Connection) -> dict:
    """重建 ipo_cornerstone_link 添加 CHECK (affiliation_flag IN (0,1,2)).

    SQLite 不支持 ALTER TABLE ADD CHECK, 只能重建表.
    步骤: 建新表 → 拷数据 → 删旧表 → 改名.
    """
    if _migration_done(conn, "migration_v1_M6"):
        return {"status": "already_done"}

    # 验证当前数据是否合法
    bad = conn.execute(
        "SELECT COUNT(*) FROM ipo_cornerstone_link "
        "WHERE affiliation_flag NOT IN (0, 1, 2) AND affiliation_flag IS NOT NULL"
    ).fetchone()[0]
    if bad > 0:
        return {
            "status": "blocked",
            "reason": f"{bad} rows have affiliation_flag NOT IN (0,1,2) — fix data first",
        }

    # 检查表已经的列, 确保我们重建后字段一致 (含 M5 加的 currency/ticket_size_native)
    existing_cols = [r[1] for r in conn.execute("PRAGMA table_info(ipo_cornerstone_link)")]

    # 新表 DDL
    new_ddl = """
    CREATE TABLE ipo_cornerstone_link__new (
        link_id              INTEGER PRIMARY KEY AUTOINCREMENT,
        ipo_id               TEXT NOT NULL,
        cornerstone_id       TEXT NOT NULL,
        stock_code           TEXT,
        cornerstone_name     TEXT,
        ultimate_holder      TEXT,
        ticket_size_hkd      REAL,
        ticket_size_native   REAL,
        currency             TEXT DEFAULT 'HKD',
        fx_to_hkd            REAL DEFAULT 1.0,
        allocation_shares    INTEGER,
        subscribe_pct        REAL,
        lockup_months_actual INTEGER,
        unlock_date          DATE,
        affiliation_flag     INTEGER DEFAULT 0
                              CHECK (affiliation_flag IN (0, 1, 2)),
        affiliation_reason   TEXT,
        hangseng_industry    TEXT,
        data_source          TEXT,
        is_estimated         INTEGER DEFAULT 0,
        as_of_date           DATE,
        FOREIGN KEY (ipo_id) REFERENCES ipo_master(ipo_id),
        FOREIGN KEY (cornerstone_id) REFERENCES cornerstone_master(cornerstone_id)
    )
    """
    conn.execute(new_ddl)

    # 列名交集 (按新表顺序)
    new_cols = [r[1] for r in conn.execute("PRAGMA table_info(ipo_cornerstone_link__new)")]
    common = [c for c in new_cols if c in existing_cols and c != "link_id"]
    cols_csv = ", ".join(common)
    conn.execute(
        f"INSERT INTO ipo_cornerstone_link__new ({cols_csv}) "
        f"SELECT {cols_csv} FROM ipo_cornerstone_link"
    )

    # 删旧表, 改名
    conn.execute("DROP TABLE ipo_cornerstone_link")
    conn.execute("ALTER TABLE ipo_cornerstone_link__new RENAME TO ipo_cornerstone_link")

    # 重建索引 (DROP TABLE 会带走旧索引)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_link_ipo ON ipo_cornerstone_link(ipo_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_link_cs ON ipo_cornerstone_link(cornerstone_id)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_link_unique "
                 "ON ipo_cornerstone_link(ipo_id, cornerstone_id)")

    n = conn.execute("SELECT COUNT(*) FROM ipo_cornerstone_link").fetchone()[0]
    _mark_done(conn, "migration_v1_M6")
    return {"status": "applied", "rows_after": n}


def migrate_M7_share_capital_backfill(conn: sqlite3.Connection) -> dict:
    """share_capital 缺失 13 行 — 用 actual_issued_shares 反推 pre_ipo_shares.

    逻辑: 若 post_ipo, actual 已知, 则 pre = post - actual.
    """
    if _migration_done(conn, "migration_v1_M7"):
        return {"status": "already_done"}

    # share_capital 的数据其实是写到 ipo_master 的列 (pre_ipo_shares, post_ipo_shares).
    # 检查这些列是否存在, 不存在则跳过 (可能 fix_p1_share_capital_via_ifind.py 还没跑过).
    cols = [r[1] for r in conn.execute("PRAGMA table_info(ipo_master)")]
    if "pre_ipo_shares" not in cols or "post_ipo_shares" not in cols:
        _mark_done(conn, "migration_v1_M7")
        return {"status": "skipped", "reason": "share columns not in ipo_master"}

    # 反推: post 有值 + actual_issued (从 raw CSV) 也有值, 但 pre 缺
    # actual_issued_shares 字段在 ipo_master 中没单独存, 我们从 raw CSV join
    raw_csv = _ROOT / "data" / "raw" / "ifind" / "ifind_share_capital.csv"
    if not raw_csv.exists():
        _mark_done(conn, "migration_v1_M7")
        return {"status": "skipped", "reason": "raw CSV missing"}

    raw_map = {}
    with raw_csv.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try:
                raw_map[r["thscode"]] = {
                    "post": float(r["post_ipo_shares"]),
                    "actual": float(r["actual_issued_shares"]),
                    "pre_raw": float(r["pre_ipo_shares"]) if r["pre_ipo_shares"]
                               not in ("", "--", "—") else None,
                }
            except (ValueError, KeyError):
                continue

    # 找 ipo_master 中 pre_ipo_shares 缺的行
    rows = conn.execute(
        "SELECT stock_code, post_ipo_shares FROM ipo_master "
        "WHERE pre_ipo_shares IS NULL"
    ).fetchall()
    n_filled = 0
    for code, post in rows:
        info = raw_map.get(code)
        if info and info["actual"] is not None:
            post_val = post if post is not None else info["post"]
            pre_derived = post_val - info["actual"]
            if pre_derived > 0:
                conn.execute(
                    "UPDATE ipo_master SET pre_ipo_shares = ?, "
                    "overhang_ratio = COALESCE(overhang_ratio, ? / ?) "
                    "WHERE stock_code = ?",
                    (pre_derived, pre_derived, info["actual"], code),
                )
                n_filled += 1

    _mark_done(conn, "migration_v1_M7")
    return {"status": "applied", "rows_filled": n_filled}


def migrate_M8_view(conn: sqlite3.Connection) -> dict:
    """创建 mv_ipo_full 只读视图 (探索/回测一处入口)."""
    if _migration_done(conn, "migration_v1_M8"):
        return {"status": "already_done"}

    # SQLite VIEW: DROP + CREATE 保证 idempotent (CREATE VIEW IF NOT EXISTS 不能改定义)
    conn.execute("DROP VIEW IF EXISTS mv_ipo_full")
    conn.execute("""
        CREATE VIEW mv_ipo_full AS
        SELECT
            m.ipo_id, m.stock_code, m.company_name_zh, m.listing_date, m.pricing_date,
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
        LEFT JOIN ipo_returns r ON r.ipo_id = m.ipo_id
    """)
    _mark_done(conn, "migration_v1_M8")
    return {"status": "applied"}


# =============================================================================
# Driver
# =============================================================================

STEPS = [
    ("M0 dedupe link",    "migrate_M0_dedupe_links"),
    ("M1 schema patches", "migrate_M1_schema_patches"),
    ("M2 indexes",        "migrate_M2_indexes"),
    ("M3 gross proceeds", "migrate_M3_gross_proceeds"),
    ("M4 due flags",      "migrate_M4_due_flags"),
    ("M5 currency",       "migrate_M5_currency"),
    ("M6 CHECK affiliation_flag", "migrate_M6_check_constraints"),
    ("M7 share_capital backfill", "migrate_M7_share_capital_backfill"),
    ("M8 mv_ipo_full view", "migrate_M8_view"),
]


def run(db_path: Path) -> int:
    """Run all migrations on db_path. Each step commits independently.

    Note: SQLite DDL statements auto-commit any pending transaction;
    therefore we don't try to make the whole sequence atomic. Caller is
    expected to take a backup first (我们在 main() 自动做).
    Use --target to operate on a temporary copy ("dry-run").
    """
    if not db_path.exists():
        print(f"ERROR: DB not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")  # M6 期间临时关
    try:
        for name, fn_name in STEPS:
            print(f"\n=== {name} ===")
            fn = globals()[fn_name]
            res = fn(conn)
            for k, v in res.items():
                print(f"  {k}: {v}")
            conn.commit()  # 每步独立提交, 失败也保留前面已成功的步骤
        print("\nALL STEPS COMMITTED ✓")
        return 0
    except Exception as e:
        conn.rollback()
        print(f"\nFAILED at last step, partial migration retained: "
              f"{type(e).__name__}: {e}", file=sys.stderr)
        raise
    finally:
        conn.close()


def main() -> int:
    import shutil
    from datetime import datetime
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(_ROOT / "data" / "nacs_real.db"))
    ap.add_argument("--dry-run", action="store_true",
                    help="复制 DB 到 .dryrun.db, 在副本上跑迁移, 完毕后删除")
    args = ap.parse_args()

    src = Path(args.db)
    if args.dry_run:
        target = src.with_suffix(".dryrun.db")
        shutil.copy(src, target)
        print(f"[DRY-RUN] working on copy: {target}")
        try:
            return run(target)
        finally:
            if target.exists():
                target.unlink()
                print(f"[DRY-RUN] deleted copy: {target}")
    else:
        # 真跑: 若有任何步骤未完成则备份 source. 全部 done 则跳过备份
        # (避免幂等重跑产生多余备份).
        # 命名约定: <name>.db.bak_migrate_v1_<ts>
        # (与 .gitignore 中 !data/nacs_real.db.bak_* 一致, 备份会进 git)
        if _any_pending_step(src):
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = src.with_name(f"{src.name}.bak_migrate_v1_{ts}")
            shutil.copy(src, backup)
            print(f"AUTO-BACKUP: {backup}")
        else:
            print("All migrations already done, skipping backup.")
        return run(src)


def _any_pending_step(db_path: Path) -> bool:
    """Return True if any migration step hasn't been marked done."""
    try:
        c = sqlite3.connect(str(db_path))
        try:
            existing = {r[0] for r in c.execute(
                "SELECT key FROM db_metadata WHERE key LIKE 'migration_v1_M%'"
            )}
        finally:
            c.close()
    except sqlite3.Error:
        return True
    needed = {f"migration_v1_M{i}" for i in range(9)}  # M0..M8
    return bool(needed - existing)


if __name__ == "__main__":
    raise SystemExit(main())
