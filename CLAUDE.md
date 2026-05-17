# Claude Code 工作准则 — HK IPO Cornerstone Agent

> 本文件按 PROJECT_SPEC.md §11 写成，是 Claude Code 启动后第一份要读的文件。所有约束与本仓库 PROJECT_SPEC.md 一致；冲突时以 PROJECT_SPEC.md 为准。

---

## 启动检查

1. 优先读 [PROJECT_SPEC.md](PROJECT_SPEC.md) — 项目权威规范（含 v1.0/v1.1/v1.2/v1.2.1 全部增量）
2. 读 [PROJECT_SPEC_UI.md](PROJECT_SPEC_UI.md) — 前端规范，UI 是独立项目但依赖本仓库后端 API
3. 读 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 理解架构（Phase 1 起开始填充）
4. 检查当前所在 Phase（看 git tag / CHANGELOG / 本文件末尾 Phase 进度看板）
5. **读 [docs/decisions/README.md](docs/decisions/README.md) — ADR 索引**，定位当前 Phase 必读的 ADR（特别是 spec 没说但实际要做的事，例如 NACS 资产继承）

---

## Phase 启动前必读（强制清单）

**每个 Phase 启动的第一件事都是核对此清单**。PROJECT_SPEC.md §4 列的是新代码 deliverables，但下表列出的是 spec 没说、又必须做的事（NACS 资产继承、版本管理协议、特殊约束等），由 ADR 强制驱动。

| Phase | 必读文档 | 关键动作 |
|---|---|---|
| **Phase 1** 核心基础设施 | PROJECT_SPEC.md §6 (Pydantic) + §11 + ADR 0001-0004 | 实现 schemas / enums / LLM client / settings / ORM / Alembic 初始 migration |
| **Phase 2** 数据层 | **ADR 0005 §1 + §4**（强制） + PROJECT_SPEC.md §3.4 | 1) 实现 `scripts/migrate_sqlite_to_pg.py` 把 NACS SQLite 迁到 PG（spec 没列但必须）；2) `historical_ipo_loader.py` 与 `cornerstone_profile_builder.py` 优先吃迁移后的数据，iFind 仅作补漏；3) `tests/unit/data/test_no_lookahead.py` 迁移防泄漏逻辑 |
| **Phase 3** 招股书处理 | PROJECT_SPEC.md §3.5 + ADR 0004 | LlamaParse + PyMuPDF 双路径；citation 强制可溯源 |
| **Phase 4** 估值模型层 | **ADR 0005 §2**（Regime Gate 后置硬门） + **ADR 0008**（DCF agent 算法借鉴） + PROJECT_SPEC.md §3.7 | `valuation/ensemble.py` 必须实现 regime<0 → SKIP 截断；DCF/Comps 公式参考 DCF agent session-f.md/session-h.md（注释标明来源行号）；不复制代码 |
| **Phase 5** Agent 层 | **ADR 0005 §2 + §5**（强制） + **ADR 0009**（港股研究agent 框架借鉴） + PROJECT_SPEC.md §7 | `policy_agent` 必须输出 `regime_score`；`cornerstone_signal_agent` 必须用 ultimate_holder 聚类；`sentiment_agent` 必须读 `themes/*.json`；3 个 prompts 的 `inherited_inputs` frontmatter 必须落地为实际工具调用；BaseAgent / ScoreCard / WorkflowExtras 参考港股研究agent (ADR 0009) |
| **Phase 6** 编排 + Critic + Synthesizer | PROJECT_SPEC.md §8 + **ADR 0010** | LangGraph 主图必须保证 `synthesize → create_snapshot → report` 顺序；辩论 Jaccard 早停（≤3 轮）；Devil 元层质疑；HITL 默认 bypass（生产 env 强制开）；Phase 6 in-memory snapshot，Phase 7.5 替换 PG |
| **Phase 7** 报告 + API + UI 集成层 | PROJECT_SPEC.md §16 全章 + **ADR 0011** | MVP 实施：10 核心 router + middleware + auth (本地 JWT，无 SSO) + SSE/WS 骨架 + reporting + What-If；OpenAPI 3.1 完整可被 UI 消费；reviews/proposals/drift/backtest 延 Phase 7.5/8 |
| **Phase 7.5** 预测档案 + 生命周期 | PROJECT_SPEC.md §3.11 / §3.11.1 / §3.11.2 + §10 + **ADR 0012**（强制） + ADR 0011 Progress（遗留收尾） | 切 7.5a/b/c/d 4 子阶段，每个子阶段独立 commit + 单测 + 停下来等确认；7.5a 一次性 18 表 + 4 trigger + Registry PG 化 + Phase 7 in-memory PG 化；7.5b 数据流闭环 + reviews/proposals/drift router；7.5c 状态机 + 代码映射 + 财报比对 + 警报；7.5d 三层调度器 + Airflow + 端到端晶泰；snapshot immutability 必须 DB trigger 强制；LISTED 三重验证 |
| **Phase 8** 回测与校准 | **ADR 0005 §3**（强制） + **ADR 0013**（强制） + PROJECT_SPEC.md §3.9 | 切 8a/b/c/d 4 子阶段：as_of_data + regime_detection / IC L-S t-stat metrics / runner + calibration + reports + 50+ 全量回测 / backtest router 收尾。`backtest/metrics.py` 实现 IC / L-S / t-stat 三件套；`calibration.py` 用 v8 5 轮 iteration archive 作单调性约束；`regime_detection.py` 用 `market_environment_cache` JSON fixture；防泄漏 `as_of_data.py` 是地基 |
| **Phase 9** 端到端验证 | **ADR 0005 §Progress（归档段）** | 把 `themes/` / `data/nacs_real.db` / NACS 顶层脚本归档到 `legacy/`；勾选 ADR 0005 Progress 全部条目 |
| **Phase 10** 持续学习闭环 | PROJECT_SPEC.md §3.12 | drift_detector / counterfactual / adjustment_applier 强制人工 gate |

**核对方法**：每个 Phase 第一个 commit 前，必须在 `docs/decisions/0005-...md` 末尾 Progress 段确认相关条目状态。未勾选条目不得标记 Phase 完成。

---

## 严格约束（不可违反）

- **严禁跨 Phase 工作**。当前 Phase 完成才能进入下一个，每个 Phase 完成必须停下来等用户确认
- **严禁引入 PROJECT_SPEC.md §1 技术栈之外的核心依赖**（如 LangChain Agents / CrewAI / AutoGen / MUI / Ant Design 等）
- **严禁在代码中硬编码任何配置**（必须走 `config/` YAML + `pydantic-settings` 加载）
- **严禁写非 async 的 IO 代码**（DB / HTTP / LLM / 向量库一律 async；CPU 任务用 `asyncio.to_thread`）
- **严禁输出无 citation 的 Finding**（所有 agent 输出必须可溯源到招股书页码 + chunk_id）
- **严禁跳过测试**。每个新模块必须配单元测试；覆盖率 ≥ 80%
- **严禁修改 PROJECT_SPEC.md / CLAUDE.md 的约束部分**；可在 `docs/decisions/` 写 ADR 提议变更

---

## 决策原则

- 遇到歧义停下来问，不要猜
- 遇到 spec 没说的小决策，先在 `docs/decisions/` 写 ADR 草稿
- 修改 schema 必须同步更新 Alembic migration
- 任何"删除"或"重构超过 1 个文件"先问用户

---

## 工作流

- 每个新功能：写 schema → 写测试 → 写实现 → 跑测试 → 跑 lint/typecheck → commit
- 每个 commit 前必须 `make lint && make typecheck && make test`
- 大改动前先在 `docs/decisions/` 写 ADR
- 每个 Phase 完成后打 tag `v0.<phase>`

---

## 提示词约束

- 所有提示词必须放 `prompts/`，不准内嵌在 `.py` 文件
- 提示词文件必须含 frontmatter（role / version / last_updated / input_schema / output_schema）
- 修改提示词必须 bump version
- 所有提示词在 LLM 调用前都必须经过 Jinja2 渲染（注入 schema、上下文）

---

## 数据安全

- 招股书 PDF 默认 gitignore（用户数据）
- 测试不准用真实公司全文，只能用 `tests/fixtures/` 小样本
- API key 必须走 env vars（见 `.env.example`），禁止入仓
- 数据库连接字符串必须用 env vars
- 提交前用 `detect-secrets` 扫描（pre-commit 已配）

---

## 性能要求

- 单次完整分析必须 ≤ 30 分钟（spec §13）
- 单 agent 调用 ≤ 5 分钟
- LLM 重试不超过 3 次
- 单次完整分析 LLM 成本 ≤ $5

---

## 我应该问而不是猜的场景

1. 是否要新增第三方依赖（超出 spec §1 列表）
2. 是否要修改 schema（含 Pydantic + SQLAlchemy）
3. 是否要修改 Phase 顺序或 deliverables
4. 数据源访问失败时的 fallback 策略
5. 任何涉及"删除"或"重构"超过 1 个文件的操作

---

## 预测生命周期约束（v1.1 — 重要程度等同于严格约束）

- **任何完整分析必须先创建 snapshot 才能输出决策**。orchestrator 图必须保证 `synthesize → create_snapshot → report`，create_snapshot 失败则整个流程失败
- **`prediction_snapshots` 表绝对不可变**。禁止 ORM update / delete；DB trigger 已拦截；应用层也不准实现 `update_snapshot`
- **任何 config / prompt 修改必须走 learning_loop**：propose（写入 `prediction_reviews`）→ reviewer 人工 accept → applier 应用 + bump version → 触发小回测验证
- 绕过此流程的紧急修改必须在 `docs/decisions/` 写 ADR 并打 hotfix tag
- **outcome / attribution / review 数据必须经过对应 workflow**；禁止直接 INSERT
- **Checkpoint 日期固定**：T+1, +5, +10, +22, +30, +60, +90, +126, +180, +252, +360（自上市日算起），不可调整。错过当天必须用 close_price of that exact date 补跑
- **系统不得自动应用任何调整**。所有 adjustment 必须 reviewer 字段非空 + status=accepted 才能 apply
- **撤回 / 聆讯失败的 IPO 也要建 snapshot**：防 survivorship bias
- **基石减持检测的不确定性必须显式标注**：`tracking_unreliable=true`

---

## 自动化与状态机约束（v1.2）

- **IPO 状态机的合法转换严格执行**。`VALID_TRANSITIONS` 之外的转换必须抛 `InvalidStateTransition`
- **LISTED 状态必须经过三重验证**（HKEX 公告 + iFind 行情 + 股票代码激活）。任一缺失不得转 LISTED
- **状态机不得回退**。误判的纠正方式是新建 correction transition 写 audit log，而不是改 current_state
- **三层调度器各司其职**：high_freq 严禁做归因 / 回测；daily 严禁实时事件；重叠运行用 DB advisory lock 拦截
- **代码映射的不确定性必须传递**：low confidence 必须 `requires_review=True` 并发警报
- **超时不等于失败**。stale_detector 触发的是警报而非自动 WITHDRAWN
- **财报口径差异不能静默处理**：earnings_comparator 前 3 次必须 `requires_human_review=True`
- **生产环境必须用 Airflow**。APScheduler 仅用于 dev/test
- **所有警报必须含 `actionable_info` 字段**。"Failed" 不可接受，必须说"应该做什么"
- **调度器失败必须升级**：daily_scheduler 失败 6h 内未恢复 → critical alert
- **数据源失败有序降级**：iFind 主路径 → 重试（最多 3 次）→ 转 `manual_pending` + warning。禁止用估算值代替真实数据

---

## UI 集成约束（v1.2.1）

- **后端是 UI 的权威**。所有业务逻辑、计算、验证都在后端实现；UI 只展示和交互
- **OpenAPI schema 必须自动生成且完整**。任何 API 变更必须同步；UI `openapi-typescript` CI diff 必须为空
- **API 错误必须用 RFC 7807 Problem Details 格式**。禁止 `{"error": "something failed"}`
- **所有 write 操作必须自动写 audit_log**。通过 `audit_middleware.py`，不依赖各 endpoint 自觉
- **RBAC 检查是 endpoint 强制项**。除 `/health` `/ready` `/metrics` 外，必须用 `require_role()` / `require_permission()`
- **CORS 白名单严格控制**。生产不允许 `*`
- **SSE 事件类型必须在 `event_types.py` 先注册**。禁止发未注册事件
- **WebSocket chat 所有消息必须持久化到 `chat_messages` 表**
- **What-If 结果必须持久化到 `whatif_calculations`**
- **API 响应必须含 `X-Request-Id` header**
- **不允许 UI 直连 DB / 向量库**；不允许 UI 调用 LLM
- **敏感字段必须脱敏**（除非有 `READ_AUDIT` 权限）
- **分页用 `PaginatedResponse` 标准格式**
- **金额字段在 JSON 中必须用 string 类型**（防 JS 精度损失）
- **日期时间统一用 ISO 8601 + timezone**

---

## 当前重构上下文（NACS → spec v1.2.1）

本仓库正在从 NACS v8（量化评分模型）原地重构到 PROJECT_SPEC.md 定义的多 Agent LLM 系统。背景：

- **NACS v8 代码完全废弃**，但 4 年数据资产 + 实证 know-how 按计划继承到 spec 的对应模块
- **NACS 资产迁移地图见 [docs/decisions/0005-nacs-legacy-asset-migration.md](docs/decisions/0005-nacs-legacy-asset-migration.md)**（ADR 0005，权威）。Phase 2 / Phase 4 / Phase 5 / Phase 8 / Phase 9 启动时必须主动核对此 ADR 的 Progress 段
- 旧代码（`src/nacs_model.py` / `src/config.py` / `src/data/dao.py` / `scripts/{fetch,fix,probe,verify,explore}_*.py` / `themes/` / `configs/` / `build_perf_cache.py` / `check_health.py` / `run_v7_backtest.py` / `nacs_checklist_tool.html`）暂留原位，由 ADR 0005 Progress 表中对应的 Phase 完成后归档到 `legacy/`
- 新代码全部位于 `src/hk_ipo_agent/`；新配置位于 `config/`（单数，与旧 `configs/` 区分）
- 数据资产（`data/nacs_real.db` 14 表，385 IPO + 1,314 基石）将在 Phase 2 通过 `scripts/migrate_sqlite_to_pg.py` 一次性迁移到 PostgreSQL（详见 ADR 0005 §1）
- `pyproject.toml` 中 ruff / mypy 已配置只覆盖 `src/hk_ipo_agent/` + `tests/{unit,integration,e2e}/` + `scripts/`（新增），旧 NACS 代码不参与新工具链 lint

**关键 Agent 必须继承的 NACS 信号**（实施时查 ADR 0005 §2）：

| Agent / 模块 | 继承的 NACS 信号 | 实证效果 |
|---|---|---|
| `agents/policy_agent.py` + `valuation/ensemble.py` | Regime Gate（regime<0 → SKIP） | regime≥0 子样本 60d IC=+0.247, t=+2.41 |
| `agents/cornerstone_signal_agent.py` | Cluster Bonus（同 ultimate_holder ≥2 个 SPV） | cluster≥2 IPO 60d mean +22% (vs +14%)，std ↓40% |
| `agents/sentiment_agent.py` | Theme Heat + AI Gilding（AI 收入 <10% ×0.85） | 主题情绪轨迹 + 镀金风险识别 |
| `backtest/metrics.py` / `calibration.py` / `regime_detection.py` | IC / L-S spread / t-stat 三件套 + 5 轮 v8 迭代基线 + market_environment_cache | Phase 8 必须用作单调性约束 baseline |

---

## Phase 进度（手动维护，每个 Phase 完成时更新）

- [x] Phase 0 — 项目骨架（DONE：`make install && make lint` 通过 + docker compose 起得来）
- [x] Phase 1 — 核心基础设施（DONE：`make migrate` 成功 + 90 tests passed + LLM client cost tracking 就位）
- [x] Phase 2 — 数据层（含 SQLite → PostgreSQL ETL）（DONE：399/2014/2560/1592 行已 ETL 到 PG + 151 tests passed + ADR 0005 §Progress 5 个 Phase 2 条目全勾）
- [x] Phase 3 — 招股书处理（DONE：9 个 prospectus 模块 + 6 prompts + 33 unit + 2 integration 测试；synthetic PDF 端到端通过 + citation 强制）
- [x] Phase 4 — 估值模型层（DONE：10 个 valuation 模块 + Regime Gate 硬门 + 58 unit tests + 3 DONE-condition smoke + 265 全仓单测通过；ADR 0008 §Progress Phase 4 两项已勾）
- [x] Phase 5 — Agent 层（DONE：7 agent + tools + WorkflowExtras + 55 unit tests + 3 DONE-condition smoke + 320 全仓单测通过；ADR 0009 §Progress 7 项 Phase 5 条目全勾；NACS 三件套 regime_score/cluster_bonus/theme_heat 全部接入）
- [x] Phase 6 — 编排 + Critic + Synthesizer（DONE：LangGraph 主图 + Bull-Bear-Devil 辩论 + Jaccard 早停 + Opus synthesizer + create_snapshot 强制 + 69 新单测 + 389 全仓单测通过；ADR 0010 §Progress Phase 6 全勾）
- [x] Phase 7 — 报告 + API + UI 集成层 MVP（DONE：reporting (memo + PDF/DOCX) + FastAPI 31 routes + JWT/RBAC + middleware + SSE + WS chat + What-If + 27 新单测 + 449 全仓单测通过；ADR 0011 §Progress Phase 7 全勾；reviews/proposals/drift/backtest 延 Phase 7.5/8；SSO 延 Phase 9）
- [x] Phase 7.5 — 预测档案 + 生命周期追踪（DONE 2026-05-16：tag `v0.7.5` + 全仓 554 单测通过；ADR 0011 Progress + ADR 0012 Progress 全勾）
  - [x] **7.5a** Schema + Registry PG 化（DONE 2026-05-16：19 张 v1.1+v1.2+v1.2.1 表 + 4 trigger + Registry Protocol/InMemory/PG 双实现 + Orchestrator HardEdge + SnapshotCreationFailed + 11 新单测；ADR 0012 §Progress 7.5a 已勾；Phase 7 in-memory PG 化挪到 7.5b）
  - [x] **7.5b** Outcome/Event/Attribution/Review 数据流闭环 + Phase 7 5 个 in-memory PG 化 + reviews/proposals/drift router（**ADR 0011 Progress 全勾**）
    - [x] 7.5b-1 (883e11e) 预测追踪流水线 5 模块 + 2 prompts + 30 新单测
    - [x] 7.5b-2 (d2d2508) audit_middleware PG 化 + reviews/proposals/drift router 实装 + 11 新单测
    - [x] 7.5b-3 chat manager + event_bus + whatif endpoint + users PG 化（4 个 store 双实现 + Protocol + setter + 10 新单测）
  - [x] **7.5c** ipo_lifecycle/ 6 模块 + code_mapper + earnings_comparator + alerts + config YAML
    - [x] 7.5c-1 ipo_lifecycle/ 6 模块（states / state_machine / state_detectors 三重 LISTED / stale_detector / terminal_handlers / ah_special） + 28 新单测含 3 状态机仿真
    - [x] 7.5c-2 code_mapper (3 策略 + LOW→requires_review) + earnings_comparator (前 3 次强制 review) + alerts (24h dedup + actionable_info 强制) + config/{alerts,mapping_rules}.yaml + 29 新单测
  - [x] **7.5d** schedulers/ 4 模块 + Airflow DAG + 端到端晶泰 + tag `v0.7.5`
    - [x] 7.5d-1 (8ae5cfa) schedulers/{base, high_freq, daily, event_driven} + advisory lock + config/schedulers.yaml + 12 新单测含 4 对抗测试
    - [x] 7.5d-2 4 Airflow DAG + 端到端晶泰 2228.HK simulation（PRE_LISTING → PRICING → LISTED → 10 canonical checkpoint outcomes → review_drafts at T+30/90/180 → TERMINATED at T+360 + immutability 保持）+ tag `v0.7.5`
- [x] Phase 8 — 回测与校准（DONE：tag `v0.8` + ADR 0013 全勾 + 374 样本全量回测跑通）
  - [x] **8a** (0b65ded) as_of_data 防泄漏 + regime_detection + market_environment_cache JSON + 25 新单测
  - [x] **8b** (cd860f7) metrics.py (Rank IC / L-S spread / Welch t-stat) + nacs_v8_baselines.json (5 iterations) + monotonicity_constraint + compare_to_baseline + 25 新单测（含 pen-paper IC + 退化 case 拒绝 + canonical p1_lockup_v2 self-pass）
  - [x] **8c** (f2fbd32) runner.py (BacktestScorer Protocol + V8LiteScorer + walk-forward + PG loader) + calibration.py (constrained grid search + monotonicity，回退 Bayesian 避免新依赖) + reports.py (markdown 5 sections) + scripts/run_backtest.py CLI + 32 新单测（runner 11 + calibration 13 + reports 8）+ 2 integration smoke
  - [x] **8d** backtest router (list runs + detail + _meta/count，6 单测) + runner.persist_run_to_pg + CLI --persist + **374 样本全量回测跑通**（277 regime-pass，报告写 `reports/backtest/2026-05-17_*.md`，374 行 prediction_snapshots 持久化）+ ETL bug fix + tag `v0.8`
- [ ] Phase 9 — 端到端验证
- [ ] Phase 10 — 持续学习闭环
