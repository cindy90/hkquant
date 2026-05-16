# ADR 0006: Phase 1 ORM 范围决策 — v1.0 基础表本期落地，v1.1/v1.2/v1.2.1 表延后

- **Status**: Accepted
- **Date**: 2026-05-16
- **Deciders**: project lead
- **Supersedes**: 无
- **Superseded by**: 无

## Context

PROJECT_SPEC.md §4 Phase 1 列了：

> `data/models/* SQLAlchemy ORM`
> Alembic 初始 migration

但 §5 SQL schema 包含 25 张表，分为 4 个版本切片：

- **v1.0 基础**（IPO core + cornerstone + sponsor + comparable + prospectus + company）
- **v1.1**（prediction_snapshots + outcomes + post_ipo_events + reviews + config_versions）
- **v1.2**（ipo_lifecycle_states + state_transitions + code_mappings + scheduler_runs + alerts + earnings_comparisons）
- **v1.2.1**（user_accounts + user_roles + audit_logs + chat_sessions + chat_messages + whatif_calculations + realtime_events + api_rate_limit_state）

且 §2 目录树中 `data/models/` 只列了 7 个文件（`base.py`, `ipo.py`, `cornerstone.py`, `comparable.py`, `sponsor.py`, `prospectus.py`, `company.py`），对应 v1.0 切片。v1.1/v1.2/v1.2.1 的 ORM 文件归属未定。

如果在 Phase 1 一次性写完所有 25 张表的 ORM，会出现 4 个问题：
1. Phase 1 单测覆盖范围爆炸（>40 张表 × CRUD + relationship test）
2. v1.1 的 immutable trigger（`prevent_snapshot_modification()`）+ v1.2 状态机约束 + v1.2.1 RBAC 关联，离对应业务模块太远，缺乏上下文校验
3. Phase 7 / 7.5 实施时反而要回头补 ORM 测试
4. Alembic 初始 migration 巨大且语义模糊

## Decision

**Phase 1 只落地 v1.0 基础 7 个 ORM 文件**（与 spec §2 文件清单一致）：

- `data/models/base.py` — `Base = DeclarativeBase`；mixins：`UUIDMixin`（`id: UUID PK` + uuid7/uuid4 默认）/ `TimestampMixin`（`created_at`, `updated_at`）/ `AsOfMixin`（`as_of_date` 用于回测样本）
- `data/models/ipo.py` — `IPOEvent`, `IPOPricing`, `IPOAllocation`, `IPOPostMarket`
- `data/models/cornerstone.py` — `CornerstoneInvestor`, `CornerstoneInvestment`
- `data/models/comparable.py` — `ComparableCompany`
- `data/models/sponsor.py` — `Sponsor`, `SponsorRecord`
- `data/models/prospectus.py` — `ProspectusDoc`, `ProspectusExtraction`
- `data/models/company.py` — `Company`, `FinancialSnapshot`

**Alembic 初始 migration** 由 `alembic revision --autogenerate -m "phase1_v10_base_tables"` 生成，包含上述全部表 + 索引 + 外键。

**延后到对应 Phase 的 ORM 文件**：

| 表切片 | 计划新文件 | 实施 Phase | 新 migration 名 |
|---|---|---|---|
| v1.1 prediction registry | `data/models/prediction.py` | Phase 7.5 | `phase75_v11_prediction_registry` |
| v1.2 lifecycle + scheduler + alert | `data/models/lifecycle.py` + `data/models/operations.py` | Phase 7.5 | `phase75_v12_lifecycle_scheduler` |
| v1.2.1 user + audit + chat + whatif | `data/models/auth.py` + `data/models/chat.py` + `data/models/audit.py` | Phase 7 | `phase7_v121_ui_support` |

**Pydantic 模型不延后**：`common/schemas.py` 在 Phase 1 必须包含 §6 的全部模型（含 v1.1/v1.2/v1.2.1 切片），因为它们是跨 Phase 的接口契约——业务代码 Phase 4-6 可能 import `PredictionSnapshot` 等模型作类型注解，即使尚未持久化。

## Consequences

### Positive
- Phase 1 单测可在 1-2 天内做到 ≥80% 覆盖（约 12 张表 × CRUD）
- v1.1 的 immutable trigger 与 prediction_registry 业务逻辑同时验证（Phase 7.5）
- v1.2.1 的 RBAC ORM 关联与 auth middleware 同时验证（Phase 7）
- 每个 Phase 的 Alembic migration 边界清晰，rollback 单元独立

### Negative
- **跨 Phase 数据完整性风险**：如果 Phase 1-6 业务代码意外 import 了 v1.1+ 表（而其 ORM 尚未存在），会运行时报错
  - **Mitigation**：`common/schemas.py` 完整给出 Pydantic 模型可用于类型注解；任何想 INSERT 到 v1.1+ 表的代码必须等到对应 Phase
  - **Mitigation**：Phase 6 编排图末尾的 `create_snapshot` 节点是 Phase 7.5 的责任，Phase 6 实施时 stub 抛 NotImplementedError 即可

### Neutral
- 旧 NACS `panel_snapshots` 表在 Phase 7.5 被 `prediction_snapshots` 取代（见 ADR 0005 §1）；ORM 落地时间一致

## Progress

- [ ] **Phase 1**：v1.0 基础 7 个 ORM 文件 + Alembic 初始 migration
- [ ] **Phase 7**：v1.2.1 UI 支撑 3 个 ORM 文件（auth/chat/audit）+ migration
- [ ] **Phase 7.5**：v1.1 + v1.2 ORM 文件（prediction/lifecycle/operations）+ migrations + immutable triggers
