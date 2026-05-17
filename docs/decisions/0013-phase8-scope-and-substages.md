# ADR 0013: Phase 8 范围 + 4 子阶段切片

- **Status**: Accepted
- **Date**: 2026-05-16
- **Deciders**: project lead
- **Supersedes**: 无
- **Superseded by**: 无

## Context

PROJECT_SPEC.md §3.9 + §4 Phase 8 deliverables + ADR 0005 §3 (强制) 列出
回测与校准的产物：

- `backtest/as_of_data.py` — ★★★ 防数据泄漏最关键文件（AsOfDataProvider）
- `backtest/runner.py` — Walk-forward 每个历史 IPO 跑完整 pipeline
- `backtest/regime_detection.py` — 规则切换点检测 + 市场 regime score；
  ADR 0005 §1 + §3 要求继承 NACS `market_environment_cache`（55 月度
  快照）
- `backtest/metrics.py` — IC / L-S / t-stat 三件套；ADR 0005 §3 要求
  直接照搬 NACS v8 定义 + 用 v8 实证基线作单调性约束
- `backtest/calibration.py` — 贝叶斯权重校准；ADR 0005 §3 要求用 5 轮
  v8 iteration archives（p1_10 → p2_2）作初始 baseline
- `backtest/reports.py` — 输出回测报告（markdown / PDF）
- 对 2022 至今所有港股科技/AH IPO 跑全量回测（50+ 样本）
- 校准后的 `valuation_weights.yaml` 通过 `learning_loop/version_manager`
  写回 config（NOT 直接 apply — CLAUDE.md prediction-lifecycle 约束）

**ADR 0011 遗留项**（Phase 8 收尾）：
- `api/routers/backtest.py` — Phase 7 stub (501) → 实装 GET runs /
  GET run by id

按 CLAUDE.md「函数 ≤ 50 行 / 文件 ≤ 500 行」+ Phase 7.5 实证节奏（每
子阶段 1.5-2 天），全量实施 ~5 天，比 spec 估时 4-5 天稍长。

参考 ADR 0011（Phase 7 MVP）+ ADR 0012（Phase 7.5 4 子阶段）模式，本
ADR 锁定 4 个子阶段的切片，保证每子阶段独立 commit + 测试 + 停下来
等用户确认。

## Decision

**Phase 8 切成 4 子阶段 8a → 8b → 8c → 8d，每个子阶段独立 commit +
测试 + 停下来等确认；最后一子阶段完结后打 tag `v0.8`。**

切片原则：
- **8a 先把防泄漏基础设施 + regime_detection 打牢**：as_of_data 是
  整个 backtest 的"地基"，错了所有指标都不可信；regime_detection
  与它共用 NACS 数据资产，一起做
- **8b 实现 metrics（IC / L-S / t-stat）+ NACS v8 baselines 加载**：
  纯计算 + 可用 fixture 数据测试，与 walk-forward 解耦
- **8c walk-forward runner + Bayesian calibration + reports + 50+
  样本回测**：依赖 8a 的 as_of_data + 8b 的 metrics；这是核心产出
- **8d backtest router 实装**（ADR 0011 最后一条遗留）+ tag `v0.8`

---

### Phase 8a — 防泄漏基础设施 + Regime Detection（~1.5 天）

**范围**：

| 子项 | 文件 / 模块 | 关键功能 |
|---|---|---|
| **AsOfDataProvider** | `backtest/as_of_data.py` | 所有数据访问必须经过此 provider；`as_of_date` 后的任何字段一律不可见。覆盖：财务（fiscal_year + period_end 严格按 ADR 0005 §4 规则）、市场行情、新闻、政策（regime change points 切换）、cornerstone disclosure。Phase 2 `tests/unit/data/test_no_lookahead.py` 5 测试套 + Phase 8 新增 backtest-specific 防泄漏断言 |
| **Regime detection** | `backtest/regime_detection.py` | 1) Regulatory change points 表 (2024-09-01 18C downward + 2025-08-04 IPO pricing 新规)；2) Market regime score = 过去 [t-120, t-30] 已上市 IPO 30d 收益中位数；3) `market_environment_cache` JSON fixture 加载（ADR 0005 §1：55 行 monthly HSI 收益/波动/南向资金）；4) `slice_by_regime(samples) → list[(regime_label, subsample)]` for runner.py |
| **NACS asset migration** | `data/fixtures/market_environment_cache.json` | 从 `data/nacs_real.db.market_environment_cache` 一次性导出为 JSON fixture；不入 PG（reference data，不是 source of truth）;Phase 2 ETL 脚本已经做了一半（看 ADR 0005 Progress），8a 收尾 |

**DONE 条件**：
- `as_of_data.py` 拒绝任何 `as_of_date` 之后的字段访问（pytest 对抗
  测试：尝试读未来 fiscal_year 必抛 `LookAheadError`）
- `regime_detection.py` 能加载 `market_environment_cache` JSON 并返回
  正确的 regime score for any anchor date
- `regulatory_regime_for(date)` 返回正确的 regime enum（pre_new_pricing
  / new_pricing 等）
- 全仓单测 0 regression + 新增 ~12 单测

---

### Phase 8b — IC / L-S / t-stat metrics + v8 baselines（~1 天）

**范围**：

| 子项 | 文件 / 模块 | 关键功能 |
|---|---|---|
| **三件套** | `backtest/metrics.py` | `rank_ic(predicted, realized) → IC`（Spearman 排序）、`ls_spread(samples, deciles=10) → spread%`、`t_stat(spread_series) → t`；按 regime / industry / listing_type 切片支持；返回结构化 `MetricsReport` dataclass |
| **NACS v8 baselines** | `data/fixtures/nacs_v8_baselines.json` | 从 `data/derived/backtest/iterations/p1_10..p2_2` 5 个 iteration archives 提取关键指标（each: IC + L-S + t-stat per slice）;`metrics.py` 提供 `monotonicity_constraint(new_metrics, baseline)` 校验新参数不显著差于 v8 |
| **基准比较** | `metrics.py:compare_to_baseline(...)` | 给 calibration.py 调用 - 返回是否通过约束 + 改善 / 退化幅度 |

**DONE 条件**：
- 三件套对 mock 历史预测产出 vs 已知收益 → IC / L-S / t-stat 结果与
  pen-paper 计算吻合（unit tests with fixture data）
- NACS v8 baseline JSON 完整 5 iterations 可加载
- `monotonicity_constraint` 拒绝显著退化（mock case：IC 跌 ≥ 0.05
  → returns False with reason）
- 新增 ~10 单测

---

### Phase 8c — Walk-forward Runner + Calibration + Reports + 全量回测（~2 天）

**范围**：

| 子项 | 文件 / 模块 | 关键功能 |
|---|---|---|
| **Walk-forward** | `backtest/runner.py` | 对每个历史 IPO（来自 `ipo_events` PG 表）：`as_of_date = pricing_date - 1`，构造 AsOfDataProvider → 跑完整 LangGraph pipeline → 比较预测 vs 实际首日/30/60/180/360 表现；写 `prediction_snapshots` + `prediction_outcomes`（复用 7.5a 表，免开新表）。**对抗测试：随机抽 5 个 IPO 验证 walk-forward 时不会读到 pricing_date 后字段**|
| **Bayesian calibration** | `backtest/calibration.py` | 输入：8b metrics + v8 baselines + 当前 `valuation_weights.yaml`；过程：贝叶斯优化（scikit-optimize 或自实现 Gaussian Process）；约束：sum-to-1 + monotonicity vs v8 + 样本量 ≥20/slice；输出：候选 `valuation_weights.yaml` 通过 `learning_loop/version_manager.bump_version()`（NOT 直接 apply）|
| **Reports** | `backtest/reports.py` | 生成 `reports/backtest/YYYY-MM-DD_{run_id}.md`：summary table（IC / L-S / t-stat per regime per listing_type）+ NACS v8 对比图 + top/bottom decile 案例；可选 PDF 导出（复用 `reporting/exporters/pdf.py`）|
| **全量回测** | CLI: `scripts/run_backtest.py` | 跑 2022 至今所有港股 IPO（PG 中 399 行）；预期 50+ 样本通过 regime gate 过滤；输出 markdown report + 候选 weights yaml |

**DONE 条件**：
- 全量回测 50+ 样本跑通；report 写出有意义的 IC / L-S / t-stat
- 候选 weights yaml 通过 monotonicity 约束（vs NACS v8 baseline）
- 防泄漏对抗：5 IPO 抽查均未读到 pricing_date 之后字段
- 新增 ~20 单测 + 1 integration test（`tests/integration/test_backtest_smoke.py`：mini-run 5 IPOs）

---

### Phase 8d — Backtest Router + tag v0.8（~0.5 天）

**范围**：

| 子项 | 文件 / 模块 | 关键功能 |
|---|---|---|
| **api/routers/backtest.py** | router 实装（ADR 0011 遗留最后一条） | GET `/api/backtest/runs` 列表（paginated）/ GET `/api/backtest/runs/{run_id}` 详情；RBAC `require_permission(Permission.READ_BACKTEST)` 或 READ_SETTINGS（看 enums.py 既有权限）|
| **DB schema** | 不开新表 — backtest run = `prediction_snapshots` 的一组 by run_id metadata | run_id 存在 `prediction_snapshots.config_snapshot["backtest_run_id"]` 字段；router 用 GROUP BY 聚合 |
| **CLAUDE.md / ADR 0011 / ADR 0012 / ADR 0013** 同步 | Phase 8 ✓ + ADR 0011 Progress backtest router 行勾上 | |

**DONE 条件**：
- backtest router 4 单测（happy + error + RBAC + OpenAPI schema 暴露）
- 全仓单测 0 regression
- 打 tag `v0.8`
- ADR 0011 Progress 完全收尾（所有遗留行勾完）
- ADR 0013 §Progress 4 行全部勾

---

## Consequences

### Positive
- **每子阶段 ~1.5-2 天**，与 Phase 5/6/7/7.5 节奏一致
- **8a 单独把防泄漏打牢**：as_of_data 是其他子阶段的依赖，单独 review 风险低
- **8b 纯 numerical 计算 + fixture 测试**：不依赖 PG，速度快
- **8c 集中跑全量回测 + 校准**：这是 Phase 8 的核心产出，单独 commit
- **8d 简短收尾**：router + tag，ADR 0011 Progress 100% 完结
- **不开新 DB 表**：backtest run 存为 `prediction_snapshots` group by `backtest_run_id` metadata，复用 7.5a 已落地 schema

### Negative
- **8c 工作量集中**：runner + calibration + reports + 全量回测一起，
  单 commit diff 大
  - **Mitigation**：3 个文件按职责分块，可分 review；50+ 样本 mini-run
    fixture 可单独验证
- **NACS 数据资产依赖**：8a 需要 `market_environment_cache.json`，
  8b 需要 NACS v8 baseline JSON
  - **Mitigation**：ADR 0005 §1 已规定一次性 export；如果 Phase 2 ETL
    没做完整，8a 第一步先补
- **calibration 用贝叶斯优化引入新依赖**（scikit-optimize 或 GPyOpt）
  - **Mitigation**：scikit-optimize 体积小，已是常见依赖；如不愿引入，
    回退到 grid search + monotonicity constraint

### Neutral
- 子阶段命名沿用 8a/b/c/d 而非 9.0/9.1/...
- backtest router 在 8d 而非 8c 是因 ADR 0011 已经把"backtest 延 Phase 8"
  写明（不属于 Phase 7 MVP）
- Phase 9 端到端验证不被本 ADR 影响：Phase 8 跑完 50+ 样本后，Phase 9
  用其中 3-5 家做 case study

## Progress

- [x] **现在**: 本 ADR 0013 写就
- [x] **Phase 8a (~1.5d)**: `backtest/as_of_data.py` + `regime_detection.py` + `data/fixtures/market_environment_cache.json` + 25 新单测 (commit `0b65ded`)
- [x] **Phase 8b (~1d)**: `backtest/metrics.py` (Rank IC / L-S spread Welch t-stat) + `data/fixtures/nacs_v8_baselines.json` (5 iterations) + `monotonicity_constraint` + `compare_to_baseline` + 25 新单测 (含 pen-paper IC / 退化 case 拒绝 / canonical p1_lockup_v2 self-pass)
- [ ] **Phase 8c (~2d)**: `backtest/runner.py` + `calibration.py` + `reports.py` + `scripts/run_backtest.py` + 50+ 样本全量回测 + 候选 weights yaml + ~20 新单测 + 1 integration test
- [ ] **Phase 8d (~0.5d)**: `api/routers/backtest.py` 实装（ADR 0011 最后遗留）+ tag `v0.8`
