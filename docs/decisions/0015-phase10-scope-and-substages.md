# ADR 0015: Phase 10 范围 + 3 子阶段切片

- **Status**: Accepted
- **Date**: 2026-05-17
- **Deciders**: project lead
- **Supersedes**: 无
- **Superseded by**: 无

## Context

PROJECT_SPEC.md §3.12 + §4 Phase 10 deliverables（v1.1 新增）：把
系统从"一次性预测器"升级到"自我校准的决策系统"。所有 config /
prompt 调整必须走 `propose → review → apply` 闭环，**严禁系统自动应
用**（CLAUDE.md prediction-lifecycle 约束的核心）。

七大模块（spec §3.12 + Phase 1 已 stub）：

1. `drift_detector.py` — CUSUM + PSI 双指标，滑动窗口 20/50；输出
   `DriftSignal[]`
2. `attribution_aggregator.py` — 跨样本归因汇总，找系统性偏差
3. `counterfactual.py` — "听 Bear 会怎样" / "单一估值模型" 反事实
4. `adjustment_proposer.py` — drift + finding + counterfactual →
   `ProposedAdjustment[]` 写入 `prediction_reviews`
5. `adjustment_applier.py` — 强制 reviewer 字段非空 + status=accepted
   才能 apply；自动 bump version + 触发 5-IPO 小回测验证 + rollback
   失败
6. `version_manager.py` — config / prompt 版本历史（git tag 或
   versioned 文件）
7. `reports.py` — 月度学习报告（markdown）

外加 spec §4 Phase 10 要求：
- 跑 Phase 8 374-sample 全量回测做 drift 检测 + 首轮 proposal
- **至少 1 轮完整 propose → review → apply → re-backtest 闭环**
- `scripts/run_learning_cycle.py` 一键月度学习
- `docs/LEARNING_PROTOCOL.md` 写明可自动 vs 必须人工的调整

参考 ADR 0011 / 0012 / 0013 / 0014 模式，本 ADR 锁定 **3 个子阶段**。

## Decision

**Phase 10 切成 3 子阶段 10a → 10b → 10c，每个子阶段独立 commit +
测试 + 停下来等用户确认；最后子阶段完结后打 tag `v1.0`（项目
1.0 release）。**

切片原则：
- **10a 是"诊断层"**：drift / attribution / counterfactual /
  version_manager — 全部只读 + 写入 `prediction_reviews`
  attribution 字段（没有 system mutation），低风险先做
- **10b 是"动作层"**：proposer 写 proposals 到
  `prediction_reviews.proposed_adjustments`，applier 强制 human
  gate；这是 prediction-lifecycle 的核心实现，单独 review
- **10c 是收尾**：CLI + LEARNING_PROTOCOL doc + e2e propose-accept-
  apply-rebacktest 闭环 + tag `v1.0`

---

### Phase 10a — 诊断层（~1.5 天）

**范围**：

| 子项 | 文件 | 关键功能 |
|---|---|---|
| **DriftDetector** | `learning_loop/drift_detector.py` | CUSUM (mean-shift detection) + PSI (population stability index); 滑动窗口（默认最近 20 完成 6m checkpoint 的预测）; 切片按 ListingType / RegulatoryRegime; 输出 `DriftSignal[]`; 阈值在 `DriftDetectorConfig` 中可调 |
| **AttributionAggregator** | `learning_loop/attribution_aggregator.py` | 聚合 `prediction_reviews.attribution_details`; 找 (agent_role × listing_type) 频次最高的 attribution; 输出 `AggregatedFinding[]` |
| **Counterfactual** | `learning_loop/counterfactual.py` | "若听 Bear" — 用 `prediction_snapshots.debate_output` 重计算决策准确率; "若用单一估值" — 用 `valuation_output.single_models` 算 hit rate; 输出 `CounterfactualReport`; 不修改任何状态 |
| **VersionManager** | `learning_loop/version_manager.py` | 维护 `config_versions` PG 表（已在 7.5a 落地，Phase 8c stub）; `bump_version(target_path, new_content) → ConfigVersion`; `get_active_version(target_path)`; `rollback(target_path, version_id)` |

**DONE 条件**：
- DriftDetector 在 mock 时序数据上正确识别 CUSUM 突变
- AttributionAggregator 对样本 reviews 聚合出 top-3 primary_attribution
- Counterfactual 对一个 BacktestRun 跑出 if-bear / if-comparable-only
- VersionManager round-trip：bump → get_active → rollback
- ~20 新单测 + 0 regression

---

### Phase 10b — 动作层（~1 天）

**范围**：

| 子项 | 文件 | 关键功能 |
|---|---|---|
| **AdjustmentProposer** | `learning_loop/adjustment_proposer.py` | 输入：DriftSignal[] + AggregatedFinding[] + CounterfactualReport; 输出：`ProposedAdjustment[]` (schema 已在 §3.12 + common/schemas); 启发式映射：(drift signal type, finding) → AdjustmentType 推荐; 写入 `prediction_reviews.proposed_adjustments` (JSONB) — 不直接写 config |
| **AdjustmentApplier** | `learning_loop/adjustment_applier.py` | **强制门**：apply(proposal_id) 必须 reviewer 字段非空 + status=accepted，否则抛 `AdjustmentNotApprovedError`; 流程: 1) verify accepted, 2) version_manager.bump_version 当前 target, 3) 修改目标文件 (write yaml / prompt), 4) 触发 5-IPO 小回测 (run_walk_forward on sample subset), 5) 比较新旧 metrics, 6) 标 `adjustment_status=implemented` + `applied_version`; 任何 step 失败 → rollback + `adjustment_status=rejected` + 写 audit reason |
| **Reports** | `learning_loop/reports.py` | 月度报告 markdown: 近 30/60/90d 准确率 + Drift summary + 待批准 proposals 列表 + 已 apply adjustments 事后效果; `reports/learning/{YYYY-MM}_learning_report.md` |

**DONE 条件**：
- AdjustmentProposer 把 DriftSignal[] 转成 ProposedAdjustment[] 并入库
- AdjustmentApplier **对抗测试**：无 reviewer / status=proposed 时拒绝 apply (抛 AdjustmentNotApprovedError)
- AdjustmentApplier happy path: status=accepted + reviewer 非空 → bump version + apply
- Rollback 测试: 小回测显示 IC 显著退化 → 自动 rollback + 标 rejected
- 月度 reports.py 渲染 5 sections 完整
- ~15 新单测 + 0 regression

---

### Phase 10c — CLI + e2e loop + tag v1.0（~0.5 天）

**范围**：

| 子项 | 文件 | 关键功能 |
|---|---|---|
| **CLI** | `scripts/run_learning_cycle.py` | 一键月度学习: 1) load 374-sample BacktestRun, 2) run_drift_detector, 3) run_attribution_aggregator, 4) run_counterfactual, 5) propose adjustments, 6) write monthly report; 不自动 apply (必须人工 review CLI 跑 `scripts/review_proposals.py`) |
| **e2e 闭环测试** | `tests/e2e/test_learning_cycle.py` | 完整 propose → human-mock-accept → apply (含小回测 + rollback) → 验证 metrics; 用 V8LiteScorer 跑 5 IPO 小子集，避免 LLM 成本 |
| **`docs/LEARNING_PROTOCOL.md`** | 文档 | 写明：哪些 AdjustmentType 可自动 propose（weight_change, threshold_change）vs 必须人工设计（prompt_edit, logic_change）；review SLO；rollback 策略 |
| **`scripts/review_proposals.py`** | 简易人工 review CLI | List PROPOSED proposals + interactive accept/reject + reviewer 字段; 桥接 Phase 7.5b 的 proposals router (api/routers/proposals.py) |
| **CHANGELOG v1.0** | 文档 | release notes — 项目 1.0 release |
| **Tag** | `v1.0` | `git tag -a v1.0` — 项目主要里程碑 |

**DONE 条件**：
- run_learning_cycle CLI 跑 374-sample → 写月度报告 + 入库 proposals
- e2e 闭环测试通过: propose → accept (mock reviewer) → apply → rebacktest pass / rollback fail
- LEARNING_PROTOCOL.md ≤ 80 行
- scripts/review_proposals.py CLI 可列 / accept / reject proposals
- 全仓单测 0 regression + 新增 ~10 单测 + 1 e2e
- tag `v1.0` 打上

---

## Consequences

### Positive
- **3 子阶段 ~3 天**与 spec 估时一致
- **10a 全只读**：诊断层不会污染任何 config；单独 review 风险低
- **10b 强制 human gate**：是 prediction-lifecycle 约束的实装；
  对抗测试明确划线
- **10c 闭环实证**：propose-accept-apply-rebacktest 的全链路验证
- **不开新 DB 表**：复用 7.5a 已落地的 `config_versions` + 
  `prediction_reviews.proposed_adjustments` JSONB

### Negative
- **CUSUM 算法是新依赖** — 用纯 numpy 实现（已是 dep），不引入
  额外包
- **5-IPO 小回测验证可能 SLO miss** — V8LiteScorer 0.8ms/IPO，
  5 个 4ms，远小于 1s
- **真实 LLM 调用回测在 10c 中是 mocked** — 真实闭环要用户在自己
  环境跑（同 9c 案例）

### Neutral
- 子阶段命名沿用 10a/b/c
- Tag `v1.0` 标志项目主要里程碑（spec 全部 11 phases 完成）
- Phase 11+ 不在 spec 内；学习闭环长期运转是 ops 工作

## Progress

- [x] **现在**: 本 ADR 0015 写就
- [x] **Phase 10a (~1.5d)** (40596f5): drift_detector (CUSUM+PSI 4 sub-detectors) + attribution_aggregator (overall+listing+agent slicing) + counterfactual (if_bear + if_single_model) + version_manager (PG-backed bump/get/list/rollback) + 42 新单测
- [x] **Phase 10b (~1d)** (4e20118): adjustment_proposer (DriftSignal→AdjustmentType heuristic mapping + persist_to_review) + adjustment_applier (**强制 human gate** + 5-IPO sanity backtest + rollback on regression) + reports (6-section markdown) + 32 新单测
- [x] **Phase 10c (~0.5d)**: run_learning_cycle CLI + scripts/review_proposals.py + LEARNING_PROTOCOL.md (≤80 行) + e2e 闭环测试（propose+accept+apply happy / regression rollback / human gate） + CHANGELOG v1.0 + tag `v1.0`
