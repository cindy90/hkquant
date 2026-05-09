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
    offering_size_hkd       REAL,                 -- 含绿鞋 (raw CSV 来源, 不一定 = price × shares)
    gross_proceeds_excl_greenshoe REAL,           -- = offer_price × total_offer_shares (派生, migration v1)
    total_offer_shares      REAL,                 -- 全球发售股数 (来自 raw CSV)
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
    last_round_premium      REAL,                 -- ⚠ 当前 100% NULL, L1 否决条款未启用; 数据补齐后自动生效
    -- 基石聚合
    cornerstone_total_hkd   REAL,
    cornerstone_coverage    REAL,
    cornerstone_count       INTEGER,
    lockup_months           INTEGER DEFAULT 6,
    -- 反幸存者偏差
    is_delisted             INTEGER DEFAULT 0,
    delisting_date          DATE,
    is_acquired             INTEGER DEFAULT 0,
    -- Deal pipeline (v2: 区分招股/定价/上市/退市/撤回阶段)
    status                  TEXT NOT NULL DEFAULT 'listed'
                            CHECK (status IN ('prospectus', 'pricing', 'listed',
                                              'delisted', 'withdrawn')),
    prospectus_pdf_path     TEXT,                  -- 本地招股说明书存档
    expected_listing_date   DATE,                  -- 招股说明书披露的预期日 (与最终 listing_date 区分)
    -- 风险/股本 (originally added by fix_p1_* scripts; consolidated into schema)
    pre_ipo_shares          REAL,
    post_ipo_shares         REAL,
    overhang_ratio          REAL,                 -- post / actual_issued
    peer_lockup_avg_drawdown REAL,                -- 同行历史 lockup 期 max DD 平均
    pe_vs_history_pct       REAL,                 -- 当前 PE 在该公司过去 PE 历史中的百分位
    fundamental_risk_score  REAL,                 -- 财务恶化度 [0,1], 越高越糟
    -- 数据质量
    data_quality_score      REAL DEFAULT 1.0,    -- [0,1]
    data_source_notes       TEXT,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ipo_date ON ipo_master(listing_date);
CREATE INDEX IF NOT EXISTS idx_ipo_chapter ON ipo_master(listing_chapter);
CREATE INDEX IF NOT EXISTS idx_ipo_gics ON ipo_master(gics_l2);
CREATE INDEX IF NOT EXISTS idx_ipo_stock_code ON ipo_master(stock_code);
CREATE INDEX IF NOT EXISTS idx_ipo_status ON ipo_master(status);

-- =============================================================================
-- 4. IPO-基石多对多关系
-- =============================================================================
CREATE TABLE IF NOT EXISTS ipo_cornerstone_link (
    link_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ipo_id               TEXT NOT NULL,
    cornerstone_id       TEXT NOT NULL,
    stock_code           TEXT,
    cornerstone_name     TEXT,                    -- 招股书原文
    ultimate_holder      TEXT,
    ticket_size_hkd      REAL,                    -- 归一为 HKD (= ticket_size_native × fx_to_hkd)
    ticket_size_native   REAL,                    -- 原始货币金额
    currency             TEXT DEFAULT 'HKD',      -- HKD / USD / CNY
    fx_to_hkd            REAL DEFAULT 1.0,        -- 写入时锁定的换算率
    allocation_shares    INTEGER,
    subscribe_pct        REAL,
    lockup_months_actual INTEGER,
    unlock_date          DATE,
    affiliation_flag     INTEGER DEFAULT 0
                          CHECK (affiliation_flag IN (0, 1, 2)),
                                                  -- 0=否, 1=明确关联, 2=可疑/待确认
    affiliation_reason   TEXT,
    hangseng_industry    TEXT,
    data_source          TEXT,                    -- prospectus / allocation_announcement / manual / iFinD_p05309
    is_estimated         INTEGER DEFAULT 0,       -- ticket_size 是否为估计
    as_of_date           DATE,
    FOREIGN KEY (ipo_id) REFERENCES ipo_master(ipo_id),
    FOREIGN KEY (cornerstone_id) REFERENCES cornerstone_master(cornerstone_id)
);

CREATE INDEX IF NOT EXISTS idx_link_ipo ON ipo_cornerstone_link(ipo_id);
CREATE INDEX IF NOT EXISTS idx_link_cs ON ipo_cornerstone_link(cornerstone_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_link_unique
    ON ipo_cornerstone_link(ipo_id, cornerstone_id);

-- =============================================================================
-- 5. 日频价格历史 (派生 M+1/M+3/M+6/解禁后等收益)
-- ⚠ 当前 0 行: ipo_returns 已通过 fix_p1_returns_via_ifind.py 直接派生写入,
--   不依赖此表. 重跑 dao.compute_ipo_returns() 会清空 ipo_returns.
--   见 docs/dev.md "未填充表".
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
    -- 业绩成熟标记 (NULL vs 真缺数 → IC 报告只统计 due=1 样本):
    is_d30_due          INTEGER DEFAULT 0,
    is_m6_due           INTEGER DEFAULT 0,
    is_m12_due          INTEGER DEFAULT 0,
    is_unlock_due       INTEGER DEFAULT 0,
    FOREIGN KEY (ipo_id) REFERENCES ipo_master(ipo_id)
);

-- =============================================================================
-- 8. 保荐人画像 (类似基石画像, 按 as-of-date 物化)
-- ⚠ 当前 0 行: 数据未灌, 见 docs/dev.md "未填充表" 章节
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
-- 11. 财务年报 (THS_BD: total_oi/gross_margin/net_margin/roe/ni_attr)
-- =============================================================================
CREATE TABLE IF NOT EXISTS ipo_financials (
    stock_code      TEXT NOT NULL,
    report_year     INTEGER NOT NULL,
    revenue_cny     REAL,
    gross_margin    REAL,
    net_margin      REAL,
    roe             REAL,
    PRIMARY KEY (stock_code, report_year)
);
CREATE INDEX IF NOT EXISTS idx_fin_code_year ON ipo_financials(stock_code, report_year);

-- =============================================================================
-- 12. 概念板块成分 (DataPool: 港股概念-IPO 多对多)
-- =============================================================================
CREATE TABLE IF NOT EXISTS ipo_concepts (
    ipo_id          TEXT NOT NULL,
    stock_code      TEXT NOT NULL,
    concept_id      TEXT NOT NULL,
    concept_name    TEXT,
    data_date       TEXT
);
CREATE INDEX IF NOT EXISTS idx_ipo_concepts_stock ON ipo_concepts(stock_code);
CREATE INDEX IF NOT EXISTS idx_ipo_concepts_concept ON ipo_concepts(concept_id);

-- =============================================================================
-- 13. 行业分类 (恒生/申万/同花顺 多源, 一只 IPO 一个 source 一行)
-- =============================================================================
CREATE TABLE IF NOT EXISTS ipo_industries (
    ipo_id          TEXT NOT NULL,
    stock_code      TEXT NOT NULL,
    source          TEXT NOT NULL,                -- 'hs' / 'sw' / 'ths_global'
    l1_name         TEXT, l2_name TEXT, l3_name TEXT, l4_name TEXT,
    leaf_bid        TEXT,
    leaf_level      INTEGER,
    data_date       TEXT
);
CREATE INDEX IF NOT EXISTS idx_ipo_industries_stock ON ipo_industries(stock_code);
CREATE INDEX IF NOT EXISTS idx_ipo_industries_leaf ON ipo_industries(leaf_bid);
CREATE INDEX IF NOT EXISTS idx_ipo_industries_l1 ON ipo_industries(l1_name);

-- =============================================================================
-- 14. 视图: 探索 / 回测的统一入口 (一处更新, 全脚本受益)
-- 用法:    SELECT * FROM mv_ipo_full WHERE status='listed' AND is_m6_due = 1
-- 注:      包含所有 status 的 IPO; 探索 panel 时务必加 WHERE status='listed'
-- =============================================================================
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

-- =============================================================================
-- 15. Panel snapshot: 全量回测面板的可还原快照 (deal 评估的"参考标杆")
-- 每跑一次 run_v7_backtest 写一行; 单 deal 评估引用 panel_snapshot_id 锁定上下文
-- =============================================================================
CREATE TABLE IF NOT EXISTS panel_snapshots (
    snapshot_id          TEXT PRIMARY KEY,                -- e.g. PANEL_2026-05-09_a3f2
    asof_date            DATE NOT NULL,
    n_ipos_in_universe   INTEGER NOT NULL,                -- panel 成员 (status='listed') 数量
    market_env_json      TEXT NOT NULL,                   -- MarketEnvironment 8 字段
    regime_score         REAL,                            -- panel 整体 regime_score
    member_ipo_ids_json  TEXT NOT NULL,                   -- JSON array of ipo_id
    aggregates_json      TEXT,                            -- 跨章节聚合 (中位/IQR/分章节)
    config_version       TEXT,
    config_hash          TEXT,
    config_yaml_snapshot TEXT,                            -- 完整 YAML 文本嵌入
    code_git_sha         TEXT,
    db_schema_version    TEXT,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notes                TEXT
);
CREATE INDEX IF NOT EXISTS idx_panel_asof ON panel_snapshots(asof_date);

-- =============================================================================
-- 16. NACS predictions: 单 deal 一次评估的完整快照 (audit trail)
-- key = (stock_code, asof_date, price_scenario, panel_snapshot_id) → case_id
-- =============================================================================
CREATE TABLE IF NOT EXISTS nacs_predictions (
    case_id              TEXT PRIMARY KEY,                -- e.g. PRED_1187.HK_2025-08-15_mid_a3f2
    stock_code           TEXT NOT NULL,                   -- 拟上市/已上市代码
    asof_date            DATE NOT NULL,                   -- 分析切点
    panel_snapshot_id    TEXT NOT NULL,                   -- → panel_snapshots
    deal_status_at_analysis TEXT,                         -- prospectus / pricing / listed
    -- 多场景定价
    price_scenario       TEXT,                            -- low / mid / high / final
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
    -- 完整诊断
    layer1_components_json TEXT,
    layer2_components_json TEXT,
    layer3_components_json TEXT,
    adjustments_json     TEXT,
    warnings_json        TEXT,
    -- 输入快照 (锁定"分析当时知道什么")
    inputs_json          TEXT NOT NULL,                   -- 完整 IPOOffering dict
    -- 同伴比对
    nacs_pct_in_panel    REAL,                            -- 0..1 panel 内百分位
    nacs_pct_in_chapter  REAL,                            -- 同章节子样本内百分位
    similar_cases_json   TEXT,                            -- 最相似 5 只 listed IPO 的实际收益
    --
    run_at               TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notes                TEXT,
    FOREIGN KEY (panel_snapshot_id) REFERENCES panel_snapshots(snapshot_id)
);
CREATE INDEX IF NOT EXISTS idx_pred_code ON nacs_predictions(stock_code, asof_date);
CREATE INDEX IF NOT EXISTS idx_pred_panel ON nacs_predictions(panel_snapshot_id);
CREATE INDEX IF NOT EXISTS idx_pred_decision ON nacs_predictions(decision);
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
