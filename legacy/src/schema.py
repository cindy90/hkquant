"""
NACS 数据库 schema - SQLite

设计原则:
    1. Bitemporal: 衍生字段(基石画像)按 as-of-date 物化, 严格防look-ahead
    2. 别名归一: cornerstone_aliases 表是匹配的痛点和价值所在
    3. 反幸存者偏差: ipo_master.is_delisted 字段保留所有历史IPO
    4. 数据质量分: 字段缺失/估计 vs 实数, 用于回测样本筛选

升级路径: SQLite -> PostgreSQL (>20万行后), schema 一致, 仅迁库
"""
from __future__ import annotations

SCHEMA_SQL = """
-- =============================================================================
-- 1. 基石机构主表
-- =============================================================================
CREATE TABLE IF NOT EXISTS cornerstone_master (
    cornerstone_id      TEXT PRIMARY KEY,
    canonical_name      TEXT NOT NULL,
    name_zh             TEXT,
    cornerstone_type    TEXT NOT NULL,        -- 8档枚举
    parent_entity       TEXT,                  -- 母公司, 用于产业资本
    country_of_origin   TEXT,
    aum_usd_latest      REAL,
    aum_asof_date       DATE,
    is_chinese          INTEGER DEFAULT 0,    -- bool
    is_longterm         INTEGER DEFAULT 0,    -- bool
    notes               TEXT,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_cs_type ON cornerstone_master(cornerstone_type);
CREATE INDEX IF NOT EXISTS idx_cs_country ON cornerstone_master(country_of_origin);

-- =============================================================================
-- 2. 别名表 (招股书原文 -> cornerstone_id)
-- =============================================================================
CREATE TABLE IF NOT EXISTS cornerstone_aliases (
    alias_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    cornerstone_id      TEXT NOT NULL,
    alias_text          TEXT NOT NULL,
    alias_text_lower    TEXT NOT NULL,        -- 用于 case-insensitive 匹配
    alias_type          TEXT NOT NULL,        -- legal_name / chinese / english / spv / abbreviation / stock_code
    match_confidence    REAL DEFAULT 1.0,
    FOREIGN KEY (cornerstone_id) REFERENCES cornerstone_master(cornerstone_id)
);

CREATE INDEX IF NOT EXISTS idx_alias_lower ON cornerstone_aliases(alias_text_lower);
CREATE UNIQUE INDEX IF NOT EXISTS idx_alias_unique
    ON cornerstone_aliases(cornerstone_id, alias_text_lower);

-- =============================================================================
-- 3. IPO主表
-- =============================================================================
CREATE TABLE IF NOT EXISTS ipo_master (
    ipo_id                  TEXT PRIMARY KEY,    -- e.g. HK_03296_2026
    stock_code              TEXT NOT NULL,
    company_name_zh         TEXT,
    company_name_en         TEXT,
    listing_date            DATE NOT NULL,
    pricing_date            DATE,                 -- 用于 as-of-date 派生
    listing_chapter         TEXT NOT NULL,
    is_a_h                  INTEGER DEFAULT 0,
    a_share_code            TEXT,
    gics_l2                 TEXT,
    -- 发行结构
    offer_price_hkd         REAL,
    offer_price_low         REAL,
    offer_price_high        REAL,
    offering_size_hkd       REAL,
    pricing_in_range        REAL,
    intl_oversub            REAL,
    public_oversub          REAL,
    clawback_triggered      INTEGER,
    greenshoe_pct           REAL,
    greenshoe_exercised     INTEGER,
    -- 中介
    sponsor_primary         TEXT,
    sponsor_tier            INTEGER,
    joint_sponsor_count     INTEGER DEFAULT 1,
    auditor_tier            INTEGER DEFAULT 1,
    -- 估值
    pe_at_offer             REAL,
    pe_peer_median          REAL,
    last_round_premium      REAL,
    -- 基石聚合
    cornerstone_total_hkd   REAL,
    cornerstone_coverage    REAL,
    cornerstone_count       INTEGER,
    lockup_months           INTEGER DEFAULT 6,
    -- 反幸存者偏差
    is_delisted             INTEGER DEFAULT 0,
    delisting_date          DATE,
    is_acquired             INTEGER DEFAULT 0,
    -- 数据质量
    data_quality_score      REAL DEFAULT 1.0,    -- [0,1]
    data_source_notes       TEXT,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ipo_date ON ipo_master(listing_date);
CREATE INDEX IF NOT EXISTS idx_ipo_chapter ON ipo_master(listing_chapter);
CREATE INDEX IF NOT EXISTS idx_ipo_gics ON ipo_master(gics_l2);

-- =============================================================================
-- 4. IPO-基石多对多关系
-- =============================================================================
CREATE TABLE IF NOT EXISTS ipo_cornerstone_link (
    link_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ipo_id              TEXT NOT NULL,
    cornerstone_id      TEXT NOT NULL,
    ticket_size_hkd     REAL,
    allocation_shares   INTEGER,
    lockup_months_actual INTEGER,
    affiliation_flag    INTEGER DEFAULT 0,
    affiliation_reason  TEXT,
    data_source         TEXT,                    -- prospectus / allocation_announcement / manual
    is_estimated        INTEGER DEFAULT 0,       -- ticket_size 是否为估计
    as_of_date          DATE,
    FOREIGN KEY (ipo_id) REFERENCES ipo_master(ipo_id),
    FOREIGN KEY (cornerstone_id) REFERENCES cornerstone_master(cornerstone_id)
);

CREATE INDEX IF NOT EXISTS idx_link_ipo ON ipo_cornerstone_link(ipo_id);
CREATE INDEX IF NOT EXISTS idx_link_cs ON ipo_cornerstone_link(cornerstone_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_link_unique
    ON ipo_cornerstone_link(ipo_id, cornerstone_id);

-- =============================================================================
-- 5. 日频价格历史 (派生 M+1/M+3/M+6/解禁后等收益)
-- =============================================================================
CREATE TABLE IF NOT EXISTS price_history (
    ipo_id              TEXT NOT NULL,
    trade_date          DATE NOT NULL,
    close_hkd           REAL,
    volume              REAL,
    turnover_hkd        REAL,
    is_suspended        INTEGER DEFAULT 0,
    PRIMARY KEY (ipo_id, trade_date),
    FOREIGN KEY (ipo_id) REFERENCES ipo_master(ipo_id)
);

CREATE INDEX IF NOT EXISTS idx_price_date ON price_history(trade_date);

-- =============================================================================
-- 6. 派生表: 基石画像快照 (按 as-of-date 物化, 防 look-ahead)
-- =============================================================================
CREATE TABLE IF NOT EXISTS cornerstone_performance_asof (
    cornerstone_id          TEXT NOT NULL,
    as_of_date              DATE NOT NULL,
    ipo_count_5y            INTEGER DEFAULT 0,
    avg_m6_return_5y        REAL,
    winrate_m6_5y           REAL,
    avg_d30_return_5y       REAL,
    lockup_discipline_score REAL,
    sector_expertise        TEXT,                -- JSON: {gics_l2: count}
    PRIMARY KEY (cornerstone_id, as_of_date),
    FOREIGN KEY (cornerstone_id) REFERENCES cornerstone_master(cornerstone_id)
);

CREATE INDEX IF NOT EXISTS idx_perf_date ON cornerstone_performance_asof(as_of_date);

-- =============================================================================
-- 7. 派生表: IPO收益快照 (一次算清, 回测时直接join)
-- =============================================================================
CREATE TABLE IF NOT EXISTS ipo_returns (
    ipo_id              TEXT PRIMARY KEY,
    return_d1_close     REAL,                    -- 上市首日收盘 vs 发行价
    return_d30          REAL,
    return_m3           REAL,
    return_m6           REAL,                    -- 解禁日附近, 关键指标
    return_m12          REAL,
    return_unlock_d30   REAL,                    -- 解禁后30天
    return_unlock_d90   REAL,                    -- 解禁后90天 (overhang测量)
    max_drawdown_m6     REAL,                    -- 锁定期内最大回撤
    avg_daily_volume_hkd REAL,                   -- 流动性
    FOREIGN KEY (ipo_id) REFERENCES ipo_master(ipo_id)
);

-- =============================================================================
-- 8. 保荐人画像 (类似基石画像, 按 as-of-date 物化)
-- =============================================================================
CREATE TABLE IF NOT EXISTS sponsor_performance_asof (
    sponsor_name            TEXT NOT NULL,
    as_of_date              DATE NOT NULL,
    ipo_count_3y            INTEGER DEFAULT 0,
    avg_d30_return_3y       REAL,
    breakage_rate_3y        REAL,
    winrate_d30_3y          REAL,
    pct_rank_winrate        REAL,                -- 全市场百分位
    pct_rank_breakage       REAL,
    pct_rank_avg_d30        REAL,
    PRIMARY KEY (sponsor_name, as_of_date)
);

-- =============================================================================
-- 9. 元数据: 数据库版本/构建时间
-- =============================================================================
CREATE TABLE IF NOT EXISTS db_metadata (
    key                 TEXT PRIMARY KEY,
    value               TEXT,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================================
-- 10. 市场环境快照缓存 (按月聚合, 用于 MarketEnvironment 实时化)
-- =============================================================================
CREATE TABLE IF NOT EXISTS market_environment_cache (
    asof_month                      DATE PRIMARY KEY,    -- 月初, e.g. 2024-03-01
    hsi_60d_return                  REAL,
    hsi_60d_vol_annualized          REAL,
    hsi_60d_vol_pct_rank            REAL,
    hsi_valuation_pct               REAL,
    hk_ipo_30d_avg_d30              REAL,
    hk_ipo_30d_breakage_rate        REAL,
    southbound_30d_net_normalized   REAL,
    sector_60d_vol_annualized       REAL,
    source                          TEXT,                -- 'ifind' / 'json' / 'fallback'
    created_at                      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def init_database(db_path: str) -> None:
    """初始化数据库; 已存在则补缺(IF NOT EXISTS)"""
    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
        conn.execute(
            "INSERT OR REPLACE INTO db_metadata(key, value) VALUES(?, ?)",
            ("schema_version", "1.0"),
        )
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    db_path = sys.argv[1] if len(sys.argv) > 1 else "nacs.db"
    init_database(db_path)
    print(f"NACS DB initialized at: {db_path}")
