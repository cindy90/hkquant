# NACS 数据字典 (Data Dictionary)

> 本文档描述 `data/nacs_real.db` (SQLite) 的全部表/字段语义、单位、缺失策略与上游来源。
> 与 `src/data/schema.py` 1:1 对齐, 任何 schema 变更必须同步本文件。
>
> 维护人: 模型 owner | 最后更新: 2026-05-09 (P1 数据字典首版)

---

## 0. 命名/类型约定

| 约定 | 说明 |
|---|---|
| `*_id` | 业务主键, 字符串, 跨表稳定 |
| `*_date` | ISO `YYYY-MM-DD` 字符串 (SQLite 无原生 DATE, 实际是 TEXT) |
| `*_hkd` / `*_usd` / `*_cny` | 金额, 单位写在后缀 |
| `*_pct` | 百分比 0-100 (如 `intl_oversub` 是倍数, 写法约定见各表) |
| `is_*` / `*_flag` | 0/1 整数代替 bool |
| 缺失值 | NULL (Python `None`); 上游 iFinD 的 `--/—/空字符串` 已在 `field_mappings.parse_*` 统一转 NULL |

**ID 编码规则**

| ID 类型 | 格式 | 例子 | 生成函数 |
|---|---|---|---|
| `ipo_id` | `HK_{stock_code_with_underscore}_{listing_year}` | `HK_1187_HK_2026` | `field_mappings.make_ipo_id` |
| `cornerstone_id` | `CS_{normalized_name}` | `CS_GIC_Private_Limited` / `CS_蓝思科技_香港_有限公司` | `field_mappings.make_cornerstone_id` |

---

## 1. cornerstone_master — 基石机构主表

> 一行 = 一个唯一基石机构 (合并多个别名后)

| 列 | 类型 | 必填 | 单位/范围 | 语义 | 缺失策略 | 来源 |
|---|---|---|---|---|---|---|
| `cornerstone_id` | TEXT PK | ✓ | — | 主键, 见 ID 编码规则 | — | 由 ETL 生成 |
| `canonical_name` | TEXT | ✓ | — | 规范化原文名 (招股书署名) | — | iFinD p05309_f005 |
| `name_zh` | TEXT |  | — | 中文名 (英文基石的中译, 可空) | NULL | 人工 / 翻译 |
| `cornerstone_type` | TEXT | ✓ | 8 档枚举 | 见 `CornerstoneType` Enum (sovereign_pension/global_long_only/top_hedge_preipo/cn_mutual_insurance/strategic_industrial/policy_fund/pe_vc_continuation/family_office_spv) | ETL 默认填 `family_office_spv` (最低分) | ETL 默认 + 人工 promote |
| `parent_entity` | TEXT |  | — | 母公司, 用于产业资本溯源 | NULL | 人工 |
| `country_of_origin` | TEXT |  | ISO-2 / 国家中文 | 注册地 | NULL | 人工 |
| `aum_usd_latest` | REAL |  | 美元亿/十亿 (按数值规模) | 最新 AUM | NULL | 人工 / 公开数据 |
| `aum_asof_date` | DATE |  | ISO date | AUM 数据日期 | NULL | — |
| `is_chinese` | INT 0/1 | ✓ | bool | 是否中资属性 (源自 `CHINESE_TYPES` 集合) | ETL 默认 0 | 由 type 派生 |
| `is_longterm` | INT 0/1 | ✓ | bool | 是否长线属性 (源自 `LONGTERM_TYPES` 集合) | ETL 默认 0 | 由 type 派生 |
| `notes` | TEXT |  | — | 自由文本备注 / 招股书简介 | NULL | iFinD p05309_f018 |
| `created_at` / `updated_at` | TIMESTAMP |  | ISO8601 | DB 写入/更新时间戳 | 自动 | 由 dao.upsert_cornerstone 写 |

**关键不变量**: ETL 重跑不会覆盖已存在记录的 `cornerstone_type` (保留人工 promote 标签)。

---

## 2. cornerstone_aliases — 别名表

> 一行 = 一个 (cornerstone_id, alias_text) 对; 用于把招股书原文映射到唯一 cornerstone_id

| 列 | 类型 | 必填 | 语义 | 备注 |
|---|---|---|---|---|
| `alias_id` | INT PK AUTO | ✓ | 自增 | — |
| `cornerstone_id` | TEXT FK | ✓ | 指向 cornerstone_master | ON DELETE 由调用方控制 |
| `alias_text` | TEXT | ✓ | 招股书或来源中的原文 | 保留大小写/空格 |
| `alias_text_lower` | TEXT | ✓ | `alias_text.strip().lower()` | 用于 case-insensitive 索引 |
| `alias_type` | TEXT | ✓ | `legal_name` / `chinese` / `english` / `spv` / `abbreviation` / `stock_code` / `prospectus` | ETL 写入 `prospectus` |
| `match_confidence` | REAL |  | [0, 1] | 别名可信度 (人工 1.0, 模糊匹配可 < 1.0) |

唯一索引: `(cornerstone_id, alias_text_lower)` — 同一基石的同一小写别名只存一条。

---

## 3. ipo_master — IPO 主表

> 一行 = 一只 IPO; PK = ipo_id

### 3.1 标识与基础信息

| 列 | 类型 | 必填 | 单位/范围 | 语义 | 缺失策略 | 来源 |
|---|---|---|---|---|---|---|
| `ipo_id` | TEXT PK | ✓ | — | 主键 | — | ETL |
| `stock_code` | TEXT | ✓ | iFinD 格式 (`1187.HK`) | 港股代码 | — | iFinD p05310_f001 |
| `company_name_zh` | TEXT |  | — | 公司中文名 | NULL | iFinD p05310_f002 |
| `company_name_en` | TEXT |  | — | 公司英文名 | NULL | 人工 / 公开 |
| `listing_date` | DATE | ✓ | ISO | 上市日 | — (无此字段则跳过) | iFinD p05310_f033 |
| `pricing_date` | DATE |  | ISO | 定价日 (基石协议签署点之后) | NULL | iFinD p05310_f032 |
| `listing_chapter` | TEXT | ✓ | 见下表 | 制度路径 | ETL 默认 `main_board` | iFinD blocks (待补) / 人工 |
| `is_a_h` | INT 0/1 |  | bool | 是否 A+H 同名 | ETL 默认 0 | iFinD blocks AH |
| `a_share_code` | TEXT |  | A 股代码 | 用于做空对冲 | NULL | 人工 |
| `gics_l2` | TEXT |  | GICS 二级 | 行业分类 | NULL | iFinD / 人工 |

**listing_chapter 枚举** (`ListingChapter` Enum):

| 值 | 含义 |
|---|---|
| `main_board` | 主板 (含已盈利/未盈利, 默认) |
| `18a` | 18A 生物医药 |
| `18c_commercial` | 18C 特专科技 (商业化档) |
| `18c_precommercial` | 18C 特专科技 (未商业化档) |
| `a_plus_h` | A+H 同名 |
| `spac` | SPAC 上市 |

### 3.2 发行结构

| 列 | 类型 | 单位 | 语义 | 来源 |
|---|---|---|---|---|
| `offer_price_hkd` | REAL | HKD/股 | 实际定价 | iFinD p05310_f010 |
| `offer_price_low` | REAL | HKD/股 | 招股价下限 | iFinD (待补) |
| `offer_price_high` | REAL | HKD/股 | 招股价上限 | iFinD p05310_f008 |
| `offering_size_hkd` | REAL | HKD | 募集总额 (含超配前) | iFinD p05310_f023 |
| `pricing_in_range` | REAL | [0, 1] | 定价位置: 0=下限, 0.5=中位, 1=上限 | 人工/派生 |
| `intl_oversub` | REAL | 倍 (e.g. 3.5 = 3.5x) | 国际配售超额认购倍数 | iFinD p05310_f052 |
| `public_oversub` | REAL | 倍 | 公开发售超额认购倍数 | iFinD p05310_f027 |
| `clawback_triggered` | INT 0/1 | bool | 是否触发回拨机制 | iFinD / 派生 |
| `greenshoe_pct` | REAL | [0, 0.15] 通常 | 绿鞋比例 | iFinD (待补) |
| `greenshoe_exercised` | INT 0/1 | bool | 绿鞋是否行使 | iFinD (待补) |

### 3.3 中介机构

| 列 | 单位 | 语义 | 来源 |
|---|---|---|---|
| `sponsor_primary` | TEXT | 主保荐人英文名 | iFinD / 招股书 |
| `sponsor_tier` | INT 1/2/3 | 保荐人画像 (见 `SponsorTier` Enum) | 人工映射 |
| `joint_sponsor_count` | INT | 联席保荐数 | iFinD / 招股书 |
| `auditor_tier` | INT 1/2/3 | 审计师等级 (Big4=1) | 人工映射 |

### 3.4 估值与基石聚合

| 列 | 单位 | 语义 | 备注 |
|---|---|---|---|
| `pe_at_offer` | 倍 | 发行 PE | 主板已盈利 IPO 必填 |
| `pe_peer_median` | 倍 | 同业 PE 中位数 | 用于 `pe_discount` 派生 |
| `last_round_premium` | 比 | Pre-IPO 最后一轮 vs 发行价溢价 (0.50 = +50%) | L1 否决: > 0.50 触发 |
| `cornerstone_total_hkd` | HKD | 基石认购总额 | 由 link 表聚合 |
| `cornerstone_coverage` | [0, 1] 小数 | 基石覆盖率 = total/offering_size | ⚠ ETL 把 iFinD 的 % 数 (35.76) 已转 0.3576 |
| `cornerstone_count` | INT | 基石个数 | 由 link 表聚合 |
| `lockup_months` | INT | 默认锁定期 (月), 整体 | 默认 6 |

### 3.5 反幸存者偏差 / 数据质量

| 列 | 单位 | 语义 |
|---|---|---|
| `is_delisted` | 0/1 | 截至 today 是否已退市 |
| `delisting_date` | DATE | 退市日 |
| `is_acquired` | 0/1 | 是否被收购退市 |
| `data_quality_score` | [0, 1] | 字段完整度评分 (1=全有, 0=全 NULL) |
| `data_source_notes` | TEXT | 数据来源串 (e.g. `ifind:p05310`) |
| `created_at` | TIMESTAMP | DB 写入时间 |

---

## 4. ipo_cornerstone_link — IPO × 基石 多对多

> 一行 = 一只 IPO 中某基石的认购信息

| 列 | 类型 | 单位 | 语义 | 来源 |
|---|---|---|---|---|
| `link_id` | INT PK AUTO | — | 自增 | — |
| `ipo_id` | TEXT FK | — | → ipo_master | ETL |
| `cornerstone_id` | TEXT FK | — | → cornerstone_master | ETL |
| `ticket_size_hkd` | REAL | HKD | 该基石认购额 | iFinD p05309_f008 |
| `allocation_shares` | INT | 股 | 该基石认购股数 | iFinD p05309_f009 |
| `lockup_months_actual` | INT | 月 | 实际锁定期 (可能与 ipo_master.lockup_months 不同) | iFinD p05309_f010 |
| `affiliation_flag` | 0/1 | bool | 是否关联方 (污染) | 人工/派生 |
| `affiliation_reason` | TEXT | — | 关联原因文本 | 人工 |
| `data_source` | TEXT | — | `prospectus` / `allocation_announcement` / `manual` / `ifind:p05309` | ETL/人工 |
| `is_estimated` | 0/1 | bool | ticket_size 是否为估计值 | ETL/人工 |
| `as_of_date` | DATE | ISO | 数据快照日期 (CURRENT_DATE) | 自动 |

唯一索引: `(ipo_id, cornerstone_id)` — 同 IPO 同基石只存一条; 多金额合并由 ETL 上游处理。

---

## 5. price_history — 日频价格历史

> 一行 = (ipo_id, trade_date) 唯一; 用于派生 `ipo_returns`

| 列 | 类型 | 单位 | 语义 |
|---|---|---|---|
| `ipo_id` | TEXT FK | — | → ipo_master |
| `trade_date` | DATE | ISO | 交易日 |
| `close_hkd` | REAL | HKD | 收盘 (前复权) |
| `volume` | REAL | 股 | 成交量 |
| `turnover_hkd` | REAL | HKD | 成交额 |
| `is_suspended` | 0/1 | bool | 是否停牌 |

PK: `(ipo_id, trade_date)`; 主索引: `trade_date`。

来源: iFinD / Yahoo Finance / 手填(早期)。

---

## 6. cornerstone_performance_asof — 基石画像快照 (派生表)

> 防 look-ahead 的核心: 给定 (cornerstone_id, as_of_date), 只用 listing_date < as_of_date 的样本计算

| 列 | 类型 | 单位 | 语义 | 计算来源 |
|---|---|---|---|---|
| `cornerstone_id` | TEXT | — | PK 1 | — |
| `as_of_date` | DATE | ISO | PK 2; 通常是回测调仓日 | — |
| `ipo_count_5y` | INT | — | 过去 5 年参与的 IPO 数 | dao.compute_cornerstone_perf_asof |
| `avg_m6_return_5y` | REAL | [-1, +∞] | 过去 5 年 IPO 6 月平均收益 | ipo_returns.return_m6 |
| `winrate_m6_5y` | REAL | [0, 1] | M+6 收益 > 0 的比例 | — |
| `avg_d30_return_5y` | REAL | — | 过去 5 年 D+30 平均收益 | ipo_returns.return_d30 |
| `lockup_discipline_score` | REAL | [0, 1] | 解禁后 30 天回撤越小越高 | clip 公式见 dao L218-225 |
| `sector_expertise` | TEXT | JSON | `{gics_l2: count}` 行业经验分布 | — |

PK: `(cornerstone_id, as_of_date)`; 物化函数: `dao.materialize_cornerstone_perf_snapshot(asof)`。

---

## 7. ipo_returns — IPO 收益快照 (派生表)

> 一行 = 一只 IPO; 派生自 price_history, 一次算清, 回测 join

| 列 | 单位 | 语义 |
|---|---|---|
| `ipo_id` | — | PK |
| `return_d1_close` | 比 | 上市首日收盘 / 发行价 - 1 |
| `return_d30` / `return_m3` / `return_m6` / `return_m12` | 比 | T+30/T+90/T+180/T+365 收盘 / 发行价 - 1 |
| `return_unlock_d30` / `return_unlock_d90` | 比 | 解禁日后 30/90 天收益 (overhang 测量) |
| `max_drawdown_m6` | 比 | 锁定期内最大回撤 (负数) |
| `avg_daily_volume_hkd` | HKD | 日均成交额 (流动性) |

构建脚本: `build_perf_cache.py`。

---

## 8. sponsor_performance_asof — 保荐人画像 (派生表)

> 同 cornerstone_performance_asof, 按保荐人聚合, 滚动 3 年

| 列 | 单位 | 语义 |
|---|---|---|
| `sponsor_name` | — | PK 1 |
| `as_of_date` | ISO | PK 2 |
| `ipo_count_3y` | INT | 过去 3 年保荐 IPO 数 |
| `avg_d30_return_3y` | 比 | 平均 D+30 收益 |
| `breakage_rate_3y` | [0, 1] | 破发率 (return_d1_close < 0 的比例) |
| `winrate_d30_3y` | [0, 1] | D+30 收益 > 0 的比例 |
| `pct_rank_winrate` / `pct_rank_breakage` / `pct_rank_avg_d30` | [0, 1] | 全市场百分位 |

---

## 9. db_metadata — 元数据键值对

| 键 | 当前值 | 用途 |
|---|---|---|
| `schema_version` | `1.0` | schema 版本号, 跨大版本迁移用 |

---

## 10. market_environment_cache — 市场环境快照 (按月)

| 列 | 单位 | 语义 | 来源 |
|---|---|---|---|
| `asof_month` | DATE | PK; 月初日期 (e.g. 2024-03-01) | — |
| `hsi_60d_return` | 比 | 恒指过去 60 日收益 | iFinD HKHSI |
| `hsi_60d_vol_annualized` | [0, +∞] | 恒指 60 日年化波动 | 派生 |
| `hsi_60d_vol_pct_rank` | [0, 1] | 60 日波动的历史百分位 | 派生 |
| `hsi_valuation_pct` | [0, 1] | 恒指 PE 历史百分位 | iFinD |
| `hk_ipo_30d_avg_d30` | 比 | 港股 IPO 过去 30 日 D+30 平均 | 派生 |
| `hk_ipo_30d_breakage_rate` | [0, 1] | 同期破发率 | 派生 |
| `southbound_30d_net_normalized` | 标准化 | 南向 30 日净流入 (z-score) | iFinD / probe_southbound JSON |
| `sector_60d_vol_annualized` | [0, +∞] | 行业 60 日年化波动 | iFinD |
| `source` | — | `ifind` / `json` / `fallback` | — |

---

## 附录 A: iFinD raw CSV → DB 字段映射

定义文件: [`src/data_sources/ifind/field_mappings.py`](../src/data_sources/ifind/field_mappings.py)

| 报表 | CSV 文件 | 主映射字典 | 目标表 |
|---|---|---|---|
| THS_DR p05309 (基石) | `ifind_cornerstones.csv` | `P05309_CORNERSTONES` (18 字段) | cornerstone_master + aliases + link |
| THS_DR p05310 (首发信息) | `ifind_ipo_info.csv` | `P05310_IPO_INFO` (16/54 字段) | ipo_master |
| THS_BD 财务年报 | `ifind_financials_annual.csv` | `FINANCIALS_ANNUAL` | (待加 ipo_financials 表) |
| THS_BD 股本 | `ifind_share_capital.csv` | `SHARE_CAPITAL` | (待加 ipo_share_capital 表) |
| THS_DataPool 板块 | `ifind_blocks.csv` | `BLOCKS` + `BLOCK_TO_CHAPTER` | ipo_master.listing_chapter (待修上游 dump 格式) |

**已知 iFinD 字段未确认含义** (TODO):

| 字段 | 当前映射 | 状态 |
|---|---|---|
| `p05309_f019` | `_unused_f019` | 样本均为 `--`, 含义未知 |
| `p05309_f013` | `hangseng_subindustry` | 待用客户端「数据浏览器」核对 |

---

## 附录 B: 缺失值/空值统一规则

定义在 [`field_mappings.NULL_TOKENS`](../src/data_sources/ifind/field_mappings.py):

```python
NULL_TOKENS = {"", "--", "—", "NULL", "NaN", "nan", "null", "None"}
```

所有 `parse_float / parse_int / parse_date / parse_str` 遇到上述 token 一律返回 `None`, 写库变 `NULL`。

---

## 附录 C: 章节折扣与后处理乘子

定义在 [`configs/nacs_v8.yaml::post_adjustments`](../configs/nacs_v8.yaml):

| 触发条件 | 乘子 | 说明 |
|---|---|---|
| `listing_chapter == 18c_commercial/precommercial` | × 0.70 | 18C 章节折扣 |
| `is_a_h == 1 and a_share_borrowable` | × 1.10 | A+H 可融券对冲加成 |
| `is_secondary_listing == 1` | × 0.85 | 第二上市折扣 |
| `related_party_tx_recent == 1` | × 0.85 | 控股股东近 12 月重大关联交易折扣 |

---

## 附录 D: 维护流程

1. 增删字段时, 同时改 `src/data/schema.py` + 本文件 + (如来自 iFinD) `field_mappings.py`
2. 升 `db_metadata.schema_version` (1.0 → 1.1)
3. 在 `tests/test_schema_dictionary.py` 加回归 (确保新字段有人工或 ETL 写入路径)
4. 大改时跑 `python check_health.py` + `python run_v7_backtest.py --config configs/nacs_v8.yaml` 验证 IC 不漂移
