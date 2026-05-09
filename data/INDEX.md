# data/ 数据资产总索引

> 后续所有分析/建模/回测必须基于本目录数据。`outputs/` 仅作脚本一次性输出 trace,
> **不应被任何脚本读取**。
>
> 本文件随 governance migration v1 (M0–M8, 2026-05-09 11:03) 和
> migration v2 (M9–M12, 12:27) 重写, 反映**治理后**状态.

## 目录结构

```
data/
├── INDEX.md                              # 本文件
├── _archive_manifest.csv                 # outputs → data 归档轨迹 (审计 log)
├── nacs_real.db                          # ★主数据库 (SQLite)
├── nacs_real.db.bak_p0fix                # P0 修复里程碑 (最早起点)
├── nacs_real.db.bak_premigrate_*         # migration v1 之前的快照
├── nacs_real.db.bak_premigrate_v2_*      # migration v2 之前的快照
│   # 2026-05-09 cleanup phase 1 删除了 5 个冗余 backup (md5 重复或已 push 到 git)
│
├── deals/                                # 拟上市公司 deal 输入 (人工 YAML)
│   └── TEMPLATE.yaml                     #   含模板 + 字段说明
├── prospectus/                           # 招股说明书 PDF 存档 (gitignored, 大文件)
│
├── raw/                                  # 原始数据 (禁止修改)
│   ├── overrides.yaml                    # 字段级人工修正 (raw + overrides → DB)
│   ├── ifind/                            # iFinD 自动化 CSV
│   │   ├── ifind_ipo_info.csv            # P05310 IPO 一览 (397 行)
│   │   ├── ifind_cornerstones.csv        # P05309 基石明细 (1613 行)
│   │   ├── ifind_financials_annual.csv   # 年报财务 (1588 行)
│   │   ├── ifind_share_capital.csv       # 股本信息 (384 行)
│   │   ├── ifind_secondary_offerings.csv # 二次发行 (2421 行)
│   │   ├── ifind_indicator_catalog.csv   # 字段对照表 (30 行)
│   │   ├── ifind_blocks.csv.broken       # ⚠ 损坏文件 (Python list dump 误覆写),
│   │   │                                 #    见 README_blocks_broken.md
│   │   ├── README_blocks_broken.md
│   │   └── probe_southbound_2026-05-08.json
│   └── manual/                           # 人工导出的 Excel (替代旧 IFIND板块ID/)
│       ├── README.md
│       ├── hk_concept_blocks_lookup.xlsx
│       └── southbound_flow_2026-05-08.xlsx
│
├── dict/                                 # 板块/主题字典 (块 ID → 成员)
│   ├── README.md
│   ├── hs_industry_blocks.json           # 恒生 112 末级
│   ├── hk_concept_blocks.json            # 港股概念 223
│   ├── ths_global_industry_blocks.json   # 同花顺全球 163
│   ├── sw_industry_blocks.json           # 申万 346
│   ├── *_index.csv                       # 配套人类可读索引
│   └── themes/                           # 主题板块成分股缓存 (从 data/ 根迁来)
│       ├── README.md
│       ├── constituents_cache.json
│       └── constituents_probe.json
│
└── derived/                              # 派生数据 (清洗后产出)
    ├── _archive/                         # ⚠ v6→v7 迁移前的实验, 不再读取
    │   ├── README.md
    │   ├── ipo_d30_returns.csv
    │   ├── nacs_v7.csv
    │   └── nacs_yearly_aff.csv
    ├── ipo_classification/               # 行业/概念分类
    │   ├── README.md
    │   ├── ipo_concepts.csv              # 1:N 长表 (DB 权威导出)
    │   ├── ipo_industries.csv            # 1:N 长表 (DB 权威导出)
    │   ├── *_wide.csv                    # 1:1 宽表对照
    │   ├── concept_coverage.csv
    │   └── industry_coverage.csv
    ├── snapshots/                        # 带日期的派生快照 (统一入口)
    │   ├── README.md
    │   └── <YYYY-MM-DD>/                 # 一日一目录
    │       ├── chapter_report_<ymd>.csv
    │       ├── chapter_mismatch_<ymd>.csv
    │       ├── industry_report_<ymd>.csv
    │       ├── industry_mismatch_<ymd>.csv
    │       ├── ic_results_<ymd>.csv
    │       ├── ic_top50_<ymd>.csv
    │       ├── ic_robustness_<ymd>.csv
    │       ├── ic_top_signals_detail_<ymd>.csv
    │       └── nacs_v7_scores_<ymd>.csv
    ├── latest/                           # 稳定别名 symlinks → snapshots/<最新>/
    │   ├── chapter_report.csv -> ../snapshots/...
    │   ├── ic_results.csv     -> ../snapshots/...
    │   └── ...
    ├── verification/                     # README only (历史快照已迁至 snapshots/)
    │   └── README.md
    ├── peer_ic/                          # README only
    │   └── README.md
    ├── scores/                           # README only
    │   └── README.md
    └── backtest/
        ├── latest/                       # 最新回测 IC 摘要 (overwrite 覆盖式)
        │   └── ic_realtime.json
        └── iterations/                   # 历次迭代快照
```

## DB 表清单 (post-governance)

| 表 | 行数 | 说明 |
|---|---|---|
| `ipo_master` | 384 | IPO 主表 (含 `status` 列: prospectus/pricing/listed/delisted/withdrawn; 含 `gross_proceeds_excl_greenshoe` 派生列) |
| `ipo_returns` | 384 | 收益 (含 `is_d30/m6/m12/unlock_due` 业绩到期标志, v1 M4 加) |
| `ipo_financials` | 1588 | 年度财务 (4 年 × 397 IPO) |
| `ipo_concepts` | 321 | IPO × 概念 (1:N) |
| `ipo_industries` | 592 | IPO × (sw / ths_global) 行业 (1:N) |
| `ipo_cornerstone_link` | 1604 | IPO × 基石 (1:N; 含 `currency / ticket_size_native / fx_to_hkd`, v1 M5 加; UNIQUE 约束 + CHECK affiliation_flag IN (0,1,2), v1 M2/M6) |
| `cornerstone_master` | 1314 | 基石主表 |
| `cornerstone_aliases` | 1051 | 基石别名映射 (~1051 covers 80% master) |
| `cornerstone_performance_asof` | 31464 | 基石画像 as-of-date 物化 (1314 CS × 23 切点) |
| `market_environment_cache` | 55 | 市场环境月聚合 |
| `panel_snapshots` | 0+ | v2 M10: 全量回测面板的可还原快照 (每跑 run_v7_backtest 加一行) |
| `nacs_predictions` | 0+ | v2 M11: 单 deal 评估 audit trail (analyze_deal --persist 加一行) |
| `db_metadata` | 14 | schema_version + migration_v1/v2 标志 |
| `price_history` | 0 | ⚠ 空, 见 docs/dev.md "未填充表" |
| `sponsor_performance_asof` | 0 | ⚠ 空, 同上 |

视图: `mv_ipo_full` (384 行) — 探索的统一入口, 详见 docs/dev.md §10.3.

## 数据流向

```
data/raw/ifind/*.csv     ──┐
data/raw/manual/*.xlsx   ──┤  (ETL 入库)
data/raw/overrides.yaml  ──┤
data/deals/*.yaml        ──┤  (人工 deal 录入)
                            ▼
                       data/nacs_real.db                ← 真相之源
                            │
            ┌───────────────┼───────────────┬────────────────┐
            ▼               ▼               ▼                ▼
       run_v7_backtest  scripts/explore  scripts/verify  scripts/analyze_deal
            │               │               │                │
            ▼               ▼               ▼                ▼
       outputs/*.csv (临时, 一次性)
            │
            ▼
   scripts/archive_outputs.py
            │
            ▼
   data/derived/snapshots/<YYYY-MM-DD>/   ← 长期存档
   data/derived/latest/                   ← symlink 到最新
```

## 上游脚本映射

| 派生文件 | 上游脚本 | 触发频率 |
|---|---|---|
| `dict/hs_industry_blocks.json` | `scripts/verify_industry_via_ifind.py` | 季度 |
| `dict/hk_concept_blocks.json` | `scripts/fetch_hk_concepts.py` | 季度 |
| `dict/{sw,ths_global}_industry_blocks.json` | `scripts/fetch_hk_industries.py` | 季度 |
| `dict/themes/constituents_cache.json` | `scripts/fetch_hk_market_data.py` | 月度 (按需) |
| `derived/ipo_classification/*.csv` | DB 表导出 (archive_outputs.py) | 跟随字典 |
| `derived/snapshots/*/chapter_*.csv` | `scripts/verify_listing_chapter*.py` | 每次 ingest |
| `derived/snapshots/*/industry_*.csv` | `scripts/verify_industry_via_ifind.py` | 每次 ingest |
| `derived/snapshots/*/ic_*.csv` | `scripts/explore_peer_*.py` | 模型迭代时 |
| `derived/snapshots/*/nacs_v7_scores_*.csv` | `run_v7_backtest.py` | 模型迭代时 |
| `derived/backtest/latest/*.json` | `run_v7_backtest.py` | 同上 |
| `nacs_predictions` (DB) | `scripts/analyze_deal.py --persist` | 每次评估 deal |
| `panel_snapshots` (DB) | `run_v7_backtest.py` | 每次回测 |

## 编码约定

| 路径 | 编码 | 行尾 |
|---|---|---|
| `raw/**/*.csv` | UTF-8 with BOM | CRLF (保留原貌) |
| `dict/*.csv` | UTF-8 无 BOM | LF |
| `derived/**/*.csv` | UTF-8 无 BOM | LF |
| `*.json` / `*.yaml` | UTF-8 | LF |

## 严格原则

1. **不得直接读 `outputs/`** — 仅作脚本临时输出, 过期即删
2. **不得修改 `raw/`** — 这是审计追溯的根 (人工修正走 `raw/overrides.yaml`)
3. **DB 是唯一真相源** — `derived/*.csv` 与 DB 不一致时以 DB 为准, 重新导出
4. **新增派生数据须落 `derived/snapshots/<YYYY-MM-DD>/`** + (可选) 链接 `latest/`
5. **历史快照保留** — 文件名加 `_YYYYMMDD` 后缀, 不要覆盖
6. **派生废弃文件移入 `derived/_archive/`** — 不直接删, 便于追溯口径迁移
7. **migration 标志在 `db_metadata` 里** — 按 `migration_v*_M*` 键识别已跑过的步骤
