# data/ 数据资产总索引

> 后续所有分析/建模/回测必须基于本目录数据。`outputs/` 仅作脚本一次性运行 trace，**不应被任何脚本读取**。
> 2026-05-09 维护：outputs/ 已清空（仅留 NOTICE.md）；derived/ 三个旧文件归档至 `_archive/`；db 中间快照 bak_p1* 已删除。

## 目录结构

```
data/
├── INDEX.md                              # 本文件
├── nacs_real.db                          # ★主数据库（SQLite），13 张表
├── nacs_real.db.bak_p0fix                # p0 修复里程碑（最早起点）
├── nacs_real.db.bak_p3_chapter_20260509_164748     # 章节修复前
├── nacs_real.db.bak_concepts_20260509_170343       # 概念入库前
├── nacs_real.db.bak_industries_20260509_171756     # 行业入库前
│   # 注：2026-05-09 清理了 7 个 bak_p1*（中间快照），仅保留 p0 起点 + 3 个里程碑
├── watchlist.json
├── theme_constituents_*.json
├── IFIND板块ID/                          # 板块 ID 手工字典 Excel
│   ├── Book1.xlsx
│   └── Book2.xlsx
├── raw/                                  # 原始数据（iFinD/Wind 拉取，禁止改动）
│   ├── ifind/                            # 7 个 csv，BOM + CRLF 原貌
│   │   # ⚠️ ifind_blocks.csv 仅 1 个板块标签 "18A"，3 行 ×（dates/codes/names）；
│   │   #    list 长度 5414 且含 BJ/SZ/SH 全市场代码（如 000002.SZ），与 18A 章语义不符，
│   │   #    疑似 iFinD 早期探针误标签。下游请勿据此构造 18A 字典；
│   │   #    权威板块字典见 dict/hs_industry_blocks.json（恒生 112 末级）。
│   └── wind/
├── dict/                                 # 板块字典（块 ID → 成员）
│   ├── README.md
│   ├── hs_industry_blocks.json           # 恒生 112 末级
│   ├── hk_concept_blocks.json            # 港股概念 223
│   ├── ths_global_industry_blocks.json   # 同花顺全球 163
│   ├── sw_industry_blocks.json           # 申万 346
│   └── *_index.csv                       # 配套人类可读索引
└── derived/                              # 派生数据（清洗后产出，可被脚本读取）
    ├── _archive/                         # 已归档历史文件（不再被读取）
    │   ├── ipo_d30_returns.csv           #   键名 thscode，与 stock_code 不统一；DB.ipo_returns 已替代
    │   ├── nacs_v7.csv                   #   5/7 版，多含 03296.HK（5/9 已去重）；新版见 scores/
    │   └── nacs_yearly_aff.csv           #   5/7 版，269 行，与 DB 口径冲突
    ├── ipo_classification/               # 行业/概念分类
    │   ├── README.md
    │   ├── ipo_concepts.csv              # 1:N 长表（DB 权威导出）
    │   ├── ipo_industries.csv            # 1:N 长表（DB 权威导出）
    │   ├── *_wide.csv                    # 1:1 宽表对照
    │   ├── concept_coverage.csv
    │   └── industry_coverage.csv
    ├── verification/                     # DB 字段对 iFinD 一致性校验
    │   ├── README.md
    │   ├── chapter_report_20260509.csv
    │   ├── chapter_mismatch_20260509.csv
    │   ├── industry_report_20260509.csv
    │   └── industry_mismatch_20260509.csv
    ├── peer_ic/                          # 同业 Peer 信号 IC 探索
    │   ├── README.md
    │   ├── ic_results_20260509.csv       # 756 维度组合
    │   ├── ic_top50_20260509.csv
    │   ├── ic_robustness_20260509.csv
    │   └── ic_top_signals_detail_20260509.csv
    ├── scores/                           # NACS 模型评分
    │   ├── README.md
    │   └── nacs_v7_scores_20260509.csv
    └── backtest/                         # 回测产物
        ├── README.md
        ├── latest/                       # 最新回测 IC 摘要
        └── iterations/                   # 历次迭代快照（5 个）
```

## DB 表清单（数据真相）

| 表 | 行数 | 说明 |
|---|---|---|
| `ipo_master` | 384 | IPO 主表（公司、章节、定价、上市日、行业 gics_l2） |
| `ipo_returns` | 384 | 收益（d1/d30/m3/m6/m12 + unlock_d30/d90 + max_drawdown_m6 + avg_daily_volume_hkd） |
| `ipo_financials` | 1588 | 财务年度数据 |
| `ipo_concepts` | 321 | IPO×概念（1:N） |
| `ipo_industries` | 592 | IPO×（sw 或 ths_global）行业（1:N，源标注） |
| `ipo_cornerstone_link` | 1609 | IPO×基石投资人（1:N） |
| `cornerstone_master` | 1311 | 基石投资人主表 |
| `cornerstone_aliases` | 1051 | 基石别名映射 |
| `cornerstone_performance_asof` | 31464 | 基石历史业绩快照 |
| `market_environment_cache` | 54 | 市场环境缓存 |
| `db_metadata` | 1 | 元数据（schema 版本等） |
| `price_history` | 0 | 待补 |
| `sponsor_performance_asof` | 0 | 待补 |

## 数据流向

```
data/raw/ifind/*.csv     ──┐
data/raw/wind/*.csv      ──┤  (人工/脚本入库)
                            ▼
                       data/nacs_real.db                ← 真相之源
                            │
            ┌───────────────┼───────────────┐
            ▼               ▼               ▼
       run_v7_backtest  scripts/explore  scripts/verify
            │               │               │
            ▼               ▼               ▼
   derived/scores/    derived/peer_ic/  derived/verification/
   derived/backtest/                    derived/ipo_classification/
                                        dict/
```

## 上游脚本映射

| 派生文件 | 上游脚本 | 触发频率 |
|---|---|---|
| `dict/hs_industry_blocks.json` | `scripts/verify_industry_via_ifind.py` | 季度刷新 |
| `dict/hk_concept_blocks.json` | `scripts/fetch_hk_concepts.py` | 季度刷新 |
| `dict/{sw,ths_global}_industry_blocks.json` | `scripts/fetch_hk_industries.py` | 季度刷新 |
| `derived/ipo_classification/ipo_concepts.csv` | DB `ipo_concepts` 表导出 | 跟随字典 |
| `derived/ipo_classification/ipo_industries.csv` | DB `ipo_industries` 表导出 | 跟随字典 |
| `derived/verification/chapter_*.csv` | `scripts/verify_listing_chapter.py` | 每次 ingest 后 |
| `derived/verification/industry_*.csv` | `scripts/verify_industry_via_ifind.py` | 每次 ingest 后 |
| `derived/peer_ic/ic_*.csv` | `scripts/explore_peer_industry_concept_ic.py` + `explore_peer_ic_robustness.py` | 模型迭代时 |
| `derived/scores/nacs_v7_scores_*.csv` | `run_v7_backtest.py` | 模型迭代时 |
| `derived/backtest/latest/*.json` | `run_v7_backtest.py` | 模型迭代时 |

## 编码约定

| 路径 | 编码 | 行尾 |
|---|---|---|
| `raw/**/*.csv` | UTF-8 with BOM | CRLF（保留原貌） |
| `dict/*.csv` | UTF-8 无 BOM | LF |
| `derived/**/*.csv` | UTF-8 无 BOM | LF |
| `*.json` | UTF-8 | — |

## 数据日期

本次会话沉淀的派生数据均带 `_20260509` 后缀，对应 2026-05-09 的拉取/计算结果。

## 严格原则

1. **不得直接读 `outputs/`** — 仅作脚本临时输出，过期即删
2. **不得修改 `raw/`** — 这是审计追溯的根
3. **DB 是唯一真相源** — `derived/` 中的 csv 与 DB 不一致时以 DB 为准，重新导出
4. **新增派生数据须落 `derived/<类目>/` + 写 README.md**
5. **历史快照保留** — 文件名加 `_YYYYMMDD` 后缀，不要覆盖
6. **派生废弃文件移入 `derived/_archive/`** — 不要直接删除，便于追溯口径迁移
