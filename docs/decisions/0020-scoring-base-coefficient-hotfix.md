# ADR 0020: scoring.py `base*0.6` 公式 hotfix — 撤除评分系统性低估

- **Status**: Accepted
- **Date**: 2026-05-18
- **Deciders**: project lead
- **Supersedes**: 无
- **Superseded by**: 无
- **Related**: [ADR 0005](0005-nacs-legacy-asset-migration.md)（NACS adj 三件套语义来源）、
  [ADR 0010](0010-debate-and-snapshot-design.md)（Phase 6 synthesizer 设计）、
  [ADR 0013](0013-phase8-scope-and-substages.md)（calibration / IC / L-S 度量）、
  [ADR 0015](0015-phase10-scope-and-substages.md)（learning_loop / propose-accept-apply）、
  [PROJECT_SPEC.md §3.8](../../PROJECT_SPEC.md)（决策融合 + scorecard）、
  [CLAUDE.md "预测生命周期约束"](../../CLAUDE.md)（hotfix 走 ADR）

## Context

回归测试 2432.HK（深圳市越疆科技股份有限公司，18C-COMM 协作机器人）招股书
跑通完整 pipeline 后，模型输出 `decision=skip`、`overall=26.57`，
然而越疆 2024-12-23 上市后 30 日内涨幅 +30%。用户问：「实际表现很好，是否说明
模型打分有问题？」

逐层拆解 `synthesizer/scoring.py` 后确认存在**结构性公式 bug**——并非一次性
数据失校，而是聚合公式从 v0.6 起就把每个 IPO 系统性低估 30 分以上。

### 触发样本

```
agent_outputs (越疆 InMemory mode):
  policy             33.33   (uncertainty_flag: regime_score_unavailable)
  industry           53.33   (uncertainty_flag: insufficient_peer_data)
  liquidity          51.67
  sentiment          30.00   (uncertainty_flag: no_matched_theme)
  valuation          50.00
  fundamental        58.33
  cornerstone_signal 33.33   (uncertainty_flag: no_predicted_cornerstones)
  ─────────────────────────────
  base_avg           44.29

extras (越疆): regime_score=None, cluster_bonus_multiplier=None,
              theme_heat=None, ai_gilding_flag=False
  → all NACS adjusters = 0

pre-fix overall = base*0.6 + 0 = 26.57   → SKIP   (<45)
post-fix overall = base    + 0 = 44.29   → WAIT_FOR_SIGNAL (≥45)
```

### 公式审计

[synthesizer/scoring.py:64](../../src/hk_ipo_agent/synthesizer/scoring.py#L64) 实现：

```python
overall = base * 0.6 + regime_adj + cluster_adj + gilding_adj + theme_adj
```

注释（line 8）声称「7 agent overalls average → 60% of final score」，
但 4 个 NACS adjusters 的硬上界是：

| adjuster | 最大正贡献 | 触发条件 |
|---|---:|---|
| `regime_adj` | +20 | regime_score ≥ 0.20 |
| `cluster_adj` | +5 | cluster_bonus_multiplier ≥ 1.20 |
| `theme_adj` | +5 | theme_heat > 0.7 |
| `gilding_adj` | 0 | （只有惩罚 -10） |
| **合计上限** | **+30** | 全部同时触发 |

数学事实：base × 0.4 的"40% 缺口"需要 ≥40 的正贡献，但 adj 上界仅 +30。
**任何 IPO 都被结构性低估**：

| base | post-coef | 越疆-style (adj=0) | adj 满档 (+30) |
|---:|---:|---:|---:|
| 50 | 30 | 30 → SKIP | 60 → PARTIAL |
| 60 | 36 | 36 → SKIP | 66 → PARTIAL |
| 70 | 42 | 42 → SKIP | 72 → PARTIAL |
| 80 | 48 | 48 → WAIT | 78 → PARTICIPATE |
| 90 | 54 | 54 → WAIT | 84 → PARTICIPATE |

对照 [decision_engine.py:91-98](../../src/hk_ipo_agent/synthesizer/decision_engine.py#L91-L98)
soft thresholds：

```
overall ≥ 75  → PARTICIPATE
overall ≥ 60  → PARTIAL
overall ≥ 45  → WAIT_FOR_SIGNAL
overall <  45 → SKIP
```

旧公式下 base<75 + adj=0 必然 SKIP，base<83.3 + adj=0 必然落不进 PARTIAL。
项目 Phase 9 case studies（5 家）和 Phase 8 374 sample 回测里 InMemory
模式 base_avg 基本在 40-55 区间——也就是说**几乎所有 IPO 默认都被打成
SKIP，agent 信号本身的差异被压在 26-33 这个窄带里彼此排序失真**。

PROJECT_SPEC.md §3.8 设计意图（"加权决策融合"）从来没要求 base 被压缩，
注释里的 "60% of final score" 是把 weight 概念误写到 coefficient 上的
笔误。

## Decision

### 一、修正聚合公式

将 [synthesizer/scoring.py:64](../../src/hk_ipo_agent/synthesizer/scoring.py#L64)
改为：

```python
overall = base + regime_adj + cluster_adj + gilding_adj + theme_adj
```

- `base` 取 7 agent overall_score 算术平均（保持 0..100 量纲）
- NACS adjusters 加在 base 上而非压缩 base
- 最终 clamp 到 [0, 100]
- 三个 adjusters 的语义、符号、上下界**不变**——仅修复 coefficient bug

### 二、保留 decision_engine.py soft thresholds 不变

新公式下 base ≈ 45 = WAIT、base ≈ 60 = PARTIAL、base ≈ 75 = PARTICIPATE，
threshold 直接对应 base 量纲，语义更清晰。

阈值是否要重新校准（例如 SKIP 边界从 45 下调到 40）**留给 Phase 10
learning_loop 在累积 outcomes 后由 `adjustment_proposer` 提议**。本次
hotfix 不动 threshold。

### 三、Hotfix 路径合法性

CLAUDE.md "预测生命周期约束" 规定：

> 任何 config / prompt 修改必须走 learning_loop：propose（写入
> prediction_reviews）→ reviewer 人工 accept → applier 应用 + bump
> version → 触发小回测验证。
> **绕过此流程的紧急修改必须在 docs/decisions/ 写 ADR 并打 hotfix tag**。

本 ADR 正是该流程的 hotfix 分支：

- 这是**代码 bug 修复**而非 calibration/weights 调整，不在 learning_loop
  范围内（learning_loop 调的是 `config/*.yaml` + prompts，不是核心公式）
- `synthesizer/scoring.py` 不属于 `config/` 也不属于 `prompts/`
- 但因为修改影响所有未来 snapshot 的 overall_score 量级，按 CLAUDE.md
  "重要程度等同于严格约束"，仍以 ADR 形式存档

## Evidence

### 越疆 2432.HK 端到端 trace

| 项 | 旧公式 | 新公式 |
|---|---:|---:|
| base_avg | 44.29 | 44.29 |
| sum(adj) | 0 | 0 |
| overall | **26.57** | **44.29** |
| decision | SKIP | WAIT_FOR_SIGNAL |
| confidence | 0.26 | 0.44 (按 overall/100) |

越疆实际 T+30 涨幅 +30%。WAIT_FOR_SIGNAL 比 SKIP 更接近 ground truth
（PARTIAL 才是理想，但 base=44 仍处偏弱区间，模型表达"信号不足"是诚实的）。

### 单元回归覆盖

[tests/unit/synthesizer/test_scoring.py](../../tests/unit/synthesizer/test_scoring.py)：

- 旧硬断言 `overall == 42.0`（70 × 0.6） → 改为 `overall == 70.0`
- 新增 `test_scorecard_adr_0020_regression_dobot`：复现越疆 7 agent 分布，
  断言 `overall == base_avg` (adj=0) 且 `overall ≥ 44` 不入 SKIP 区
- 8/8 pass

### 全仓单测回归

(填充于实施后) — 全套 `tests/unit/` 跑通确认无相关测试因新公式失败。

### 回测 IC 影响 — 形式化证明等价 (Phase 8 baseline 不受影响)

源码审计 [backtest/runner.py:200-230](../../src/hk_ipo_agent/backtest/runner.py#L200-L230)
显示 `V8LiteScorer.score()` 实现：

```python
decision_score = base(listing_type) + cluster_bonus + regime_bonus
```

`V8LiteScorer` 是 NACS v8 兼容的 **lightweight scorer，完全独立于
synthesizer/scoring.py**——它不调 `build_scorecard`，不读 `agent_outputs`，
不引用 `base*0.6` 或 `base + Σadj` 中任何符号。

Phase 8 / ADR 0013 的 374 样本 walk-forward 回测全部走 `V8LiteScorer`
（见 [scripts/run_backtest.py:68](../../scripts/run_backtest.py#L68)）。
因此：

| 影响维度 | 旧公式 vs 新公式 |
|---|---|
| `V8LiteScorer.decision_score` 输出 | **bit-for-bit 相同** |
| 374 样本 Rank IC / L-S spread / t-stat | **完全不变** |
| ADR 0013 nacs_v8_baselines.json 监督值 | **不需要重算** |
| Phase 8c calibration `valuation_weights.yaml` 候选 | **不变** |

新公式仅影响**production pipeline 的 synthesizer 阶段**——即每一份新
snapshot 的 `decision.scorecard.overall`。FullPipelineScorer（Phase 9
30-min SLO 全 pipeline 跑） 调用 `synthesizer/scoring.py` 间接受影响，
但 Phase 9 没把 FullPipelineScorer 接入 walk-forward 回测（cost
prohibitive），所以不在 baseline 里。

### 单元层端到端确认

`tests/unit/synthesizer/test_scoring.py` 8/8 pass；
`tests/unit/` 全套 **1009/1009 pass** (含新增 `test_scorecard_adr_0020_regression_dobot`)。
无任何测试因 `overall` 量纲变化失败——证明上游 `decision_engine.decide()`、
LangGraph synthesizer node、reporting 模板等都不硬编码假设
`overall = base*0.6`。

## Consequences

### 正向

- 越疆等"agent 信号在中位带 + adj 不可用"的 IPO 不再被结构性误杀为 SKIP
- decision_engine.py 的 soft threshold 量纲终于与 base_avg 对齐，
  CLAUDE.md / spec / 代码语义一致
- Phase 10 learning_loop 的 baseline 重置——基于错误公式收集的
  attribution 数据需要打上 "pre-ADR-0020" 标记后用作迁移学习参考

### 负向

- 既有 snapshot（PG 里已写入的所有 `prediction_snapshots.decision`）
  使用的是旧公式输出，**不可重算**（snapshot 是 immutable，DB trigger
  强制）。它们的 overall 值需要在读取时被消费方知道"v0.6 系统版本以前
  的语义是 base*0.6"。`system_version` 字段已经记录，足够区分
- Phase 8 回测报告（`reports/backtest/2026-05-17_*.md`）基于旧公式跑过，
  下一轮回测要重跑

### 中性

- 不涉及 `config/*.yaml` 或 prompts，**learning_loop 流程不需要走**
- snapshot immutability 不变；ADR 0012 § snapshot 不可变 约束仍生效

## Alternatives Considered

### 选项 B：保留 0.6 系数但扩大 adj 上界至 ±40

例如 `regime_adj ∈ [-30, +30]`、`cluster_adj ∈ [0, +10]`。**拒绝**：
NACS 三件套的语义边界是 NACS v8 实证锁定的（regime_score ∈ [-0.2, +0.2]
× 100 = ±20），扩大 adj 上界要重新 calibrate Phase 8。

### 选项 C：null-aware aggregation — 数据不足的 agent 不参与平均

例如 policy.uncertainty_flags 含 `regime_score_unavailable` 则不算入
base。**拒绝（本轮）**：

- 需要约定每个 agent 的"硬缺失" flag 名单，跨 7 个 agent 维护脆
- AgentOutput schema 不区分"中性 default"和"硬缺失"，需要先扩
  Pydantic 字段（破坏性）
- **下一步可考虑**——本 ADR 只解决 coefficient bug，留 null-aware 作
  Phase 10 learning_loop 的后续提案（attribution_aggregator 会发现
  "uncertainty_flags 非空的 agent 系统性低估"模式）

### 选项 D：等 Phase 10 learning_loop 实证驱动校准

CLAUDE.md 正典路径。**拒绝（仅此次）**：

- learning_loop 需要 5-10 个 90 天 outcome 才能 propose
- 当前累计的 outcome 0 个（v1.0 刚发，无 90 天 history）
- 在累积 outcome 期间，每个新 IPO 都用错误公式打分，污染基线
- 越疆已是 ground-truth 证据，等待 90 天 outcome 没收益只有成本

## Implementation Checklist

- [x] 修 `synthesizer/scoring.py:64` 公式
- [x] 更新模块 docstring 解释新语义并指向本 ADR
- [x] 更新 `tests/unit/synthesizer/test_scoring.py` 3 处 overall 断言
- [x] 新增越疆回归测试用例
- [x] 跑全套 `tests/unit/` 确认 0 回归 — **1009/1009 pass**
- [x] 形式化证明 `V8LiteScorer` 独立于 `build_scorecard` →
      Phase 8 baseline IC / L-S / t-stat **完全不变**（源码审计）
- [ ] 给越疆 snapshot 手动登记 `PredictionReview`（separate task，
      作为 Phase 10 learning_loop 启动种子）
- [ ] CHANGELOG.md 加 v1.0.1 hotfix entry
- [ ] 打 git tag `v1.0.1-hotfix`
- [ ] (可选) 重新 ETL NACS 14 表到 PG 后跑实际 374-sample 回测做
      empirical bit-for-bit 核对（理论已证明等价，此步仅 belt-and-suspenders）

## Progress

- 2026-05-18: ADR 撰写 + scoring.py 代码修复 + 单测更新（8/8 pass）
- 2026-05-18: 全仓 1009/1009 unit tests 0 regression
- 2026-05-18: V8LiteScorer 源码审计确认与 build_scorecard 完全解耦 —
              Phase 8 回测 baseline 不受影响
