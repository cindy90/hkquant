# ADR 0005: NACS v8 遗产资产迁移到 spec v1.2.1 的完整映射

- **Status**: Accepted
- **Date**: 2026-05-16
- **Deciders**: project lead
- **Supersedes**: 无
- **Superseded by**: 无

## Context

PROJECT_SPEC.md v1.2.1 定义的是从零搭建的多 Agent LLM 决策系统，没有提到 NACS v8（当前仓库已有的量化评分模型）。在 Phase 0 启动会上，用户明确决策：

1. NACS v8 代码**完全废弃**，不作为 spec 内任何子模块复用
2. NACS 已积累的**数据资产 + 4 年实证 know-how** 可以部分保留，转生到 spec 的对应模块

如果不把这份转生地图明确记录在 ADR 并交叉引用到目标 stub 文件，Phase 1+ 的实施者（包括未来的我）将无从知晓哪些"现成的东西"可以继承，会导致重复造轮子或更糟——遗忘掉 v7/v8 已经实证有效的领域知识（Regime Gate 60d IC=+0.247, t=2.41）。

本 ADR 把这份映射固化，并要求 Phase 2 / Phase 5 / Phase 8 实施时必须主动查阅并兑现。

## Decision

按下表执行 NACS 资产到 spec 模块的迁移。**每一行都必须在对应 Phase 完成时勾选**（在本 ADR 末尾 Progress 段维护）。

---

### 1. 数据库：SQLite → PostgreSQL（Phase 2 执行）

迁移脚本：`scripts/migrate_sqlite_to_pg.py`（Phase 2 新建，本 ADR 完成时建空 stub）

| SQLite 旧表 | 行数 | PostgreSQL 新表 | 字段映射要点 | 迁移负责模块 |
|---|---:|---|---|---|
| `ipo_master` | 384 | `ipo_events` | stock_code, company_name_zh/en, listing_type, industry_code, sponsor_ids, a1_filing_date, hearing_date, pricing_date, listing_date, issue_size_hkd | `data/builders/historical_ipo_loader.py` |
| `ipo_master`（定价子集） | 384 | `ipo_pricings` | price_range_low/high, final_price, intl_oversubscription, retail_oversubscription, margin_subscription_multiple, allocation_mechanism, final_public_allocation_pct | 同上 |
| `ipo_returns` | 384 | `ipo_postmarket` | day1_return, day5_return, day22_return, day126_return, day127_return, day252_return, max_drawdown_d126, cornerstone_held_after_lockup | 同上 |
| `ipo_financials` | 1,588 | 嵌入 `prospectus_extractions.extraction` JSONB（或独立 `financial_snapshots` 表，视 Phase 1 schema 决定） | 年度营收 / 毛利率 / ROE / FCF / R&D 等 | `data/builders/historical_ipo_loader.py` |
| `cornerstone_master` | 1,314 | `cornerstone_investors` | name_zh, name_en, category, parent_org, home_country；NACS 的 ultimate_holder → 新增 `parent_org` 字段 | `data/builders/cornerstone_profile_builder.py` |
| `cornerstone_aliases` | 1,051 | 合并到 `cornerstone_investors.aliases` JSONB（或独立 alias 表） | 80% 别名覆盖率必须保留 | 同上 |
| `ipo_cornerstone_link` | 1,604 | `cornerstone_investments` | ipo_id, investor_id, commitment_amount_hkd, pct_of_offering, lockup_months, disclosure_date, is_anchor | 同上 |
| `cornerstone_performance_asof` | 31,464 | **重新计算**（不迁移；属于 derived data） | Phase 7.5 由 `prediction_registry/outcome_tracker.py` + `benchmarks.py` 重新跑出 | 不迁移 |
| `market_environment_cache` | 55 | `backtest/regime_detection` 的 fixture（JSON 或 parquet） | 月度 HSI 收益/波动/南向资金；作为 Phase 8 回测的初始市场环境训练集 | `backtest/regime_detection.py` |
| `panel_snapshots` | n/a | **不迁移** | 已被 `prediction_snapshots`（v1.1，DB trigger immutable）取代 | — |

**迁移前置条件**：备份 `data/nacs_real.db.bak_<timestamp>`；迁移脚本必须幂等（用 PostgreSQL 唯一约束防重）。

**迁移后保留期**：SQLite 文件保留至 Phase 8 回测通过验证（Phase 9 结束后归档到 `legacy/`）。

---

### 2. 量化信号 → Agent / Valuation 子模块（Phase 4-5 执行）

NACS 的三个核心量化信号都已经过 4 年回测实证，不应在新系统里重新摸索。

| NACS 信号 | 实证效果 | 转生位置 | 集成方式 |
|---|---|---|---|
| **Regime Gate** | regime_score < 0 → SKIP 全部 IPO；过滤后子样本 60d IC=+0.247, t=+2.41 | (a) `agents/policy_agent.py`：作为 `regime_fit` 评分维度 (b) `valuation/ensemble.py`：作为 ensemble 后置调整乘子（regime<0 → 截断决策为 SKIP） | Policy Agent 必须输出 `regime_score: float` 字段；ensemble post-processor 必须检查此字段 |
| **Cluster Bonus** | 同 ultimate_holder ≥2 个基石 → ×1.10/1.15/1.20；cluster≥2 IPO 60d mean +22% (vs 无关联 +14%)，std ↓40% | `agents/cornerstone_signal_agent.py`：作为 `predicted_cornerstone_strength` 评分维度 | Agent 必须查 `cornerstone_profile_builder` 的 ultimate_holder 聚类输出 |
| **Theme Heat + AI Gilding** | heat_today.json 当日热度 0-100；AI 镀金检测：AI 收入 <10% → ×0.85 | `agents/sentiment_agent.py`：作为 `market_temperature` + `narrative_risk` 评分维度 | Agent 必须读取 `themes/heat_today.json` + `themes/premium_curve.json` + `themes/ai_revenue_manual.json` |

**注意**：上述都是**评分输入**，最终决策由 Synthesizer (Opus) 综合，不是简单乘法叠加。

---

### 3. 回测基础设施（Phase 8 执行）

| NACS 资产 | 转生位置 | 备注 |
|---|---|---|
| Rank IC / L-S spread / t-stat 三件套 | `backtest/metrics.py` | 直接照搬指标定义；v8 实证基线作为单调性约束 |
| 5 轮迭代存档（`data/derived/backtest/iterations/p1_10` → `p2_2`） | `backtest/calibration.py` 的初始 baseline | 校准时新参数应至少不显著差于 v8 |
| `run_v7_backtest.py` walk-forward 逻辑 | `backtest/runner.py` 的实现参考 | 防泄漏机制（pricing_date 前才可用）必须保留 |
| `panel_snapshots` 表 | **不迁移** | 由 `prediction_snapshots` 取代 |

---

### 4. 测试遗产（Phase 1-2 执行）

NACS 87 单元测试中，绝大多数耦合 NACS 模型代码，无法迁移。但**数据质量 / 防泄漏**那部分逻辑必须保留：

| 旧测试文件 | 迁移决策 | 新位置 |
|---|---|---|
| `tests/test_no_lookahead.py` | **逻辑必须保留** — 这是数据质量底线 | `tests/unit/data/test_no_lookahead.py`（Phase 2 重写为 PostgreSQL 版本） |
| `tests/test_dao.py`（数据访问层） | **逻辑迁移** — repository 模式可借鉴 | `tests/unit/data/test_repositories.py`（Phase 1-2 重写） |
| `tests/test_etl.py` | **逻辑迁移** — ETL 幂等性 / 数据质量评分 | `tests/unit/data/test_etl.py`（Phase 2 重写） |
| `tests/test_smoke.py` | 部分迁移 — schema 完整性检查 | `tests/integration/test_db_repositories.py` |
| `tests/test_config.py` | **不迁移** — NACS 专属 yaml 校验 | 由 `tests/unit/common/test_settings.py` 取代 |
| `tests/test_nacs_model.py` | **不迁移** — 整个评分模型废弃 | — |
| `tests/test_parallel.py` | **不迁移** | — |
| `tests/test_resolve.py` | **不迁移** | — |

`tests/unit/data/` 目录由本 ADR 触发创建（含 `.gitkeep`）。

---

### 5. Theme 系统的处理

`themes/` 目录下的资源：

| 文件 | 内容 | 新位置 / 处理 |
|---|---|---|
| `themes/theme_definitions.json` | AI / 半导体 / 新能源等主题定义 + 核心公司 | **保留** — 作为 Sentiment Agent / Industry Agent 的主题分类器输入；新位置 `data/knowledge_base/themes/theme_definitions.json`（Phase 2 由 builder 复制） |
| `themes/heat_today.json` | 当日主题热度（每天 cron 更新） | **保留** — Sentiment Agent 数据源；保留 cron 脚本 `themes/theme_tracker.py`（迁移到 `scripts/update_theme_heat.py`） |
| `themes/premium_curve.json` | 主题估值溢价曲线（季度更新） | **保留** — Valuation Agent 的可比公司溢价调整因子 |
| `themes/ai_revenue_manual.json` | AI 收入占比手工标注 | **保留** — Sentiment Agent 的 AI 镀金检测器输入 |
| `themes/history.csv` | 30d 主题热度趋势 | **保留** — Sentiment Agent 趋势 sparkline 数据源 |
| `themes/research_premium_coefficient.py` | 主题溢价系数研究脚本 | **保留**（季度运行） — 输出更新 `premium_curve.json` |

新位置约定：Phase 2 时把 `themes/` 内容拷贝到 `data/knowledge_base/themes/` 并由 `data/builders/theme_loader.py`（Phase 2 新增）维护。旧 `themes/` 目录在 Phase 9 归档。

---

## Consequences

### Positive
- Phase 2 启动时 ETL 脚本不需要从零设计；本 ADR 已经把表映射列死
- Phase 5 三个 agent（policy / cornerstone_signal / sentiment）启动时直接知道有现成数据可读，prompts 也明确把这些当输入
- Phase 8 回测启动时不需要重新选择指标，IC / L-S / t-stat 已经过 4 年实证
- 4 年累积的领域知识不丢失，决策质量起点更高
- 防泄漏测试逻辑迁移避免重新踩坑

### Negative
- 跨 Phase 的隐式耦合：本 ADR 没被遵守时，Phase 5 实施者可能不知道有 NACS 遗产可用
  - **Mitigation**：本 ADR 在 CLAUDE.md "当前重构上下文" 段被引用；所有目标 stub 文件 docstring 都交叉引用本 ADR
- NACS 数据质量并非完美（部分财务字段缺失、个别基石分类待复核）；迁移后必须在 Phase 2 跑数据质量审计
  - **Mitigation**：`tests/unit/data/test_data_quality.py` 必须覆盖迁移后数据，质量阈值不达标的字段 fallback iFind 重拉

### Neutral
- `cornerstone_performance_asof` 31k 缓存被丢弃重算；好处是新计算用 Phase 7.5 的标准化 benchmark；坏处是初次重算耗时
- `panel_snapshots` 整表丢弃；用 `prediction_snapshots`（immutable）取代——更严格但需 Phase 7.5 才能用，过渡期回测复现性靠 git tag + config_versions

---

## Cross-References (Module-Level Reverse Index)

下列 stub 文件的 docstring 都必须包含 `See ADR 0005` 引用：

| 文件 | 引用的 ADR 0005 内容 |
|---|---|
| `src/hk_ipo_agent/data/builders/historical_ipo_loader.py` | §1（385 IPO + 财务 ETL） |
| `src/hk_ipo_agent/data/builders/cornerstone_profile_builder.py` | §1（1,314 基石 + 别名）+ §2 Cluster Bonus 数据基础 |
| `src/hk_ipo_agent/agents/policy_agent.py` | §2 Regime Gate |
| `src/hk_ipo_agent/agents/cornerstone_signal_agent.py` | §2 Cluster Bonus |
| `src/hk_ipo_agent/agents/sentiment_agent.py` | §2 Theme Heat + AI Gilding + §5 themes 文件清单 |
| `src/hk_ipo_agent/valuation/ensemble.py` | §2 Regime Gate 作为 post-adjustment |
| `src/hk_ipo_agent/backtest/metrics.py` | §3 IC / L-S / t-stat |
| `src/hk_ipo_agent/backtest/calibration.py` | §3 v8 迭代基线 |
| `src/hk_ipo_agent/backtest/regime_detection.py` | §1（market_environment_cache）+ §3 |
| `prompts/agents/policy.md` | §2 Regime Gate（输入字段） |
| `prompts/agents/cornerstone_signal.md` | §2 Cluster Bonus（输入字段） |
| `prompts/agents/sentiment.md` | §2 Theme Heat + AI Gilding（输入字段） |
| `scripts/migrate_sqlite_to_pg.py` | §1 全部表映射（脚本主要任务） |
| `tests/unit/data/` | §4 测试遗产迁移清单 |

---

## Progress（Phase 完成时勾选）

- [ ] **Phase 2**：`scripts/migrate_sqlite_to_pg.py` 实现 §1 全部映射并通过幂等测试
- [ ] **Phase 2**：`data/builders/historical_ipo_loader.py` 优先从迁移后的 PG 表加载，缺字段才回退 iFind
- [ ] **Phase 2**：`data/builders/cornerstone_profile_builder.py` 复用 §1 中的 1,314 基石画像作为初始种子
- [ ] **Phase 2**：`data/builders/theme_loader.py` 把 `themes/` 拷贝到 `data/knowledge_base/themes/`
- [ ] **Phase 2**：`tests/unit/data/test_no_lookahead.py` 迁移完成
- [ ] **Phase 4**：`valuation/ensemble.py` 实现 Regime Gate post-adjustment（regime<0 → 截断 SKIP）
- [ ] **Phase 5**：`agents/policy_agent.py` + `prompts/agents/policy.md` 接入 Regime Gate
- [ ] **Phase 5**：`agents/cornerstone_signal_agent.py` + `prompts/agents/cornerstone_signal.md` 接入 Cluster Bonus
- [ ] **Phase 5**：`agents/sentiment_agent.py` + `prompts/agents/sentiment.md` 接入 Theme Heat + AI Gilding
- [ ] **Phase 8**：`backtest/metrics.py` 实现 IC / L-S / t-stat 三件套
- [ ] **Phase 8**：`backtest/calibration.py` 用 v8 迭代基线作为单调性约束
- [ ] **Phase 8**：`backtest/regime_detection.py` 用 market_environment_cache 作为初始训练集
- [ ] **Phase 9**：`themes/` 旧目录归档到 `legacy/`
- [ ] **Phase 9**：`data/nacs_real.db` 归档到 `legacy/`
- [ ] **Phase 9**：NACS 顶层脚本（`build_perf_cache.py` / `check_health.py` / `run_v7_backtest.py` / `nacs_checklist_tool.html`）归档到 `legacy/`
