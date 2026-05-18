# Post-v1.0 Hardening Plan

> 基于 2026-05-17 完整代码审查产出的修复路线图。覆盖 8 个子系统、24 个 Critical + 约 60 个 Major + 大量 Minor。沿用 CLAUDE.md 的 Phase 推进原则，切成 12 个修复 Phase（R0-R11），逐 Phase 暂停。
>
> **状态**：drafted 2026-05-17 — 详细审查报告见对话归档
> **关联 ADR**：见 §链接段尾
> **执行原则**：每个任务有稳定编号 `R<N>-<M>`，commit message 必须引用，例 `fix(dcf): R1-1 — terminal ΔWC coefficient cagr→g`

---

## 目录

- [0. 总体节奏](#0-总体节奏)
- [1. CLAUDE.md 严格约束违反清单（驱动 Phase 选择）](#1-claudemd-严格约束违反清单驱动-phase-选择)
- [2. Phase R0 — 工程基线 Blocker](#2-phase-r0--工程基线-blocker)
- [3. Phase R1 — 数值正确性硬 bug](#3-phase-r1--数值正确性硬-bug)
- [4. Phase R2 — 生产安全 + 不可变](#4-phase-r2--生产安全--不可变)
- [5. Phase R3 — 流程伪完成治理](#5-phase-r3--流程伪完成治理)
- [6. Phase R4 — 模型路由 + Jinja2 + ADR 治理](#6-phase-r4--模型路由--jinja2--adr-治理)
- [7. Phase R5 — async 一致性 + 类型 + ID 碰撞](#7-phase-r5--async-一致性--类型--id-碰撞)
- [8. Phase R6 — RBAC + Auth 加固 + 脱敏](#8-phase-r6--rbac--auth-加固--脱敏)
- [9. Phase R7 — 数据层修复](#9-phase-r7--数据层修复)
- [10. Phase R8 — 调度器 + 警报 + fixture fail-fast](#10-phase-r8--调度器--警报--fixture-fail-fast)
- [11. Phase R9 — 测试缺口补齐](#11-phase-r9--测试缺口补齐)
- [12. Phase R10 — scripts 大扫除 + docs + README](#12-phase-r10--scripts-大扫除--docs--readme)
- [13. Phase R11 — 结构性收尾](#13-phase-r11--结构性收尾)
- [14. 跨 Phase 全局守则](#14-跨-phase-全局守则)
- [15. 依赖图](#15-依赖图)
- [16. Progress 看板](#16-progress-看板)

---

## 0. 总体节奏

| Phase | 主题 | 工作量 | 出口 tag |
|---|---|---|---|
| R0 | 工程基线 3 Blocker | 0.5 天 | `v1.0.1-r0` |
| R1 | 数值正确性硬 bug | 1 天 | `v1.0.1-r1` |
| R2 | 生产安全 + 不可变 | 2-3 天 | `v1.0.2-r2` |
| R3 | 流程伪完成治理 | 3 天 | `v1.0.3-r3` |
| R4 | 模型路由 + Jinja2 + ADR | 2 天 | `v1.0.4-r4` |
| R5 | async 一致性 + 类型 + ID | 1.5 天 | `v1.0.5-r5` |
| R6 | RBAC + Auth 加固 + 脱敏 | 2 天 | `v1.0.6-r6` |
| R7 | 数据层修复 | 2 天 | `v1.0.7-r7` |
| R8 | 调度器 + 警报 + fixture | 1.5 天 | `v1.0.8-r8` |
| R9 | 测试缺口 | 3 天 | `v1.0.9-r9` |
| R10 | scripts + docs + README | 3 天 | `v1.1.0-r10` |
| R11 | 结构性收尾 | 2-3 天 | `v1.1.0` |

**合计 22-28 工作日**。每 Phase 结束跑 `make lint && make typecheck && make test-all`、独立 commit、tag、停下来等用户确认。

---

## 1. CLAUDE.md 严格约束违反清单（驱动 Phase 选择）

8 个子系统 review 汇总后未真正落地的 CLAUDE.md 硬约束（参与本计划是为了把它们逐条落地）：

| 约束 | 现状 | 关联任务 |
|---|---|---|
| 严禁硬编码任何配置 | synthesizer model 3 处、critic 4 处、agent 阈值多处 | R4-1, R4-2, R4-3 |
| 严禁写非 async 的 IO 代码 | `pdf_to_snapshot.py` 3 处 `path.write_text` | R5-1 |
| 严禁输出无 citation 的 Finding | `agents/base.py:279` Citation(page=1) 虚假兜底 | R1-3 |
| 严禁跳过测试 | iFind 补漏 stub 但 Phase 2 DONE；sponsor 永真测试 | R3-1, R7-3 |
| 所有 prompt 经 Jinja2 渲染 | grep `{{` 在 prompts/ 零命中 | R4-4 |
| HITL 默认 bypass，生产强制开 | 无 model_validator 联动，pending 会死循环 | R2-1, R2-2 |
| snapshot 不可变（应用层无 update/delete） | PG 实现无显式拒绝 | R2-3 |
| 基石减持不确定性必须显式标注 | `tracking_unreliable` 全仓 0 命中 | R2-5 |
| 误判走 correction transition + audit log | state_machine 无 record_correction | R2-4 |
| 所有 write 经 audit_middleware | user_id 永远 None | R2-6 |
| RBAC 强制 require_role/permission | 7 个 router 仅用 CurrentUserDep | R6-1 |
| 金额字段 JSON 用 string | 未在 StrictModel 强制 | R5-6 |

---

## 2. Phase R0 — 工程基线 Blocker

**目标**：解锁 CI，作为后续所有修复的护栏。
**工作量**：0.5 天。
**出口 tag**：`v1.0.1-r0`。

| ID | 任务 | 锚点 | 修复步骤 | DoD |
|---|---|---|---|---|
| R0-1 | 补 `.env.example` | 项目根 | 按 [`common/settings.py`](src/hk_ipo_agent/common/settings.py) 12 个 section + `docker-compose.yml` 4 个 POSTGRES_* 字段列全；按 section 分组 + 注释每个字段用途 | `cp .env.example .env && make db-up` 一次通过；`make lint` 不报 secrets |
| R0-2 | 补 `.secrets.baseline` | 项目根 | `uv run detect-secrets scan --baseline .secrets.baseline`；commit baseline；验证 `pre-commit run detect-secrets --all-files` 通过 | pre-commit detect-secrets hook 0 报错 |
| R0-3 | 重写 [`.github/workflows/ci.yml`](.github/workflows/ci.yml) | `.github/workflows/ci.yml:32-40` | 改 `pip install uv && uv sync` 装新栈；新增 lint + typecheck + test 三 job；保留 py3.11/3.12 matrix；新增 docker-compose 起 PG/Qdrant/Redis 的 integration job（标 `if: github.event_name == 'pull_request'` 限触发） | CI 在 GitHub 上由"红 → 绿"；coverage 上传成功 |

**完成验证**：在一个 PR 上观察 CI 全绿。
**风险**：极低。
**回滚**：每个 R0-N 是独立 commit；回滚单个 commit 即可。

---

## 3. Phase R1 — 数值正确性硬 bug

**目标**：消除会污染数据/输出/citation 的 5 个硬 bug。
**工作量**：1 天。
**出口 tag**：`v1.0.1-r1`。

| ID | 任务 | 锚点 | 修复 | DoD |
|---|---|---|---|---|
| R1-1 | DCF 终值 ΔWC 符号 `cagr → g` | [`src/hk_ipo_agent/valuation/dcf.py:131-137`](src/hk_ipo_agent/valuation/dcf.py:131) | 终值年 ΔWC 改为 `revenue_t * wc_pct * g`；docstring 注释"显式预测期用 cagr，终值期用 g"；`key_assumptions["terminal_growth_used"]` 新字段 | pen-paper 单测：revenue=1e9 / wc=0.04 / cagr=0.25 / g=0.03 → 终值 UFCF 等于手算值 ±1e-4 |
| R1-2 | LlamaParse page 漂移 | [`src/hk_ipo_agent/prospectus/parser.py:127-154`](src/hk_ipo_agent/prospectus/parser.py:127) | 把 `page += 1` 提到 `if not text.strip(): continue` 之前；或循环用 `page = idx + 1` | 新单测：含 2 空 page + 3 实页 fixture，每 chunk.citation.page 与源 PDF 一致 |
| R1-3 | citation 虚假兜底 | [`src/hk_ipo_agent/agents/base.py:279`](src/hk_ipo_agent/agents/base.py:279) | 删除 `Citation(page=1)` fallback；新异常 `InsufficientCitationError`；extraction 缺数据时 raise 或返回 uncertainty_flag + 空 finding | 新单测 `test_make_finding_with_empty_extraction_raises` |
| R1-4 | f-string 三元 bug × 3 | [`agents/sentiment_agent.py:146`](src/hk_ipo_agent/agents/sentiment_agent.py:146) + [`fundamental_agent.py:103-110`](src/hk_ipo_agent/agents/fundamental_agent.py:103) + [`liquidity_agent.py:64-71`](src/hk_ipo_agent/agents/liquidity_agent.py:64) | 每个条件 fragment 提到独立 chunk 变量再 join | 单测：fallback 路径下 user_msg 必含完整 `# Task` 段 |
| R1-5 | debate 早停 round≥2 + max_rounds clamp | [`critic/debate_graph.py:87, 116`](src/hk_ipo_agent/critic/debate_graph.py:87) | 入口 clamp `min(max_r, settings.debate_max_rounds)`；早停条件 `sim >= threshold and r >= 2` | 单测：(a) max_rounds=10 实跑 ≤3；(b) r=1 不停 / r=2 才停 |

**风险**：DCF 修复会让历史 DCF 估值产生差异；CHANGELOG 标 BREAKING NOTE。
**回滚**：单 commit 回滚。

---

## 4. Phase R2 — 生产安全 + 不可变

**目标**：把 CLAUDE.md 7 条硬约束落地。
**工作量**：2-3 天。
**出口 tag**：`v1.0.2-r2`。

| ID | 任务 | 锚点 | 修复 |
|---|---|---|---|
| R2-1 | HITL 生产强制开 | [`common/settings.py:137`](src/hk_ipo_agent/common/settings.py:137) | `Settings.model_validator(mode='after')`：env=prod && enable_hitl=False → raise `ConfigurationError` |
| R2-2 | hitl_wait 死循环 | [`orchestrator/edges.py:34`](src/hk_ipo_agent/orchestrator/edges.py:34) + [`graph.py`](src/hk_ipo_agent/orchestrator/graph.py) | hitl_wait 节点改 `interrupt_before=True` 真实暂停；或 pending → END，caller 显式 resume |
| R2-3 | snapshot PG 应用层拒绝 update/delete | [`prediction_registry/registry.py:155-168`](src/hk_ipo_agent/prediction_registry/registry.py:155) | `PGPredictionRegistry.update_snapshot` / `delete_snapshot` 显式 `raise NotImplementedError("immutable by design — ADR 0012")`；DB trigger `P0001` 包装成 `SnapshotIntegrityError` |
| R2-4 | state correction transition | [`ipo_lifecycle/state_machine.py`](src/hk_ipo_agent/prediction_registry/ipo_lifecycle/state_machine.py) | 新 `record_correction(reviewer, justification, target_state)`：写 `state_audit_log` + `bypass_validation=True` + emit critical alert；`CORRECTION` enum |
| R2-5 | tracking_unreliable 字段 | [`common/schemas.py:365`](src/hk_ipo_agent/common/schemas.py) + [`outcome_tracker.py`](src/hk_ipo_agent/prediction_registry/outcome_tracker.py) + alembic | `PredictionOutcome.cornerstone_tracking_unreliable: bool = False`；outcome_tracker 在数据缺失时 set True；migration 加列 |
| R2-6 | audit user_id 注入 | [`api/auth/dependencies.py:43-45`](src/hk_ipo_agent/api/auth/dependencies.py:43) | `get_current_user` 末尾 `request.state.current_user = result`；rate_limit + audit 自动受益 |
| R2-7 | JWT prod 启动断言 | [`api/auth/jwt.py:42-46`](src/hk_ipo_agent/api/auth/jwt.py:42) + [`common/settings.py:118`](src/hk_ipo_agent/common/settings.py:118) | `AuthSettings.model_validator`：env in prod && jwt_secret == default literal → raise |

**新增测试**：
- `test_settings_prod_requires_hitl`
- `test_hitl_wait_does_not_loop`
- `test_pg_registry_rejects_update`
- `test_state_correction_writes_audit`
- `test_audit_log_user_id_not_null`
- `test_settings_prod_requires_jwt_secret`

**回滚**：R2-5 涉及 alembic 加列，要写 downgrade。

---

## 5. Phase R3 — 流程伪完成治理

**目标**：堵住 v1.0 release 叙事中 3 个最大的洞（iFind 补漏 / Phase 8 calibration / learning_loop sub-detectors）。
**工作量**：3 天。
**出口 tag**：`v1.0.3-r3`。

| ID | 任务 | 锚点 | 修复 |
|---|---|---|---|
| R3-1 | iFind 补漏路径 | [`data/builders/historical_ipo_loader.py:91-107`](src/hk_ipo_agent/data/builders/historical_ipo_loader.py:91) + [`comparable_pool_builder.py:88-96`](src/hk_ipo_agent/data/builders/comparable_pool_builder.py:88) | 选 A：实装 iFind upsert + IFindGatedSource 降级写 alerts.actionable_info；选 B：改 `raise NotImplementedError("Phase 2 deliverable deferred — see ADR 0018")` + ADR 0005 §Progress 降级为 ⚠️ + 写 ADR 0018 记录决策 |
| R3-2 | learning_cycle 数据加载完整 | [`scripts/run_learning_cycle.py:108-117`](scripts/run_learning_cycle.py:108) | JOIN `prediction_snapshots` / `valuation_outputs` / `debate_output` / `agent_outputs` JSONB，填齐 4 个 sub-detector 字段 |
| R3-3 | calibration placebo 显式标注 | [`backtest/calibration.py:186-228`](src/hk_ipo_agent/backtest/calibration.py:186) | 选 A：禁掉 V8Lite calibration；选 B：`SliceCalibration.is_placebo: bool` + 报告突出"weights don't move IC under V8Lite" |
| R3-4 | monotonicity baseline 真实 label | [`calibration.py:287-300`](src/hk_ipo_agent/backtest/calibration.py:287) + `data/fixtures/nacs_v8_baselines.json` | baseline fixture 加 `regime_pass` 真实条目；删除"regime_pass→main_board"重命名；strict mode |
| R3-5 | applier 失败链路单事务 | [`learning_loop/adjustment_applier.py:172-238`](src/hk_ipo_agent/learning_loop/adjustment_applier.py:172) | bump+write+backtest+mark 包进单 transaction；删 `contextlib.suppress(KeyError)`；`_rollback` 在无 prior 时主动写 sentinel |
| R3-6 | version_manager advisory lock | [`learning_loop/version_manager.py:122-173`](src/hk_ipo_agent/learning_loop/version_manager.py:122) | `pg_advisory_lock(hash(target_path))` + finally unlock；unique constraint `(target_path, version)` migration |
| R3-7 | 强制 proposed_value 非空 | [`learning_loop/adjustment_applier.py:164`](src/hk_ipo_agent/learning_loop/adjustment_applier.py:164) + [`scripts/review_proposals.py`](scripts/review_proposals.py) | applier 加 `proposed_value is not None` 检查；review CLI accept 子命令要求 `--proposed-content path/to/json` |
| R3-8 | applier 进 CLI 主路径 | `scripts/review_proposals.py` | 新增 `apply <review_id>` 子命令取代 LEARNING_PROTOCOL.md 手撸 python -c |

**新增测试**：
- `test_learning_cycle_loads_all_fields` — outcome_samples 4 字段非 None ≥ 95%
- `test_calibration_placebo_flag`
- `test_applier_rollback_writes_audit_row`
- `test_version_manager_concurrent_bumps` — 2 个 psycopg connection 同时 bump，仅一个成功

**风险**：R3-3 改 calibration 行为会动 v0.8 release 叙事，CHANGELOG 标 BREAKING NOTE。

---

## 6. Phase R4 — 模型路由 + Jinja2 + ADR 治理

**目标**：统一模型解析入口、落地 Jinja2 渲染、补 ADR 0017 记录 KIMI 切换。
**工作量**：2 天。
**出口 tag**：`v1.0.4-r4`。

| ID | 任务 | 锚点 | 修复 |
|---|---|---|---|
| R4-1 | `resolve_agent_model()` 单一入口 | 新增 `common/settings.py:resolve_agent_model(role)` | 6 处 hardcode（[`base.py:151`](src/hk_ipo_agent/agents/base.py:151) / [`bull.py:46`](src/hk_ipo_agent/critic/bull.py:46) / [`bear.py:25`](src/hk_ipo_agent/critic/bear.py:25) / [`devils_advocate.py:29`](src/hk_ipo_agent/critic/devils_advocate.py:29) / [`synthesizer.py:86`](src/hk_ipo_agent/synthesizer/synthesizer.py:86) / [`snapshot.py:118`](src/hk_ipo_agent/prediction_registry/snapshot.py:118) / [`extractor.py:128`](src/hk_ipo_agent/prospectus/extractor.py:128)）全部 import 它 |
| R4-2 | Synthesizer 模型分层 | [`config/llm_models.yaml:12`](config/llm_models.yaml:12) | agents.<role>.model 显式区分；synthesizer 单独 key 即使物理上仍是 moonshot |
| R4-3 | 温度配置接入 | [`agents/base.py:172-190`](src/hk_ipo_agent/agents/base.py:172) | `_call_llm` 从 `resolve_agent_model_config(role)` 取 temperature |
| R4-4 | Jinja2 渲染落地 | 新建 `prompts/_render.py` + [`agents/base.py`](src/hk_ipo_agent/agents/base.py) | `render_prompt(path, score_card_class=None, **vars)`：`StrictUndefined` 渲染 body + 注入 `schema_instruction()`；7 agent + critic + synthesizer + extractor 全部走它；prompt 文件加 `{{ ... }}` 显式占位符 |
| R4-5 | extraction prompt 版本 bump | `prompts/extraction/*.md` (6 个) | `version: 0.1 → 1.0`；`last_updated` 同步今天 |
| R4-6 | ADR 0017 + 0002 状态修正 | `docs/decisions/0002-*.md` + 新 `0017-llm-provider-kimi-moonshot.md` | ADR 0002 加 `Superseded-by: 0017`；ADR 0017 记录 KIMI 切换决策、Synthesizer 切回 Opus 开关、风险 |
| R4-7 | inherited_inputs 落地工具调用 | 7 个 `prompts/agents/*.md` | BaseAgent 启动时按 frontmatter `inherited_inputs` 做实际工具调用（regime_score / cluster_bonus / theme_heat），缺失则 raise；ADR 0009 §Progress 同步 |

**新增测试**：
- `test_resolve_agent_model_routes_synthesizer`
- `test_prompt_jinja_rendering_injects_schema`
- `test_prompt_jinja_strict_undefined_raises`

---

## 7. Phase R5 — async 一致性 + 类型 + ID 碰撞

**目标**：消除 async 阻塞、类型不一致、ID 碰撞。
**工作量**：1.5 天。
**出口 tag**：`v1.0.5-r5`。

| ID | 任务 | 锚点 | 修复 |
|---|---|---|---|
| R5-1 | pdf_to_snapshot 异步化 | [`pipelines/pdf_to_snapshot.py:562, 656, 1072`](src/hk_ipo_agent/pipelines/pdf_to_snapshot.py:562) | 3 处 `path.write_text` 全部 `await asyncio.to_thread(...)`；同步 `yaml.safe_load` 同样处理 |
| R5-2 | snapshot_id 类型统一 | [`pdf_to_snapshot.py:100, 477`](src/hk_ipo_agent/pipelines/pdf_to_snapshot.py:100) | `PipelineResult.snapshot_id: UUID \| None`；显式 coerce |
| R5-3 | chunk_id / point_id 统一 UUID 字符串 | [`prospectus/vector_store.py:186-188`](src/hk_ipo_agent/prospectus/vector_store.py:186) | 删 int 转换；Qdrant `PointStruct(id=chunk_id_uuid_str)`；rebuild collection migration |
| R5-4 | pipelines 不再 `set_registry` 全局副作用 | [`pdf_to_snapshot.py:418-420`](src/hk_ipo_agent/pipelines/pdf_to_snapshot.py:418) | `build_main_graph(registry=...)` 显式注入 |
| R5-5 | clear_config_caches 副作用收敛 | [`pdf_to_snapshot.py:320`](src/hk_ipo_agent/pipelines/pdf_to_snapshot.py:320) | contextvar 隔离；只在 CLI 入口调一次 |
| R5-6 | Decimal JSON 强制 string | [`common/schemas.py`](src/hk_ipo_agent/common/schemas.py) StrictModel base | `model_config = ConfigDict(ser_json_decimal="str")` |
| R5-7 | `_merge_extras` 性能 | [`orchestrator/states.py:36-50`](src/hk_ipo_agent/orchestrator/states.py:36) | 不再 `asdict()`；遍历 `dc_fields(left)` + `setattr` |

**新增测试**：
- `test_pdf_to_snapshot_no_blocking_io` — 监控 event loop 阻塞 > 50ms 即 fail
- `test_pipeline_concurrent_runs_no_registry_clobber`
- `test_decimal_serializes_as_string`

---

## 8. Phase R6 — RBAC + Auth 加固 + 脱敏

**目标**：把 7 个 router 加 require_permission、密码切 Argon2、audit 字段级脱敏。
**工作量**：2 天。
**出口 tag**：`v1.0.6-r6`。

| ID | 任务 | 锚点 | 修复 |
|---|---|---|---|
| R6-1 | 7 个 router 加 require_permission | [`dashboard.py:18`](src/hk_ipo_agent/api/routers/dashboard.py:18) / `ipos.py` / `snapshots.py` / `alerts.py` / `prospectus.py` / `auth.py` | 新增 5 个 Permission（READ_DASHBOARD / READ_SNAPSHOT / READ_IPO / READ_ALERT / READ_PROSPECTUS）；按 ROLE_PERMISSIONS 分配给 VIEWER+ |
| R6-2 | 密码哈希切 Argon2 | [`api/auth/dependencies.py:43-45`](src/hk_ipo_agent/api/auth/dependencies.py:43) | `argon2-cffi` 依赖；`_hash_password` 改 Argon2id；旧 sha256 登录时 lazy rehash |
| R6-3 | audit endpoint 字段级脱敏 | [`api/routers/audit.py:34-39`](src/hk_ipo_agent/api/routers/audit.py:34) | 新增 `READ_AUDIT_FULL`；READ_AUDIT 只返回元数据 |
| R6-4 | WS 端点用 PG | [`api/websocket/chat_endpoint.py:43`](src/hk_ipo_agent/api/websocket/chat_endpoint.py:43) | 改用 `get_user_by_id_pg`；in-memory 仅 dev |
| R6-5 | CostGuard 路径豁免精确化 | [`api/middleware/cost_guard.py:18-27`](src/hk_ipo_agent/api/middleware/cost_guard.py:18) | 完整 path 匹配，或路由 metadata `cheap=True` |
| R6-6 | LLMClient prod 失败重抛 | [`api/main.py:36-40`](src/hk_ipo_agent/api/main.py:36) | dev: warn + None；prod: reraise |
| R6-7 | whatif user_id FK 修复 | [`api/routers/whatif.py:51-52`](src/hk_ipo_agent/api/routers/whatif.py:51) + lifespan | lifespan upsert 3 个默认账号到 `user_accounts`；写库时 user_id = current_user.id |
| R6-8 | resource_type 推断 | [`api/auth/audit_middleware.py:74-94, 153-154, 226`](src/hk_ipo_agent/api/auth/audit_middleware.py:74) | middleware 按 path 推断 + 写库 |

---

## 9. Phase R7 — 数据层修复

**目标**：修数据层 stub、import 路径、查询逻辑错。
**工作量**：2 天。
**出口 tag**：`v1.0.7-r7`。

| ID | 任务 | 锚点 | 修复 |
|---|---|---|---|
| R7-1 | TYPE_CHECKING import 路径 | [`historical_ipo_loader.py:26`](src/hk_ipo_agent/data/builders/historical_ipo_loader.py:26) | `from .data.sources.ifind_client` → `from ..sources.ifind_client` |
| R7-2 | 3 个 data source stub → NotImplementedError | [`disclosure_scraper.py`](src/hk_ipo_agent/data/sources/disclosure_scraper.py) / `news_client.py` / `web_search.py` | Protocol class + raise NotImplementedError；__init__ 显式 export |
| R7-3 | sponsor_track_record WHERE 缺失 | [`builders/sponsor_track_record.py:55-71`](src/hk_ipo_agent/data/builders/sponsor_track_record.py:55) | SQL 加 `WHERE sponsor_primary LIKE :pat OR sponsor_secondary LIKE :pat`；测试改严格断言 |
| R7-4 | cornerstone aliases JSONB | [`repositories/cornerstone_repo.py:20-25`](src/hk_ipo_agent/data/repositories/cornerstone_repo.py:20) + 新 migration | `find_by_any_alias(name)` 用 `aliases @> jsonb_path_query`；GIN index |
| R7-5 | ifind_client password 持有 SecretStr | [`ifind_client.py:213-244`](src/hk_ipo_agent/data/sources/ifind_client.py:213) | `self._password: SecretStr` 内部持有；调 SDK 时 `get_secret_value()` |
| R7-6 | ifind_client 重试异常分类 | [`ifind_client.py:546-572`](src/hk_ipo_agent/data/sources/ifind_client.py:546) | 网络抖动→`DataSourceUnavailableError`（可重试）；其他→`DataSourceError` |
| R7-7 | migrate_sqlite_to_pg 单事务 | [`scripts/migrate_sqlite_to_pg.py:615-650`](scripts/migrate_sqlite_to_pg.py:615) | IPO + companies/financials 单 session 单 commit |
| R7-8 | BaseRepository upsert 默认排除 | [`repositories/base.py:124-167`](src/hk_ipo_agent/data/repositories/base.py:124) | 默认 update_columns 排除 `created_at, id` |
| R7-9 | builders session 注入 | 4 个 builder | 改 `__init__(self, session: AsyncSession)` |
| R7-10 | async_session_factory 跨 loop 安全 | [`data/database.py:39-47`](src/hk_ipo_agent/data/database.py:39) | `ContextVar[async_sessionmaker]` 替换 `lru_cache(maxsize=1)` |

---

## 10. Phase R8 — 调度器 + 警报 + fixture fail-fast

**工作量**：1.5 天。
**出口 tag**：`v1.0.8-r8`。

| ID | 任务 | 锚点 | 修复 |
|---|---|---|---|
| R8-1 | regime_score fixture 缺失 → raise | [`backtest/regime_detection.py:178-184`](src/hk_ipo_agent/backtest/regime_detection.py:178) | warning + 0.0 改为 raise RuntimeError |
| R8-2 | cornerstone_count 真实填充 | [`backtest/runner.py:354-398`](src/hk_ipo_agent/backtest/runner.py:354) | 调 `AsOfDataProvider.get_cornerstone_investments(ipo_id)` 真实 count |
| R8-3 | daily T+360 不再自动 TERMINATE | [`schedulers/daily_scheduler.py:128-130`](src/hk_ipo_agent/prediction_registry/schedulers/daily_scheduler.py:128) | 改为 emit critical alert + 等待人工 ack；新 `terminal_proposed` 中间态 |
| R8-4 | fallback_price None 短路 | [`daily_scheduler.py:227-235`](src/hk_ipo_agent/prediction_registry/schedulers/daily_scheduler.py:227) | 返回 None；review_workflow 短路 skip |
| R8-5 | settings.scheduler_runtime enum | [`common/settings.py`](src/hk_ipo_agent/common/settings.py) | 加 `Literal["airflow", "apscheduler"]`；prod 必须 airflow |
| R8-6 | event_driven_scheduler 注入 registry | [`schedulers/event_driven_scheduler.py:128-134`](src/hk_ipo_agent/prediction_registry/schedulers/event_driven_scheduler.py:128) | 构造器注入而非 `get_registry()` 全局 |
| R8-7 | outcome_tracker 交易日 vs 日历日 | [`outcome_tracker.py:125`](src/hk_ipo_agent/prediction_registry/outcome_tracker.py:125) | `BenchmarkPriceService.get_trading_day_offset` |
| R8-8 | alerts PG 实装 | `prediction_registry/alerts.py` | 补 `PGAlertStore` + setter |
| R8-9 | Airflow DAG 落地 | 4 个 [`airflow_dags/*.py`](src/hk_ipo_agent/prediction_registry/schedulers/airflow_dags/) | NotImplementedError 改真实 PythonOperator wire |

---

## 11. Phase R9 — 测试缺口补齐

**工作量**：3 天。
**出口 tag**：`v1.0.9-r9`。

| ID | 任务 | 锚点 |
|---|---|---|
| R9-1 | tests/unit/pipelines/ 全套 | 新建目录；15-20 单测；覆盖率 ≥70% |
| R9-2 | WebSocket / RateLimit / CostGuard / CORS-prod | tests/unit/api/ |
| R9-3 | DCF pen-paper 单测 | tests/unit/valuation/test_dcf.py |
| R9-4 | calibration placebo regression | tests/unit/backtest/test_calibration.py |
| R9-5 | state machine 不回退断言 | tests/unit/prediction_registry/test_state_machine.py |
| R9-6 | LlamaParse page 锁定 | tests/unit/prospectus/test_parser.py |
| R9-7 | 测试分层重构 | 把 pg_required 测试挪到 integration/；抽 `tests/_pg_helpers.py` |
| R9-8 | slow marker | 3 个 e2e 测试打 `@pytest.mark.slow`；`make test` 默认 `-m "not slow"` |
| R9-9 | conftest mock_llm_client 还原 | `tests/conftest.py:96-107` yield + 还原 env |
| R9-10 | e2e conftest session-scope | `tests/e2e/conftest.py:55-87` autouse 改 session-scope + 哨兵文件 |

---

## 12. Phase R10 — scripts 大扫除 + docs + README

**工作量**：3 天。
**出口 tag**：`v1.1.0-r10`。

| ID | 任务 | 范围 |
|---|---|---|
| R10-1 | scripts 一次性脚本归档 | 25+ 个 `fix_p*, verify_*, probe_*, explore_*` 挪到 `legacy/scripts/` |
| R10-2 | dev.py 主 CLI 扩展 | 注册 backtest / learning-cycle / review / fetch-data 子命令 |
| R10-3 | analyze_pdf 默认 peers 外移 | `config/default_peers.yaml` |
| R10-4 | requirements.txt 处理 | 删除或改 `-e .[dev]` |
| R10-5 | docs/ 5 篇 UI 关键文档 | API_REFERENCE / SSE_PROTOCOL / WS_PROTOCOL / RBAC / UI_INTEGRATION |
| R10-6 | README Status 段刷新 | v1.0/v1.1 完成度 |
| R10-7 | ARCHITECTURE.md 补全 | 模块图 + 数据流 + 关键扩展点 + 部署架构 |
| R10-8 | LEARNING_PROTOCOL 与代码对齐 | 删手撸 python -c 段 |
| R10-9 | data/derived 归档 | iterations / _archive / theme_constituents_* 挪 `legacy/data/` |
| R10-10 | pyproject ruff exclude 收敛 | `legacy/**` 统一；mypy overrides 删 langchain |
| R10-11 | pre-commit mypy 翻 active | `stages: [manual]` → `[pre-commit]` |

---

## 13. Phase R11 — 结构性收尾

**工作量**：2-3 天。
**出口 tag**：`v1.1.0`。

| ID | 任务 | 锚点 |
|---|---|---|
| R11-1 | 拆 backtest_snapshots 表 | 新 alembic migration + router filter |
| R11-2 | MarketDataExtras 类型化 | 新增 `common/schemas.MarketDataExtras` Pydantic 类 |
| R11-3 | Regime Gate 单一常量 | `common/constants.py:REGIME_GATE_THRESHOLD` |
| R11-4 | 3 个 reporting 模板处理 | 实装或删除 |
| R11-5 | Devil's Advocate 注入证据 | [`critic/devils_advocate.py:33-40`](src/hk_ipo_agent/critic/devils_advocate.py:33) |
| R11-6 | Bull/Bear 独立证据池 | [`critic/bull.py`](src/hk_ipo_agent/critic/bull.py) / `bear.py` |
| R11-7 | expected_return 6m vs 12m | [`synthesizer.py:74`](src/hk_ipo_agent/synthesizer/synthesizer.py:74) |
| R11-8 | Synthesizer LLM 失败 fallback | [`synthesizer.py:141-147`](src/hk_ipo_agent/synthesizer/synthesizer.py:141) |
| R11-9 | retriever BM25 LRU 缓存 | [`prospectus/retriever.py:84-113`](src/hk_ipo_agent/prospectus/retriever.py:84) |
| R11-10 | create_snapshot_node 异常窄化 | [`orchestrator/nodes.py:194`](src/hk_ipo_agent/orchestrator/nodes.py:194) |
| R11-11 | LLMClient per-call cost 预检 | [`common/llm_client.py:224`](src/hk_ipo_agent/common/llm_client.py:224) |
| R11-12 | _write_detailed_report 拆函数 + 修中文乱码 | [`pdf_to_snapshot.py:663-1073`](src/hk_ipo_agent/pipelines/pdf_to_snapshot.py:663) |

---

## 14. 跨 Phase 全局守则

1. **分支策略**：每个 Phase `git checkout -b fix/phase-rN-<slug>`
2. **commit message 模板**：`fix(<scope>): R<N>-<M> — <summary>`
3. **DoD 强制**：每个 Critical 修复必须配单测，且新单测应在修复前先 fail 验证（red-green）
4. **完成验证**：每 Phase 跑 `make lint && make typecheck && make test-all`
5. **tag 策略**：每 Phase 一个 tag；tag 是回滚锚点
6. **回滚**：涉及 DB migration 的步骤必写 downgrade
7. **BREAKING NOTE**：R1-1（DCF 校正）+ R3-3（calibration 改动）+ R11-1（snapshot 拆表）在 CHANGELOG 标 BREAKING

---

## 15. 依赖图

```
R0 ──> R1 ──> R2 ──> R3 ──┐
              ╲           ├─> R6 (Auth 改造需要 R2 audit 主体落地)
              R4 ──> R5 ──┘
                          │
              R7 ─────────┤
              R8 ─────────┤
                          ▼
                          R9 (测试缺口建立在功能修复完成后)
                          │
                          ▼
                          R10 (docs 反映最终代码状态)
                          │
                          ▼
                          R11 (结构性收尾)
```

- R0 必须最先
- R1 / R2 / R3 / R4 / R5 / R7 / R8 严格意义上可部分并行，但建议串行
- R9 必须在 R1-R8 全部完成后
- R10 / R11 最后

---

## 16. Progress 看板

| Phase | 状态 | 开始日期 | 完成日期 | tag | 备注 |
|---|---|---|---|---|---|
| R0 | ✅ 完成 | 2026-05-17 | 2026-05-17 | `v1.0.1-r0` | commits `8ee82bd` (functional) + `d78b34c` (pre-commit auto-fix LF/EOF/ruff format)；727 unit tests 全过 |
| R1 | ✅ 完成 | 2026-05-17 | 2026-05-17 | `v1.0.1-r1` | commit `442e1fc`；5 任务全 red-green-refactor；736 unit tests 全过（+9 新 R1 测试，0 regression）。**BREAKING**: R1-1 DCF 终值公式校正，历史 snapshot 会有 ~5% 差异 |
| R2 | ✅ 完成 | 2026-05-17 | 2026-05-17 | `v1.0.2-r2` | 7 任务全 red-green-refactor；753 unit tests 全过（+17 新 R2 测试，0 regression）。R2-1 + R2-7 prod guards / R2-6 audit user_id / R2-3 snapshot 应用层拒绝 / R2-5 tracking_unreliable + alembic / R2-2 hitl pending → END / R2-4 record_correction. CLAUDE.md 7 条硬约束全部落地 |
| R3 | ✅ 完成 (8/8) | 2026-05-17 | 2026-05-18 | `v1.0.3-r3` | R3-1..R3-8 全部完成。**787 unit tests passed** (R2: 753 → R3 complete: +34, 0 regression). R3-1 iFind stub raise + ADR 0018 / R3-2 learning_cycle 4 extractors / R3-3 calibration is_placebo / R3-4 monotonicity regime_pass baseline / R3-5 applier rollback sentinel + 删除 suppress(KeyError) / R3-6 version_manager pg_advisory_xact_lock / R3-7 proposed_value=None 显式 reject / R3-8 review_proposals.py apply 子命令 |
| R4 | ✅ 完成 7/7 | 2026-05-18 | 2026-05-18 | `v1.0.4-r4-final` | 全 7 任务完成：R4-1 resolve_agent_model 单一入口 + 13 处 hardcode 替换 / R4-2 Synthesizer YAML 分层 / R4-3 temperature 接入 / R4-4 Jinja2 prompt_renderer + StrictUndefined + schema auto-inject / R4-5 extraction prompts version 0.1→1.0 / R4-6 ADR 0017 + 0002 治理 / R4-7 inherited_inputs frontmatter 校验（MissingInheritedInputError + BaseAgent.\_verify\_inherited\_inputs + alias map）. **810 unit tests passed (+23 R4 total)** |
| R5 | ⏸ 等待 | - | - | - | async + 类型 |
| R6 | ⏸ 等待 | - | - | - | RBAC + Auth |
| R7 | ⏸ 等待 | - | - | - | 数据层 |
| R8 | ⏸ 等待 | - | - | - | 调度器 + 警报 |
| R9 | ⏸ 等待 | - | - | - | 测试缺口 |
| R10 | ⏸ 等待 | - | - | - | scripts + docs |
| R11 | ⏸ 等待 | - | - | - | 结构性收尾 |

每个 Phase 完成时手动更新此表。

---

## 链接

- 完整审查发现：见 2026-05-17 对话归档（8 个 review agent 报告）
- CLAUDE.md 「严格约束」+ 「UI 集成约束」+ 「预测生命周期约束」+ 「自动化与状态机约束」是本计划全部任务的合规基线
- 关联 ADR：0001-0016（v1.0 前的全部决策）；本计划将新增 ADR 0017（KIMI/Moonshot 切换）+ 可能 0018（iFind 补漏延期）
