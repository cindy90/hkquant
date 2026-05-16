# ADR 0007: IPOPostMarket 扩展 JSONB 字段与 CHECKPOINT_DAYS 对齐

- **Status**: Accepted
- **Date**: 2026-05-16
- **Deciders**: project lead
- **Supersedes**: 无
- **Superseded by**: 无

## Context

PROJECT_SPEC.md §5 `ipo_postmarket` 表只定义 6 个 checkpoint 标量列：

```
day1_return, day5_return, day22_return, day126_return, day127_return, day252_return
+ max_drawdown_d126 + cornerstone_held_after_lockup
```

而 §11 强制约束的 `CHECKPOINT_DAYS` 是 11 个：
`(1, 5, 10, 22, 30, 60, 90, 126, 180, 252, 360)`

**缺口**：`day10 / day30 / day60 / day90 / day180 / day360` 在 `ipo_postmarket` 中没有列。`day127` 不是 `CHECKPOINT_DAYS` 之一（它是 D126 后的"解禁日"，专门用于跟踪锁定期解除）。

如果只按 spec §5 实现 ORM，Phase 8 回测和 Phase 7.5 prediction 追踪都需要的中间 checkpoint（D10/D30/D60/D90/D180/D360）将无处存放，必须落到 `prediction_outcomes`。但 `prediction_outcomes`（Phase 7.5）只对**做过预测的 IPO** 创建，**纯历史样本**（未来 backtest 用的 NACS 385 IPO）没有 prediction_snapshots，也就没有 prediction_outcomes，从而中间 checkpoint 回报丢失。

## Decision

在 `ipo_postmarket` 表上**新增两个 JSONB 字段**（不删/不改 spec §5 既有列）：

1. **`returns_by_day JSONB`** — 形如 `{"1": "0.05", "5": "0.12", "10": "0.08", ...}`
   - 键：`CHECKPOINT_DAYS` 中的整数转字符串（JSONB 不接受 int key）
   - 值：Decimal 字符串（避免 JS 数字精度问题，与 spec §16 一致）
   - 完整覆盖 11 个 checkpoint + 可选 day127（lockup expiry）
2. **`cornerstone_held_pct_by_day JSONB`** — 同形状，存基石各 checkpoint 持仓占比

**保留** spec §5 的 6 个标量列：
- 作为 NACS 数据 ETL 的目标（ADR 0005 §1 的 `ipo_returns → ipo_postmarket` 映射保持原状）
- 作为常用查询的去归一化加速字段（`day1_return` / `day126_return` 是 Phase 8 IC 计算的高频字段）
- 与 `prediction_outcomes` 的 11 个 checkpoint 列（v1.1）形成分工：
  - `ipo_postmarket` = 实际历史回报（无论是否预测过）
  - `prediction_outcomes` = 预测后 + checkpoint 评估的归因数据

**写入约束**（ETL / scheduler 实施时必须遵守）：

| 数据源 | 写入字段 |
|---|---|
| NACS SQLite `ipo_returns` 迁移（ADR 0005 §1，Phase 2） | 6 个标量列；JSONB 字段为空 |
| iFind 实时拉取 / Phase 8 backtest 补齐 | 同时写 JSONB 全集 + 6 个标量列（双写保持一致） |
| Phase 7.5 outcome_tracker 写 `prediction_outcomes` | 不写 `ipo_postmarket`（不同表，不同语义） |

## Consequences

### Positive
- Spec §5 既有契约 100% 保留（兼容 NACS 迁移）
- 完整 CHECKPOINT_DAYS 覆盖（无回报数据丢失）
- 历史 IPO（无 prediction）也能用于 Phase 8 全 checkpoint 回测
- JSONB 弹性：未来新增 checkpoint（如 day504）无需 schema migration

### Negative
- 双写要求（同时维护标量 + JSONB）易出 drift
  - **Mitigation**：单元测试 `tests/unit/data/test_postmarket_consistency.py`（Phase 2 加）断言 ETL 写入后两边数据一致
- JSONB 查询比标量列慢
  - **Mitigation**：高频字段已是标量列；JSONB 仅用于 backtest 全样本扫描

### Neutral
- 与 `prediction_outcomes` 表字段重叠（两表都能存 D90 收益），但语义不同：
  - `prediction_outcomes.relative_return_industry` 是相对超额收益，存归因
  - `ipo_postmarket.returns_by_day["90"]` 是绝对收益，存历史
  
  两者由各自的 workflow 写入，不互相依赖。

## Progress

- [x] **Phase 1**：ORM 加 `returns_by_day` + `cornerstone_held_pct_by_day` 两个 JSONB 列，Alembic migration 重生
- [ ] **Phase 2**：`scripts/migrate_sqlite_to_pg.py` 写 6 个标量列（NACS 数据无 JSONB 全集）
- [ ] **Phase 2**：`tests/unit/data/test_postmarket_consistency.py` 双写一致性断言
- [ ] **Phase 7.5**：`outcome_tracker` 写 `prediction_outcomes`，**不**写 `ipo_postmarket`
- [ ] **Phase 8**：backtest 优先读 JSONB（全 checkpoint）；标量列作为加速 fallback
