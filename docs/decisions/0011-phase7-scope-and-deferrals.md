# ADR 0011: Phase 7 范围 + 延期项

- **Status**: Accepted
- **Date**: 2026-05-16
- **Deciders**: project lead
- **Supersedes**: 无
- **Superseded by**: 无

## Context

PROJECT_SPEC.md §16（v1.2.1 新增）+ Phase 7 deliverables 列出了 ~50 个新模块：

- `reporting/` — Jinja2 模板 + matplotlib charts + PDF/DOCX exporters
- `api/main.py` + `openapi.py` + `schemas.py` + `dependencies.py`
- `api/middleware/` 6 个（CORS / request_id / rate_limit / error / cost / audit）
- `api/auth/` 6 个（JWT / RBAC / SSO / dependencies / audit_middleware）
- `api/routers/` 18 个（health/dashboard/ipos/snapshots/analysis/prospectus/whatif/alerts/audit/chat/auth/backtest/drift/proposals/reviews/settings/system + ...）
- `api/streaming/` 4 个（SSE bus + types + endpoint）
- `api/websocket/` 4 个（chat manager + handler + endpoint）
- 7 张新 DB 表（users / roles / audit_log / chat_sessions / chat_messages / whatif_calculations / realtime_events）
- Phase 7.5 才完整的 lifecycle 字段（reviews / proposals / drift）

按 CLAUDE.md "函数 ≤ 50 行 / 文件 ≤ 500 行" 约束 + 实证 Phase 5/6 节奏，全量实现需 5-7 天，远超 spec 估的 3-4 天。需要明确 MVP 范围。

## Decision

**采用 Phase 7 MVP + 延期项**。MVP 实现所有 spec 强制 deliverables 中"UI 端能基本跑通工作流"必需项；其余推到 Phase 7.5 / 8 / 9。

### Phase 7 MVP — 必须落地

| 模块组 | 包含 | 关键功能 |
|---|---|---|
| **`reporting/`** | charts.py / report_builder.py / templates/*.j2 / exporters/{pdf,docx}.py | Jinja2 渲染 investment_memo + matplotlib chart + WeasyPrint PDF + python-docx |
| **`api/main.py`** | FastAPI app + lifespan + 中间件挂载 + OpenAPI 自定义 | uvicorn 可直接启动 |
| **`api/schemas.py`** | API 层 Request/Response Pydantic（独立于 common/schemas.py，避免泄漏内部模型） | UI 用 `openapi-typescript` 可生成完整 TS 类型 |
| **`api/middleware/`** | cors / request_id / rate_limit / error_handler / cost_guard | RFC 7807 Problem Details 错误格式 |
| **`api/auth/`** | jwt.py / rbac.py / dependencies.py (`require_role` / `require_permission`) + audit_middleware.py | **SSO providers (OKTA/AzureAD/Google) 延期到 Phase 9**；MVP 仅本地 JWT |
| **`api/routers/`** 10 核心 | health / dashboard / ipos / snapshots / analysis / prospectus / whatif / alerts / audit / chat | 其他 8 个 router (reviews / proposals / drift / backtest / settings / system / auth / ...) 由 Phase 7.5 / 8 落地，**Phase 7 MVP 留 stub 文件 + TODO** |
| **`api/streaming/`** | event_bus (in-memory) + event_types + sse_endpoint | DB-backed event store + Redis Pub/Sub 延期 Phase 7.5 |
| **`api/websocket/`** | chat manager (in-memory) + chat_endpoint | 消息持久化到 in-memory store；DB schema 延期 Phase 7.5 |
| **What-If** | `synthesizer/whatif.py` + `/api/whatif/run` endpoint | 调用 `valuation/run_ensemble` 子集 + 修改的 MarketData |
| **Prospectus PDF 服务** | `/api/ipos/{ipo_id}/prospectus/pdf` 返回本地 PDF | 签名 URL + S3 / R2 延期 Phase 9 |

### Phase 7 必须 deliverables 的实施约束

1. **Auth/Audit/Chat/WhatIf 数据存储**：与 Phase 6 snapshot 同形态 — **in-memory store**（参考 `prediction_registry/registry.py` 模式）。Phase 7.5 替换为 PostgreSQL + DB trigger
2. **OpenAPI 3.1 schema 必须 100% 完整**：UI 端 `pnpm run generate-api-types` 必须成功 — 这是 v1.2.1 强约束（CLAUDE.md 已列）
3. **RBAC 6 角色 + 权限矩阵**：复用 `common/enums.py` 已定义的 `UserRole` + `Permission` + `ROLE_PERMISSIONS`（Phase 1 已完成）
4. **CORS 严控**：dev 允许 `localhost:3000`；生产从 `Settings.api.cors_origins` 读

### 明确延期项 — Phase 7 不做

| 延期项 | 推到 | 理由 |
|---|---|---|
| SSO 真实接入（OKTA / AzureAD / Google） | Phase 9 | 需要真实租户配置；MVP 本地 JWT 已能让 UI 走完 login flow |
| `api/routers/{reviews,proposals,drift}.py` | Phase 7.5 | 依赖 lifecycle 表（v1.1/v1.2 schema），与 outcome_tracker / attribution / review_workflow 强耦合 |
| `api/routers/backtest.py` | Phase 8 | backtest runner 还没实装 |
| `api/routers/{system,settings,auth}.py` | Phase 9 e2e | 主要是配置 CRUD + login；MVP 留 stub |
| DB-backed audit_log / users / chat / whatif | Phase 7.5 | 同 Phase 6 snapshot 策略；in-memory 先把 graph 跑通 |
| 真实 PDF 渲染（WeasyPrint with CSS） | Phase 7 MVP 保留，但仅基础排版；进阶图表 / 嵌入式公式 Phase 9 |
| Redis Pub/Sub 多 worker 事件总线 | Phase 7.5 | 同 in-memory snapshot 策略 |
| 招股书签名 URL / 对象存储 | Phase 9 | MVP 走本地路径 |

### Phase 7 测试约束

- 每个 router 必须有 happy-path + error-path 测试（最少 2 个 case）
- OpenAPI schema 必须能成功导出（`/openapi.json` 200 OK）
- DONE-condition smoke：UI 模拟流程 `POST /analysis → GET /snapshots/{id} → POST /whatif → GET /reports/{snapshot_id}/pdf` 端到端

## Consequences

### Positive
- **3-4 天可完成**：MVP 范围与 spec 估时对齐
- **UI 可立即接入**：核心 10 router + SSE + WS 满足 PROJECT_SPEC_UI.md v1.3 工作台首屏需求
- **OpenAPI 3.1 完整**：v1.2.1 强约束达成
- **延期路径清晰**：每个 stub router 写明 Phase 编号，Phase 7.5 / 8 / 9 启动时直接接力
- **In-memory 模式 Phase 6 已验证**：Phase 7 沿用风险低；Phase 7.5 统一替换 PG

### Negative
- **UI 端 reviews/proposals/drift 页面 Phase 7 不可用**：UI 团队需先专注 dashboard / snapshots / chat / whatif
  - **Mitigation**：本 ADR 同步标记到 PROJECT_SPEC_UI.md（如果需要）
- **SSO 延期意味着 MVP 安全等级低**：本地 JWT 仅适合内网 demo
  - **Mitigation**：CLAUDE.md "数据安全" 已说 API key 走 env；Phase 7 MVP 默认仅 `127.0.0.1` 监听
- **In-memory chat history Phase 7 重启即丢**
  - **Mitigation**：UI 端必须显式提示用户"对话历史 Phase 7.5 起持久化"；Phase 7 dev 用 in-memory 不影响 demo

### Neutral
- Phase 7 在 spec 估的 3-4 天和 v1.2.1 扩展的 "Phase 7 扩展为 API + 报告 + UI 集成层" 之间取折中：实现"核心 10 router + 全套中间件 + SSE/WS 骨架"
- Phase 7.5 / 8 / 9 启动时本 ADR 的"延期项"清单是接力 checklist

## Progress

- [x] **现在**: 本 ADR 0011 写就
- [x] **Phase 7 (2026-05-16)**: `reporting/` charts + report_builder + pdf/docx exporters + investment_memo template v1.0
- [x] **Phase 7 (2026-05-16)**: `api/main.py` + `openapi.py` (3.1 + BearerAuth) + `schemas.py` + `dependencies.py`
- [x] **Phase 7 (2026-05-16)**: `api/middleware/` 5 模块 (CORS + request_id + rate_limit + error_handler + cost_guard)
- [x] **Phase 7 (2026-05-16)**: `api/auth/` 4 模块 (JWT + RBAC + dependencies + audit_middleware)，SSO 留 Phase 9 stub
- [x] **Phase 7 (2026-05-16)**: `api/routers/` 11 核心实装 + 6 stub (501)
- [x] **Phase 7 (2026-05-16)**: `api/streaming/` 4 模块 (event_types + in-memory event_bus + connection_manager + sse_endpoint)
- [x] **Phase 7 (2026-05-16)**: `api/websocket/` 4 模块 (in-memory chat store + chat_handler + chat_endpoint)
- [x] **Phase 7 (2026-05-16)**: `synthesizer/whatif.py` + POST /api/whatif/run
- [x] **Phase 7 (2026-05-16)**: 测试 27 新单测 + 449 全仓单测通过；commit + v0.7 tag
- [x] **Phase 7.5b (2026-05-16)**: 替换 in-memory audit/users/chat/whatif 为 PG + DB trigger — 全 5 项完成：
  - 7.5b-2: PGAuditStore + AuditStoreProtocol + set_audit_store（audit_logs DB trigger 由 7.5a migration 落地）
  - 7.5b-3: PGChatStore + ChatStoreProtocol + set_chat_store (chat_sessions + chat_messages + ON DELETE CASCADE)
  - 7.5b-3: EventBus(session_factory=...) PG hook + set_event_bus (best-effort INSERT realtime_events，broadcast 失败不阻塞)
  - 7.5b-3: whatif endpoint INSERT whatif_calculations (best-effort，FK violation 时 warning + 仍返回 200)
  - 7.5b-3: get_user_by_id_pg + resolve_user PG lookup (in-memory 默认，PG 作 production fallback)
- [x] **Phase 7.5b (2026-05-16)**: 实装 reviews / proposals / drift routers（3 router 实装 + 11 新单测；ADR 0012 §7.5b 已勾）
- [x] **Phase 8d**: 实装 backtest router（list runs + detail by run_id + count meta；6 单测含 PG-seeded happy + 404 + auth + OpenAPI；存储复用 prediction_snapshots.config_snapshot.backtest_run_id，不开新表）
- [ ] **Phase 9**: SSO providers + 签名 URL + Redis Pub/Sub
