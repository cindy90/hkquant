# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
The project follows the Phase-based versioning of `PROJECT_SPEC.md` §4.

---

## [v0.6] — Phase 6 完成: 编排 + Critic + Synthesizer + Snapshot (2026-05-16)

### Added
- **`src/hk_ipo_agent/orchestrator/`** LangGraph 主图 6 个模块：
  - `states.py` — `AnalysisState` TypedDict + `operator.or_` reducer (agent_outputs) + 自定义 `_merge_extras` reducer (NACS 信号字段)
  - `nodes.py` — 13 node 工厂 (`make_nodes()`)：6 个 NACS-aware fanout agent + valuation + debate + cross_check + synthesize + create_snapshot + hitl_wait + report
  - `edges.py` — 3 个 conditional router (`route_after_snapshot` / `route_after_hitl` / `route_after_validation`)
  - `graph.py` — `build_main_graph()` 主图组装；START → 6 parallel agents → valuation → debate → cross_check → synthesize → create_snapshot → (hitl?) → report
  - `hitl.py` — `approve()` / `reject()` / `hitl_enabled()` 人工审核接口；默认 bypass (ADR 0010 §4)
  - `checkpoint.py` — `get_checkpointer()` 返回 LangGraph `MemorySaver`（Phase 7 替换 PostgresSaver）
- **`src/hk_ipo_agent/critic/`** 辩论 + 历史对照 5 个模块：
  - `bull.py` / `bear.py` — Bull-Bear 论点生成（Sonnet）；Bear 必读 Regime Gate 上下文
  - `devils_advocate.py` — 元层质询，**不站队**（ADR 0010 §2）
  - `debate_graph.py` — **Jaccard 早停 + 3 轮硬上限**：char-level CJK-friendly tokenizer；当 `jaccard(bull, bear) ≥ 0.6` 触发收敛
  - `cross_checker.py` — 历史样本对照（Phase 6 确定性，Phase 8 升级 LLM）
- **`src/hk_ipo_agent/synthesizer/`** Opus 决策合成 5 个模块：
  - `scoring.py` — `build_scorecard()` 综合 7 agent overall + NACS 修正（regime ±20 / cluster +5 / gilding -10 / theme heat ±5）
  - `decision_engine.py` — 硬规则 + 软阈值：`regime_score < 0` / no_models / `ai_gilding + narrative_risk ≥ 70` → SKIP；overall ≥ 75 / 60 / 45 分别 PARTICIPATE / PARTIAL / WAIT
  - `price_range.py` — 从 ensemble 派生 (low, fair, high)；regime gate 防御层强制清零；borderline regime ±10% 加宽
  - `trigger_rules.py` — 监控触发规则；gilding / regime-skip 各有专属 rule
  - `synthesizer.py` — Opus 4.7 顶层合成 + Pydantic constrained JSON 输出；LLM 不能覆盖硬规则
- **`src/hk_ipo_agent/prediction_registry/`** Phase 6 最小集 2 模块：
  - `snapshot.py` — `build_snapshot()` + `compute_input_hash()` (SHA-256 over 5 artifacts) + `verify_snapshot()` 重读完整性
  - `registry.py` — `PredictionRegistry` in-memory append-only store + 进程级 singleton (`get_registry()`)；Phase 7.5 替换 PostgreSQL
- **`docs/decisions/0010-debate-and-snapshot-design.md`** — ADR 0010：辩论 Jaccard 早停 + Devil 元层质疑 + Phase 6 in-memory snapshot + HITL bypass + `operator.or_` reducer
- **`prompts/debate/{bull,bear,devils_advocate,cross_checker}.md`** + **`prompts/system/{synthesizer,orchestrator}.md`** v1.0 全部补齐
- **`config/`** OrchestratorSettings 新增（`enable_hitl` / `debate_max_rounds` / `debate_jaccard_threshold` / `system_version`）
- **`tests/unit/{critic,synthesizer,prediction_registry,orchestrator}/`** 69 新单测：
  - 12× debate + cross_checker (Jaccard / tokenize / 早停 / max rounds)
  - 22× synthesizer (scoring / decision_engine / price_range / trigger_rules)
  - 11× prediction_registry (snapshot integrity / registry CRUD / singleton)
  - 11× orchestrator (states reducer / graph compile / edges)
  - **1× DONE-condition full-pipeline smoke**（START → 7 agents → valuation → debate → cross_check → synthesize → create_snapshot → report；验证 NACS extras 回填 + snapshot 强制创建）

### Verified (Phase 6 DONE)
- ✅ LangGraph 主图编译成功（13 nodes + START/END）
- ✅ 6 个 NACS-aware fanout agent 通过 `operator.or_` reducer 合并到 `agent_outputs`，无覆盖；`extras` 通过自定义 `_merge_extras` 累积 NACS 信号
- ✅ 辩论 Jaccard 早停：bull/bear 相似 → 1 轮收敛；分歧 → 跑满 max_rounds
- ✅ Synthesizer 硬规则不可被 LLM 覆盖：regime gate / no models / AI gilding 三类强制 SKIP
- ✅ **CLAUDE.md HARD invariant**: `synthesize → create_snapshot → report` 顺序不可破；snapshot 失败整流程失败
- ✅ Snapshot SHA-256 hash 复算一致；Pydantic FrozenModel 拒绝任何 mutation
- ✅ HITL 默认 bypass (`enable_hitl=False`)；生产环境可通过 env var 切换
- ✅ ruff strict + mypy strict 全通过（Phase 6 共 16 新模块 + 全仓 162 source files）
- ✅ **389 unit tests pass**（v0.5 320 + 69 新增）

### Notes
- 辩论 LLM 模型：Bull/Bear/Devil 全部 Sonnet 4；Synthesizer Opus 4.7（spec §1 强制）
- Devil 元层质疑（ADR 0010 §2）专注 data quality / causal validity / unaddressed risks，不站队
- Phase 6 in-memory snapshot 多 worker 不共享 → Phase 7.5 替换 PostgreSQL 后才能多进程部署
- Jaccard 阈值 0.6 经验定；Phase 8 calibration 可用回测样本调优
- `cross_checker.py` Phase 6 是确定性 statistics.median；Phase 8 升级到 LLM 智能最近邻匹配
- ADR 0005 §Progress Phase 6 条目无变动（NACS 三件套已在 Phase 5 接入，本 Phase 仅消费）
- 工作量实际 ≈ 3 天（spec §3.8 / §8 估 2-3 天）

---

## [v0.5] — Phase 5 完成: Agent 层 (7 expert agents + NACS 三件套 + tools) (2026-05-16)

### Added
- **`src/hk_ipo_agent/agents/`** 7 个 expert agent 全部实装（async + LangGraph-ready + 强制 citation + 强制 ScoreCard Pydantic 输出）：
  - `base.py` — `BaseAgent` ABC + `AgentContext` 跨 agent 上下文 + frontmatter-aware `load_prompt()` + LLM 调用 wrapper（cost / runtime 自动归集）
  - `workflow_extras.py` — `WorkflowExtras` 强类型容器，**首批 NACS 字段**: `regime_score` / `cluster_bonus_multiplier` / `cluster_groups` / `theme_heat` / `ai_gilding_flag`
  - `scoring.py` — `BaseScoreCard` + 7 个子类（Fundamental / Industry / Valuation / Policy / Liquidity / Cornerstone / Sentiment）+ `extract_json_block()` / `schema_instruction()` 助手
  - `policy_agent.py` — **NACS Regime Gate（ADR 0005 §2）**: 确定性算 median 30d return of HK IPOs in [pricing-120d, pricing-30d]，写入 `ctx.extras.regime_score`；负值触发估值 ensemble 硬门 SKIP
  - `cornerstone_signal_agent.py` — **NACS Cluster Bonus（ADR 0005 §2）**: ultimate_holder 聚类，≥2 同源 → 1.10x / ≥2 cluster → 1.20x multiplier
  - `sentiment_agent.py` — **NACS Theme Heat + AI Gilding（ADR 0005 §5）**: 读 `themes/heat_today.json` + `theme_definitions.json` + `ai_revenue_manual.json`；AI 占比 <10% 但声称 AI 触发 narrative_risk ≥70
  - `fundamental_agent.py` — 业务质量 / 财务健康 / 治理三维；确定性原语含 revenue CAGR / gross margin / top-1 客户集中度
  - `industry_agent.py` — 竞争位置 / 增长前景 / peer 估值；含 peer multiple summary（p25/p50/p75）
  - `valuation_agent.py` — 驱动 Phase 4 `run_ensemble()` 全 6+ 模型（含 industry 特化），把 `ValuationEnsembleOutput` stash 到 `ctx.extras.misc['valuation_output']`
  - `liquidity_agent.py` — 流通量 / 锁定期 / 港股通三维；按 listing_type 启发式 tier southbound eligibility
- **`src/hk_ipo_agent/agents/tools/`** 4 个 agent-injectable tool：
  - `prospectus_tool.py` — 包 `prospectus.qa.ProspectusQA`（citation 强制由其本身 raise）
  - `ifind_tool.py` — 包 `IFindClient` 的 4 个 production-ready endpoint（ipo_history / macro / valuation_snapshot / ah_premium_history）
  - `kb_tool.py` — `data/knowledge_base/` + `themes/` legacy 双路径读 themes JSON / market_env / AI revenue manual + `match_themes()` 关键词匹配
  - `web_tool.py` — Phase 5 stub（is_stub=True）；真实 provider 待 Phase 9
- **`prompts/agents/`** 7 个 prompts v1.0 全部补齐 frontmatter（role / version / inherited_inputs / score_card）+ body
- **`docs/decisions/0009-research-agent-framework-borrowing.md`** — ADR 0009 港股研究agent 双轨借鉴方案
- **`tests/unit/agents/`** 55 新单测：
  - 6× workflow_extras（dict-style API + reserved keys）
  - 9× scoring（json block + schema_instruction + 7 ScoreCard 校验）
  - 3× base（frontmatter parser + AgentContext）
  - 6× policy_agent（regime override + iFind aggregation + LLM stub）
  - 8× cornerstone_agent（clustering ladder + agent run）
  - 8× sentiment_agent（AI gilding + theme match + agent run）
  - 12× other_agents（fundamental / industry / valuation / liquidity 各原语 + run）
  - **3× DONE-condition smoke**（7 agents fanout + asyncio.gather + NACS 信号回填）

### Verified (Phase 5 DONE)
- ✅ 7 个 expert agent 通过 `asyncio.gather` 并发执行，每个产 `AgentOutput`（含 `scores` / `key_findings` / `data_sources_used` / `cost_usd` / `runtime_seconds`）
- ✅ ADR 0005 §2 + §5 三件套全部回填到 `ctx.extras`：`policy_agent` 写 `regime_score`、`cornerstone_signal_agent` 写 `cluster_bonus_multiplier`、`sentiment_agent` 写 `theme_heat` + `ai_gilding_flag`
- ✅ 每个 agent 都有"deterministic primitive + LLM narrative + ScoreCard 覆盖"三段式：核心信号代码强制覆盖（不信任 LLM），叙事由 LLM 生成
- ✅ Citation 强制：所有 `Finding` 至少 1 个 page；prospectus_tool 沿用 Phase 3 `CitationRequiredError`
- ✅ Frontmatter parser 支持 inline `# comment` 剥离
- ✅ ruff strict + mypy strict 全通过（agents/ 16 files + 全仓 162 files）
- ✅ **320 unit tests pass**（v0.4 265 + 55 新增）

### Notes
- BaseAgent 模式参考港股研究agent 但改造为 async + 强制 Pydantic（ADR 0009 §1）；不复制代码
- WorkflowExtras 直接借鉴港股研究agent extras.py，扩展加入 4 个 NACS 字段
- 多 provider 路由 / chromaDB / hkquant SQLite 全部**未**移植（spec 单 Claude / Qdrant / PG）
- LangGraph 主图编排是 Phase 6 工作；Phase 5 提供独立可调用 agent，便于 Phase 6 fanout
- KB 双路径读取（`data/knowledge_base/themes/` 优先，`themes/` legacy fallback）— Phase 9 归档时再迁移

---

## [v0.4] — Phase 4 完成: 估值模型层 (5 single + 2 industry + ensemble + Regime Gate) (2026-05-16)

### Added
- **`src/hk_ipo_agent/valuation/`** 估值层 10 个模块全部实装：
  - `base.py` — `ValuationModel` ABC + `MarketData` 运行时上下文 + `PeerMultiples` + `distribution_from_samples()` + `_not_applicable()` 助手
  - `monte_carlo.py` — **10000 路径 MC 引擎**（PROJECT_SPEC.md §3.7 要求）+ 7 个 `Distribution` 类（Constant / Normal / LogNormal / Uniform / Triangular / Bernoulli / FromArray）；64-bit seed 复现保证
  - `comparable.py` — PS+PE 分位数（DCF agent session-h.md L1-3 借鉴；outlier filter `0 < m < 200`；PE 仅在盈利时启用；50/50 blend）
  - `dcf.py` — 5y 显式预测 + Gordon TV（DCF agent session-f.md L120-200 借鉴；UFCF = NOPAT + DA − CapEx − ΔWC；EV→Equity bridge；wacc-g 守护）
  - `pre_ipo_anchor.py` — last_round × (1 − discount)，Triangular(−0.20, 0.10, 0.50) 默认折扣分布
  - `ah_premium.py` — AH 对仅适用；H = A × (1 − premium_pct)；历史经验分布优先 + 行业 fallback `Triangular(0.15, 0.30, 0.40)`
  - `milestones.py` — 18C-pre / 18A 阶段实物期权 NPV；默认 4 阶段阶梯（PoC / Pilot / Commerc / Scale）；Bernoulli × LogNormal × Triangular 组合
  - `ensemble.py` — 多模型加权融合 + **Regime Gate 硬门**（ADR 0005 §2：`regime_score < 0 → SKIP`，price_range 强制清零）；YAML 权重 + applicable 子集 renormalize
  - `industry/ai_arr.py` — AI/SaaS ARR 倍数（LogNormal 中位 7x）；ASCII 词边界匹配避免 "AI" 误匹配 "retail"
  - `industry/semiconductor.py` — EV/Sales + 周期相位调整（trough/mid/peak ×0.70/1.00/1.30）
- **`config/valuation_weights.yaml`** — 6 个 `ListingType` 的初始权重，Phase 8 校准
- **`docs/decisions/0008-dcf-agent-dual-track-integration.md`** — DCF agent 双轨联动决策（spec Phase 4 自建 + DCF agent skill 保留 + iFind 合并）
- **`tests/unit/valuation/`** 58 新单测：
  - 12× monte_carlo（分布 + run_mc + seed 复现）
  - 8× base（distribution helper + MarketData + ABC）
  - 5× comparable、4× dcf、4× pre_ipo_anchor、4× ah_premium、4× milestones
  - 7× ensemble（含 Regime Gate 硬门）、7× industry
  - **3× DONE-condition smoke test**（4+ 模型 / Regime Gate 硬门 / 18C-pre milestones）

### Verified (Phase 4 DONE)
- ✅ `ProspectusExtraction` → 4+ 独立 applicable 模型 → 10k MC → 加权 ensemble → `ValuationEnsembleOutput`（含 `implied_price_range`）
- ✅ Regime Gate 硬门：`market_data.regime_score < 0` 强制清零 `implied_price_range`，notes 记录触发
- ✅ Pre-commercial 18C/18A 走 milestones（DCF / Comparable 自动 not_applicable）
- ✅ AH 对走 ah_premium 模型，非 AH 自动跳过
- ✅ AI 行业匹配触发 `AIARRValuation`；半导体匹配触发 `SemiconductorValuation`
- ✅ ruff strict + mypy strict 全通过（valuation/ 15 files + 全仓 160 files）
- ✅ **265 unit tests pass**（v0.3 186 + 79 新增）

### Notes
- 公式来源全部注释标记 DCF agent session-f.md / session-h.md 行号（ADR 0008 §Negative mitigation）
- iFind catalog（52 验证过的指标）已在 Phase 2 合并到 `data/knowledge_base/`，Phase 4 模型可立即消费 peer multiples
- 行业骨架仅落地 ai_arr / semiconductor 2 个；biotech_18a / ev_battery / robotics 保留 TODO（Phase 8 校准时填）
- NACS v8 post_adjustments（×0.70 18C 高估、AH 套保分层）**未**移植；Phase 8 重新校准后再决定是否启用（ADR 0005 §3）
- Phase 7 投决备忘录 Excel 附件 adapter（`reporting/exporters/dcf_excel.py`）留待 Phase 7

---

## [v0.3] — Phase 3 完成: 招股书处理 (Parse / Chunk / Embed / Qdrant / RAG QA) (2026-05-16)

### Added
- **`src/hk_ipo_agent/prospectus/`** 9 个模块全部实装：
  - `schema.py` — 重导 Phase 1 `ProspectusExtraction` + 新增 `ParsedBlock` / `ParsedTable` / `ParsedDocument` / `ParserBackend` / `Chunk` 数据类
  - `parser.py` — **LlamaParse 主路径 + PyMuPDF 兜底**（ADR 0004 决策）；自动降级当 `LLAMA_CLOUD_API_KEY` 未配置；每个 block 保留 `(page, char_offset, bbox)` 可溯源元数据
  - `chunker.py` — 章节感知分块（中英双语 IPO 章节识别 + section 边界 flush + 表格独立 chunk + 确定性 chunk_id sha256）
  - `embeddings.py` — 三层 provider：**HashEmbeddings**（CI fallback 0 依赖）/ **BGEEmbeddings**（本地 BAAI/bge-large-zh-v1.5）/ **VoyageEmbeddings**（云）；`get_embedding_provider()` 自动降级
  - `vector_store.py` — `ProspectusVectorStore` async Qdrant 封装，按 prospectus_id 隔离 collection
  - `retriever.py` — `HybridRetriever`（dense vector + 进程内 BM25 + RRF fusion）
  - `extractor.py` — `ProspectusExtractor` LLM 抽取调度器；Sonnet → Opus 自动降级 + 失败标 `needs_human_review`
  - `qa.py` — **`ProspectusQA.ask()` 强制 citation**（无 chunks → `CitationRequiredError`；LLM 幻觉 chunk_id → 过滤；最终空 citation 列表 → raise）
  - `validators.py` — 5 个一致性校验（negative_revenue / top1_exceeds_top5 / shareholder_pct_sum / no_risk_factors / ch18c_revenue_inconsistent）
- **`prompts/extraction/`** 6 个抽取 prompts 全部带 frontmatter：`prospectus_section_router` / `financials_extractor` / `business_extractor` / `risks_extractor` / `shareholders_extractor` / `ch18c_qualifier`
- **`tests/unit/prospectus/`** 33 新单测（chunker × 9, parser_fallback × 5, embeddings × 7, qa_citation × 6, validators × 6）
- **`tests/integration/test_rag_qa.py`** 2 新集成测试：端到端 synthetic PDF → 解析 → chunk → embed → Qdrant upsert → mocked QA → 验证 citation 含正确 page。Qdrant 不可达时优雅 skip

### Verified (Phase 3 DONE)
- ✅ 给定一份 synthetic 3-page 招股书 PDF，<1 秒走通完整 pipeline 输出带 citation 的 Answer
- ✅ Qdrant collection 按 prospectus_id 隔离（`prospectus_<id>`）+ idempotent upsert via deterministic chunk_id
- ✅ `qa.ask()` 必返回 citation；空 citation / 幻觉 chunk_id 全部 raise `CitationRequiredError`
- ✅ Parser 自动 LlamaParse → PyMuPDF 降级（`prefer_llamaparse=True` 默认，无 API key 时 silent fallback）
- ✅ ruff strict + mypy strict 全通过 (160 source files)
- ✅ **186 tests pass** (175 unit + 11 integration) — 比 v0.2 的 151 +23%

### Notes
- LlamaParse 真实路径留待 `LLAMA_CLOUD_API_KEY` 配齐后由 Phase 9 端到端 golden case 触发；接口/降级路径 100% 测试覆盖
- BGE 本地 embeddings 留待 Phase 5 agent 层启用（需 `uv sync --extra embeddings-local` 装 torch ~2GB）；默认 HashEmbeddings 适合 CI 但**没有语义性**
- Phase 3 prompts 内容是结构性占位；Phase 5 会用 agent 反馈循环迭代提示词质量
- 真实招股书 PDF fixture 留待 Phase 9（晶泰控股 2228.HK golden case）

---

## [v0.2] — Phase 2 完成: 数据层 + NACS SQLite ETL (2026-05-16)

### Added
- **`src/hk_ipo_agent/data/repositories/`** (6 文件): `BaseRepository` + `IPOEventRepository` / `IPOPricingRepository` / `IPOPostMarketRepository` / `CornerstoneInvestorRepository` / `CornerstoneInvestmentRepository` / `ComparableCompanyRepository` / `SponsorRepository` / `ProspectusDocRepository` / `ProspectusExtractionRepository`。`BaseRepository.bulk_upsert()` 用 PostgreSQL `INSERT ... ON CONFLICT` 实现幂等批量插入
- **`src/hk_ipo_agent/data/sources/ifind_client.py`**: 完整 iFinDPy SDK 异步包装；lazy import（无 iFinDPy 也可 import 类）+ 强制 `as_of_date` 防泄漏 + tenacity retry + token-bucket QPS 限流 + 4 个 endpoint（financials / ipo_history / comparable_companies / ah_premium）
- **`src/hk_ipo_agent/data/sources/hkex_scraper.py`**: httpx 异步 HKEXnews 爬虫骨架；尊重 robots.txt + rate limit + 流式下载 PDF
- **`src/hk_ipo_agent/data/builders/`** (5 文件): `HistoricalIPOLoader` / `CornerstoneProfileBuilder`（含 `cluster_report_for_ipo` 实现 ADR 0005 §2 Cluster Bonus 数据侧）/ `SponsorTrackBuilder`（24m 滚动 win rate）/ `ComparablePoolBuilder` / **`ThemeLoader`** (NEW，ADR 0005 §5 — 把 NACS `themes/` 5 个 JSON/CSV 拷贝到 `data/knowledge_base/themes/`)
- **`scripts/migrate_sqlite_to_pg.py`** 完整实现（ADR 0005 §1）：UUID5 namespace 稳定映射 + 幂等 UPSERT + 7 张 SQLite 表 → PG + market_environment_cache JSON fixture 导出
- **`tests/unit/data/`** (3 个新测试文件，22 测试): `test_etl_mappers.py`（UUID 稳定性 + 类型强制 + 映射逻辑）/ `test_no_lookahead.py`（5 测试覆盖 fiscal_year + period_end 双规则）/ `test_postmarket_consistency.py`（ADR 0007 §双写一致性）
- **`tests/integration/test_db_repositories.py`** (9 测试): 端到端 CRUD 对 PG，含 NACS 语料完整性断言（399/2014/2560/1592 行） + Cluster Bonus 数据侧验证 + 优雅 skip 当 PG 不可达

### Changed
- **IPOEvent.industry_code / Company.industry_code / ComparableCompany.industry_code**: VARCHAR(20) → VARCHAR(120)（NACS gics_l2 最长 34 字符，留余量）
- **CornerstoneInvestor.name_zh / name_en**: VARCHAR(200) → VARCHAR(500)；parent_org / ultimate_holder: 200 → 300（NACS 实际最长 429）
- **ETL `ultimate_holder` 字段映射修正**: 原计划从 `cornerstone_master.parent_entity` 取，实际 NACS 该字段全空；改为从 `ipo_cornerstone_link.ultimate_holder` 按 cornerstone_id 投票聚合最频繁值。修正后 cluster bonus 检测在 50 IPO 样本中确认有效

### Database
- 2 个新 Alembic migrations:
  - `20260516_0415_e82407eae19a_phase2_widen_industry_code` (industry_code: 20→120)
  - `20260516_0417_7efd3de0efa7_phase2_widen_cornerstone_strings` (cornerstone names 200→500, parent_org/ultimate_holder 200→300)
- PG corpus 实测填充：**399 IPO 事件 + 399 定价 + 398 后市表现 + 2,014 基石投资者（含 1,770 别名合并）+ 2,560 IPO-投资者关联 + 399 公司 + 1,592 财务快照 + 55 行 market env cache JSON fixture**

### Verified (Phase 2 DONE)
- ✅ `scripts/dev.py migrate` 等价 make migrate 成功 (3 个 alembic migrations all applied)
- ✅ `scripts/migrate_sqlite_to_pg.py` 实跑 (幂等：再跑数字一致 + ON CONFLICT 不重复插入)
- ✅ ruff strict + mypy --strict 全通过 (160 source files)
- ✅ **151 tests pass** (142 unit + 9 integration) — 比 v0.1 的 90 testes +68%
- ✅ ADR 0005 §Progress 中 5 个 Phase 2 条目全部勾选

### Notes
- ADR 0005 §1 表里给的旧数字 (1,314 cornerstones / 1,604 links / 1,051 aliases) 是 ADR 草稿期 NACS 早期快照；实际迁移时为 **2,014 / 2,560 / 1,770**。文档已就地更新
- `iFind` 增量加载留待 Phase 2.1（需 iFinDPy 凭证就绪 + 同花顺 QuantAPI 客户端运行）
- 5 张 NACS 表故意不迁移：`cornerstone_performance_asof` (Phase 7.5 重算) / `panel_snapshots` (替换为 prediction_snapshots) / `nacs_predictions` (NACS 特有) / `price_history` (空) / `sponsor_performance_asof` (空)
- **`comparable_companies` 表故意为空**：NACS 没有可比公司语料可种子。设计为 Phase 4 `valuation/comparable.py` 按需通过 iFind 动态构建并写入此表；Phase 2 只就位 ORM 表 + builder 接口 + iFind 通路。详见 [`comparable_pool_builder.py`](src/hk_ipo_agent/data/builders/comparable_pool_builder.py) 文档

---

## [v0.1] — Phase 1 完成: 核心基础设施 (2026-05-16)

### Added
- **`src/hk_ipo_agent/common/`**: 8 个模块
  - `enums.py` — 全部 25+ 枚举（StrEnum）+ `VALID_TRANSITIONS` + `ROLE_PERMISSIONS` + `CHECKPOINT_DAYS` 常量
  - `exceptions.py` — `HkIpoAgentException` 根 + 25+ 子类（含 ApiError 子类的 RFC 7807 字段）
  - `schemas.py` — 40+ Pydantic 模型覆盖 §6 v1.0/v1.1/v1.2/v1.2.1 全切片；`StrictModel`/`FrozenModel` 基类
  - `logging.py` — structlog JSON 配置 + `LogContext` 上下文绑定 + 敏感字段脱敏
  - `settings.py` — pydantic-settings v2 分层加载（defaults < YAML < .env < env）+ 5 个辅助 YAML loader（llm_models / data_sources / agents / valuation_weights / regulations）
  - `cache.py` — async `_MemoryCache` + `@cached` 装饰器 + `_RedisCache` 骨架 + `CacheBackend` Protocol
  - `utils.py` — `utcnow` / `canonical_json` / `sha256_hex` / `coerce_decimal` / `safe_div`
  - `llm_client.py` — Anthropic AsyncAnthropic 完整封装 + tenacity 重试 + Anthropic prompt caching + 每日成本守卫 + `acomplete_json(response_model=...)` 结构化输出（含 schema-validation 失败时重提交反馈）
- **`src/hk_ipo_agent/data/`**:
  - `database.py` (NEW) — `get_engine()` + `async_session_factory()` + `get_session()` FastAPI 依赖
  - `models/` 7 文件 — v1.0 基础 12 张表 ORM (`Base` + 6 实体文件)，含 `UUIDMixin` / `TimestampMixin` / 通用 `__repr__`
  - `migrations/alembic.ini` + `env.py` + `script.py.mako` + 初始 migration `20260516_0337_3760e227ac2a_phase1_v10_base_tables.py`（已应用到 PG 16）
- **`tests/conftest.py`** — 共享 fixtures (mock_llm_client / sample_extraction / sample_ipo_event / sample_cornerstone / sample_decision / settings_override / frozen_now)
- **`tests/unit/common/`** — 7 测试文件 70 测试，覆盖率 ≥ 93%（含 LLMClient retry exhaustion 路径）
- **`tests/unit/data/test_orm_models.py`** — 10 ORM smoke tests
- **`scripts/dev.py`** — 跨平台 Python 等价 Makefile（Windows 用户无需 GNU make）
- **`docs/decisions/0006-phased-orm-rollout.md`** — ORM 分阶段落地 ADR
- **`docs/decisions/0007-ipo-postmarket-jsonb-extension.md`** — IPOPostMarket JSONB 字段对齐 CHECKPOINT_DAYS

### Changed
- `pyproject.toml`：新增 `psycopg[binary]>=3.2`（sync alembic 用）；ruff `RUF001/002/003` 加入 ignore 列表容纳 × ≥ 等业务符号
- `IPOPostMarket` ORM：新增 `returns_by_day` + `cornerstone_held_pct_by_day` JSONB 字段（spec §5 标量列保留）
- `CLAUDE.md` 新增 "Phase 启动前必读" section 强制 Phase → ADR 路由
- `docs/decisions/README.md` 新增 0006 / 0007 ADR 索引
- `README.md` 新增 Windows `scripts/dev.py` 包装说明

### Verified (Phase 1 DONE)
- ✅ `make migrate` 等价命令成功（`uv run python scripts/dev.py migrate` 已 apply 13 表到 PG）
- ✅ 90 单元测试通过；总覆盖率 96%（`llm_client.py` 97% / `utils.py` 93%）
- ✅ ruff strict (含 RUF/PL/SIM/RET/C4/B/UP) + mypy --strict 全通过（160 source files）
- ✅ LLMClient 完整 mock 覆盖含 retry exhaustion 路径与 JSON 输出 schema 验证重试

---

## [v0.0] — Phase 0 完成: 项目骨架 (2026-05-16)

### Added
- 完整目录树（spec §2 全部 188 个 Python 模块 stub + docstring 占位）
- 顶层配置：`pyproject.toml` (uv-managed, spec §1 全栈依赖) / `Makefile` / `docker-compose.yml` (postgres:16 + qdrant + redis) / `.env.example` / `.pre-commit-config.yaml` / `.gitignore` / `README.md` / `CLAUDE.md`
- `config/` — 5 个根 YAML + 4 个 regulations/ 版本化规则文件
- `prompts/` — 19 个提示词文件含 frontmatter
- `docs/` — 14 个章节文档占位
- `docs/decisions/` — ADR 0001-0005:
  - 0001 Use LangGraph for orchestration
  - 0002 Use Claude (Sonnet 4 + Opus 4.7 for Synthesizer)
  - 0003 Use Qdrant over Chroma
  - 0004 LlamaParse primary + PyMuPDF fallback
  - **0005 NACS v8 遗产资产迁移到 spec v1.2.1**（Phase 2/4/5/8/9 强制 checklist 15 条）
- `notebooks/` 6 个 .ipynb 占位
- `workflows/` 3 个 YAML 工作流定义
- `data/{raw/prospectuses,processed/extractions,knowledge_base,benchmarks/backtest_runs,samples}/` 含 .gitkeep
- `tests/{unit,integration,e2e,fixtures,golden}/` 结构
- `scripts/` 7 个 CLI stub
- `PROJECT_SPEC.md` + `PROJECT_SPEC_UI.md` 复制到根

### Architecture Decisions
- 原地重构（保留仓库 git 历史）；NACS v8 代码废弃但数据资产 + 实证 know-how 通过 ADR 0005 迁移到对应 spec 模块
- 新代码全部位于 `src/hk_ipo_agent/`；新配置位于 `config/`（单数，与旧 `configs/` 区分）

### Verified (Phase 0 DONE)
- ✅ `uv sync` 成功（核心依赖 + dev group 全装好）
- ✅ ruff check + mypy --strict 全通过（空骨架）
- ✅ `docker compose config` 校验通过
- ✅ ADR 0005 + 9 个 stub docstring 交叉引用 + 3 个 prompts `inherited_inputs` frontmatter 落地

---

## [Pre-Phase 0] — Legacy NACS v8

之前的 NACS v8 量化评分模型（`src/nacs_model.py` 等）保留在仓库中，由 ADR 0005 + ADR 0006 + ADR 0007 规划的 Phase 2 ETL + Phase 9 归档完成后才会清理到 `legacy/`。详见 git log 此 changelog 条目之前的记录。
