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
| Phase 5 Agent 层 | **★ 0005 §2 + §5** | **是** — 3 个 agent 必须接入 NACS 信号（Regime / Cluster Bonus / Theme Heat） |
| Phase 6 编排 + Critic + Synthesizer | 0001 / 0002 | 否 |
| Phase 7 报告 + API + UI 集成层 | **0008** | **是**（少量）— reporting/exporters/ 可加 dcf_excel.py adapter 调 DCF agent skill |
| Phase 7.5 预测档案 + 生命周期 | — | 否（spec §3.11 自包含） |
| Phase 8 回测与校准 | **★ 0005 §3** | **是** — `backtest/{metrics,calibration,regime_detection}.py` 继承 v8 baseline + IC 三件套 |
| Phase 9 端到端验证 | **★ 0005 §Progress 归档段** | **是** — 把 themes/ / nacs_real.db / NACS 顶层脚本归档到 legacy/ |
| Phase 10 持续学习闭环 | 0005（参考归因部分） | 否 |

---

## 写新 ADR 的约定

- 文件名：`0NNN-short-slug.md`（NNN 单调递增，slug 用 kebab-case）
- 必须含字段：Status / Date / Deciders / Context / Decision / Consequences
- 若 ADR 跨 Phase 强制要求实施动作，必须在末尾加 `Progress` 段，列可勾选 checklist
- 写完后必须更新本 README 两张表（全清单 + 按 Phase 反向索引），并在 CLAUDE.md「Phase 启动前必读」加入条目
- 若 ADR 修订原决策，老 ADR 标记 `Superseded by: NNNN`，新 ADR 标记 `Supersedes: NNNN`
