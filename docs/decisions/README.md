# Architecture Decision Records (ADR Index)

每个 ADR 捕捉一项架构重要决策。格式：`0NNN-short-slug.md`，状态：`Proposed / Accepted / Deprecated / Superseded`。

**关键导航原则**：每个 Phase 启动前必须扫描本索引，看哪些 ADR 标记的 Phase 字段包含当前 Phase。CLAUDE.md「Phase 启动前必读」段也列了同样信息，两处一致。

---

## ADR 全清单

| ID | 标题 | 状态 | 影响 Phase | 摘要 |
|---|---|---|---|---|
| [0001](0001-use-langgraph.md) | Use LangGraph for agent orchestration | Accepted | Phase 6+ | 多 Agent 编排统一走 LangGraph，禁用 LangChain Agents / CrewAI / AutoGen |
| [0002](0002-claude-as-primary-llm.md) | Use Claude (Sonnet 4 / Opus 4.7 for Synthesizer) | Accepted | Phase 1, 5, 6 | 所有 LLM 调用走 Anthropic Claude，Synthesizer 用 Opus，其余 Sonnet |
| [0003](0003-qdrant-over-chroma.md) | Use Qdrant over Chroma | Accepted | Phase 3 | 向量库选 Qdrant（hybrid search + 元数据过滤） |
| [0004](0004-llamaparse-for-pdf.md) | LlamaParse primary + PyMuPDF fallback for PDF | Accepted | Phase 3 | 招股书表格识别走 LlamaParse，PyMuPDF + Camelot 兜底 |
| [0005](0005-nacs-legacy-asset-migration.md) | NACS v8 遗产资产迁移到 spec v1.2.1 的完整映射 | **Accepted** | **Phase 2, 4, 5, 8, 9** | 4 年数据 + 实证 know-how 的完整继承计划；含 Progress checklist 15 条 |
| [0006](0006-phased-orm-rollout.md) | Phase 1 ORM 范围决策 — v1.0 基础表本期落地，v1.1/v1.2/v1.2.1 表延后 | **Accepted** | **Phase 1, 7, 7.5** | ORM 切片由 Phase 1 / 7 / 7.5 分担落地；schemas.py 全量；Alembic migration 边界清晰 |
| [0007](0007-ipo-postmarket-jsonb-extension.md) | IPOPostMarket 扩展 JSONB 字段对齐 CHECKPOINT_DAYS | **Accepted** | **Phase 1, 2, 7.5, 8** | spec §5 标量列保留 + JSONB 全 checkpoint 覆盖，双写约束 |
| [0008](0008-dcf-agent-dual-track-integration.md) | DCF agent 联动 — 算法借鉴 + 双轨保留 | **Accepted** | **Phase 2, 4, 7** | Phase 4 按 spec 自建，公式参考 DCF agent；iFind catalog 合并到 Phase 2；Phase 7 加 Excel 附件 adapter |
| [0009](0009-research-agent-framework-borrowing.md) | 港股研究agent 框架借鉴 — 模式参考 + 双轨保留 | **Accepted** | **Phase 5** | BaseAgent / ScoreCard / WorkflowExtras 模式借鉴；NACS 三件套必须 Phase 5 自建；7 agent 调研维度参考 |
| [0010](0010-debate-and-snapshot-design.md) | Phase 6 编排 — 辩论早停 + Snapshot 创建发位 | **Accepted** | **Phase 6, 7.5** | Jaccard 早停 + 3 轮硬上限；Devil 元层质疑；Phase 6 in-memory snapshot → Phase 7.5 替换为 PG；HITL 可配置 bypass；`operator.or_` reducer |
| [0011](0011-phase7-scope-and-deferrals.md) | Phase 7 范围 + 延期项 | **Accepted** | **Phase 7, 7.5, 8, 9** | MVP 实现 10 核心 router + 全套 middleware + auth (无 SSO) + SSE/WS 骨架 + reporting；reviews/proposals/drift 延 Phase 7.5；backtest 延 Phase 8；SSO 延 Phase 9 |
| [0012](0012-phase7.5-scope-and-substages.md) | Phase 7.5 范围 + 4 子阶段切片 | **Accepted** | **Phase 7.5** | 18 表 + 4 trigger + Registry PG 化 / Outcome+Event+Attribution+Review 闭环 / 状态机 + CodeMapper + EarningsComparator + Alerts / 三层调度器 + Airflow + 端到端晶泰 — 切成 7.5a→b→c→d 四子阶段 |
| [0013](0013-phase8-scope-and-substages.md) | Phase 8 范围 + 4 子阶段切片 | **Accepted** | **Phase 8** | 防泄漏 as_of_data + regime_detection / IC L-S t-stat metrics + NACS v8 baselines / walk-forward runner + Bayesian calibration + reports + 50+ 样本回测 / backtest router 收尾 — 切成 8a→b→c→d 四子阶段；继承 NACS market_environment_cache + 5 轮 iteration archive 作单调性约束 |
| [0014](0014-phase9-scope-and-substages.md) | Phase 9 范围 + 3 子阶段切片 | **Accepted** | **Phase 9** | NACS legacy 归档 / FullPipelineScorer + e2e 测试骨架 / 5 家案例 stub + tag v0.9 — 切成 9a→b→c 三子阶段 |
| [0015](0015-phase10-scope-and-substages.md) | Phase 10 范围 + 3 子阶段切片 | **Accepted** | **Phase 10** | drift_detector + attribution_aggregator + counterfactual + version_manager / adjustment_proposer + applier (强制 human gate) + reports / CLI + LEARNING_PROTOCOL + e2e 闭环 — 切成 10a→b→c 三子阶段 + tag v1.0 |
| [0016](0016-phase9-stragglers-cleanup-and-e2e-cli-parametrization.md) | Phase 9a 补归档 + 参数化 e2e CLI 入口 | **Accepted** | **Phase 9 (post-tag), Phase 10 prep** | 6 个 NACS 同源遗漏脚本归档/删除 + 删 workflows/ stub + 登记 `scripts/analyze_pdf.py` 参数化任务（堵住"一次性脚本积累"成因） |
| [0017](0017-llm-provider-kimi-moonshot.md) | LLM Provider: Anthropic Claude → KIMI/Moonshot | **Accepted** | **R4 (post-v1.0)** | 记录 commit `2582dab` 的 provider 切换决策（cost / latency / OpenAI SDK 兼容）；ADR 0002 标 Superseded；保留 Anthropic API key env slot 作 future fallback；YAML-driven 单点切换 |
| [0018](0018-ifind-incremental-loader-deferral.md) | iFind Incremental Loader Deferral | **Accepted** | **R3 (post-v1.0)** | 把 `HistoricalIPOLoader._upsert_from_ifind` + `ComparablePoolBuilder._ingest` 标 DEFERRED；ADR 0005 §Progress 对应条目 ✅ → ⚠️；stub 改 raise NotImplementedError 阻止 silent zero return |

---

## 按 Phase 反向索引（实施者快速查表）

启动某 Phase 前，按行扫描；带 ★ 的 ADR 是 spec 没说但必须做的事。

| Phase | 强制查阅的 ADR | 是否含 spec 外动作 |
|---|---|---|
| Phase 0 项目骨架 | 0001 / 0002 / 0003 / 0004 / 0005 | 是 — 已完成（搭建 NACS 资产继承的预埋） |
| Phase 1 核心基础设施 | 0001 / 0002 / **0006** / **0007** | 是 — ADR 0006 限定 ORM 切片；ADR 0007 加 IPOPostMarket JSONB |
| Phase 2 数据层 | **★ 0005 §1 + §4** | **是** — `scripts/migrate_sqlite_to_pg.py` ETL + `tests/unit/data/test_no_lookahead.py` |
| Phase 3 招股书处理 | 0003 / 0004 | 否 |
| Phase 4 估值模型层 | **★ 0005 §2** / **★ 0008** | **是** — ensemble.py regime<0 → SKIP 硬门；DCF/Comps 公式参考 DCF agent (ADR 0008) |
| Phase 5 Agent 层 | **★ 0005 §2 + §5** / **★ 0009** | **是** — 3 个 agent 必须接入 NACS 信号（Regime / Cluster Bonus / Theme Heat）；BaseAgent / ScoreCard / WorkflowExtras 参考港股研究agent (ADR 0009) |
| Phase 6 编排 + Critic + Synthesizer | 0001 / 0002 / **★ 0010** | **是** — 辩论 Jaccard 早停 + Devil 元层质疑 + Phase 6 in-memory snapshot (Phase 7.5 替换 PG) + HITL bypass 默认 + `operator.or_` reducer |
| Phase 7 报告 + API + UI 集成层 | **0008** / **★ 0011** | **是** — ADR 0011 定 MVP 范围；reviews/proposals/drift/backtest 延后；SSO 延 Phase 9；in-memory audit/chat/whatif 沿用 Phase 6 snapshot 模式 |
| Phase 7.5 预测档案 + 生命周期 | **★ 0012** | **是** — ADR 0012 切 7.5a/b/c/d 4 子阶段；7.5a 同时收掉 ADR 0011 遗留的 Phase 7 in-memory PG 化；7.5b 实装 reviews/proposals/drift |
| Phase 8 回测与校准 | **★ 0005 §3** + **★ 0013** | **是** — ADR 0013 切 8a/b/c/d 4 子阶段；`backtest/{metrics,calibration,regime_detection}.py` 继承 v8 baseline + IC 三件套；8d 收掉 ADR 0011 最后遗留的 backtest router |
| Phase 9 端到端验证 | **★ 0005 §Progress 归档段** + **0014** + **0016**（post-tag stragglers） | **是** — 把 themes/ / nacs_real.db / NACS 顶层脚本归档到 legacy/；ADR 0016 收尾 9a 漏归档 + 登记 Phase 10 前置任务 |
| Phase 10 持续学习闭环 | **★ 0015** (实施切片 10a/b/c) + **★ 0016** (前置任务) + 0005（参考归因部分） | **是** — ADR 0015 切 10a/b/c 三子阶段；启动前先做 ADR 0016 第三类参数化 e2e CLI |

---

## 写新 ADR 的约定

- 文件名：`0NNN-short-slug.md`（NNN 单调递增，slug 用 kebab-case）
- 必须含字段：Status / Date / Deciders / Context / Decision / Consequences
- 若 ADR 跨 Phase 强制要求实施动作，必须在末尾加 `Progress` 段，列可勾选 checklist
- 写完后必须更新本 README 两张表（全清单 + 按 Phase 反向索引），并在 CLAUDE.md「Phase 启动前必读」加入条目
- 若 ADR 修订原决策，老 ADR 标记 `Superseded by: NNNN`，新 ADR 标记 `Supersedes: NNNN`
