# ADR 0009: 港股研究agent 框架借鉴 — 模式参考 + 双轨保留

- **Status**: Accepted
- **Date**: 2026-05-16
- **Deciders**: project lead
- **Supersedes**: 无
- **Superseded by**: 无

## Context

用户本地另一项目 `D:\自定义工具\港股数据分析\港股基石建模\港股研究agent`（PyPI 包名 `tradingagent_hk`）已实现一套多 Agent 港股 IPO 研究系统：

- **形态**：人对话式工具，CLI 入口 + 同步 Python + 命令式 `workflow.py` 线性编排
- **触发**：用户运行 `cli.py <project_id>` → 顺序跑 8 个 agent → 输出 `FINAL_MEMO.md` Markdown
- **输出**：投决会级研究备忘录，每个 agent 完整 `full_report.md` + `brief (≤500 字)` 落盘
- **核心模式**：BaseAgent / TemplateAgent / ScoreCard / WorkflowExtras / SUMMARIZE-ANALYZE-DECIDE 三档 LLM tier
- **8 个 agent**：prospectus_analyst / industry / macro / comparable / cornerstone / sentiment / scarcity / bull/bear/debate / risk / decision

而 PROJECT_SPEC.md §3.6 / §7 + ADR 0005 §2/§5 要求的 Phase 5 是另一形态：

- **形态**：Python async + LangGraph 编排 + Pydantic `AgentOutput` 强制 + 强制 citation
- **触发**：LangGraph 主图 fanout 7 个 agent node 并行执行
- **输入**：`ProspectusExtraction` + `MarketData` + 上游 NACS 信号
- **输出**：每个 agent 输出 `AgentOutput`（scores / key_findings / uncertainty_flags / data_sources_used / cost_usd / runtime_seconds）
- **必须实现**：7 个 expert agent + 强制接入 NACS 信号三件套（regime_score / cluster_bonus / theme_heat）

两者**形态、消费者、数据流均不同**，与 ADR 0008（DCF agent）类似的双轨格局：
- 港股研究agent → 人看的 markdown 备忘录，单次约 5-10 分钟人工跑完
- Phase 5 → LangGraph node 消费的结构化 `AgentOutput`，<30 秒自动 fanout

但 7 个 agent 的**业务调研维度**（行业 HHI、宏观 regime gate 雏形、比价多维锚定、基石阵容评估、二级情绪、稀缺度）+ **架构模式**（BaseAgent 抽象 / ScoreCard 评分卡 / WorkflowExtras 强类型容器 / tier 路由 / prompt caching）是通用的，且已在真实案例验证过实现细节。

## Decision

**采用方案 A — 模式借鉴 + 双轨保留**，与 ADR 0008 同构。具体路径：

### 1. Phase 5 按 spec 自建，架构模式参考港股研究agent
- `src/hk_ipo_agent/agents/` 7 个模块按 spec §3.6 / §7 完全自建为 Python async 库
- **`BaseAgent` 抽象** → 参考 `src/agents/base.py` L49-62 的 (full_report, brief) 双输出模式，但强制 async + 加 citation + 输出 `AgentOutput`
- **`TemplateAgent` 样板** → 参考 `src/agents/_template.py` 的 system prompt + JSON schema 校验、参考 prompt caching 策略，但用 Anthropic 原生 `cache_control: ephemeral`
- **`WorkflowExtras` 跨 agent 共享容器** → 直接参考 [extras.py](D:/自定义工具/港股数据分析/港股基石建模/港股研究agent/src/agents/extras.py)，扩展加入 NACS 信号字段（`regime_score`, `cluster_bonus_multiplier`, `theme_heat`, `ai_gilding_flag`）
- **`ScoreCard` Pydantic 评分卡** → 参考 [feedback/models.py](D:/自定义工具/港股数据分析/港股基石建模/港股研究agent/src/feedback/models.py) 的 `AgentScoreCard` 基类，加入 `evidence_pages` 字段
- **三档 tier (Haiku / Sonnet / Opus)** → 概念借鉴，spec 强制单 provider Claude，丢弃 Kimi / DeepSeek 分支
- **辩论 / Critic 早停（Jaccard 相似度）** → 留到 **Phase 6 Critic / Bull-Bear-Devil 子图** 时再借鉴

### 2. 港股研究agent 作为独立 skill 完整保留
- 不修改、不重构 `D:\自定义工具\港股数据分析\港股基石建模\港股研究agent\` 任何文件
- 继续服务原有场景：人工 IPO 研究 + 投决备忘录 markdown 输出
- 同步命令式 + chromaDB + 多 provider 形态对人工研究有不可替代价值，spec Phase 5 的 LangGraph async 形态代替不了

### 3. NACS 信号三件套 — 必须 Phase 5 全部自建
港股研究agent 的 macro.py / cornerstone.py / sentiment.py 都**没有** NACS 信号接入（详见 ADR 0005 §2/§5 校验报告）：

| NACS 信号 | 必须接入的 agent | 港股研究agent 现状 | Phase 5 落地方式 |
|---|---|---|---|
| `regime_score` | `policy_agent` | macro.py 只有 HSI vol，无 NACS regime gate | 从零写：median 30d return of HK IPOs in [pricing-120d, pricing-30d]；数据源 PG `ipo_postmarket` |
| `cluster_bonus_multiplier` | `cornerstone_signal_agent` | cornerstone.py 仅统计阵容，无聚类 | 从零写：ultimate_holder 聚类 ≥2 → bonus；数据源 `data/builders/cornerstone_profile_builder.py` |
| `theme_heat` + `ai_gilding_flag` | `sentiment_agent` | scarcity.py 有 theme_classifier 但无热度 | 从零写：读 `data/knowledge_base/themes/heat_today.json`；AI 收入 <10% × 0.85 镀金检测 |

这 3 个信号是 ADR 0005 实证基线（regime≥0 60d IC=+0.247 / cluster≥2 +22% / theme heat 主题情绪）的核心载体，**Phase 5 失败的就是 Phase 8 校准的失败**。

### 4. 7 个 agent 业务调研维度参考
| Phase 5 agent | 港股研究agent 对应文件 | 借鉴维度（不复制代码） |
|---|---|---|
| `fundamental_agent` | `prospectus_analyst.py` | 业务模式 / 财务质量 / 治理结构 / 募资用途 4 大块 |
| `industry_agent` | `industry.py` + `tech_trend.py` | TAM/SAM/SOM / HHI 计算 / 竞争格局 / 技术发展 |
| `valuation_agent` | `comparable.py` | 调本仓 `valuation/` 子模块（已 Phase 4 完成），不直接借鉴 |
| `policy_agent` | `macro.py` + `extras.py` regime 字段 | HSI vol / IPO 窗口 / 恒指估值，**外加 NACS regime_score** |
| `liquidity_agent` | `scarcity.py` | 存量稀缺 / 流量稀缺 / pipeline 排队效应 |
| `cornerstone_signal_agent` | `cornerstone.py` | 阵容评估 / 锁定期分析 / 保荐人 track record，**外加 NACS ultimate_holder cluster** |
| `sentiment_agent` | `sentiment.py` | 暗盘 / 同期 IPO / 媒体情绪，**外加 NACS theme heat + AI gilding** |

## Consequences

### Positive
- **Phase 5 架构实现风险显著降低**：直接借鉴 BaseAgent / WorkflowExtras / ScoreCard 已验证的设计模式，避免空中楼阁
- **7 个 agent 调研维度有真实案例参考**：港股研究agent 已对至少 20+ 真实港股 IPO 跑过，业务维度的覆盖度可信
- **港股研究agent 用户场景零影响**：原工具完整保留，原用户继续受益
- **职责清晰**：spec Phase 5 = LangGraph async 自动决策路径；港股研究agent = 人工 markdown 深度研究路径；互补不互替
- **NACS 三件套有 ADR 0005 实证基线 + 失败必现**：Phase 8 校准能立即发现是否漏接 / 错接

### Negative
- **架构借鉴需手动 cross-check**：港股研究agent 是同步 Python，Phase 5 是 async + LangGraph；BaseAgent 转译时如果漏掉 `await` / `Annotated[Dict, operator.update]` reducer 会埋 bug
  - **Mitigation**：`tests/unit/agents/` 单测每个 agent 的 `run()` 必须用 `pytest.mark.asyncio`；`tests/integration/test_agent_fanout.py` 验证 7 agent 并行不互覆盖
- **NACS 三件套必须自建带来工作量**：约占 Phase 5 总工时 40%
  - **Mitigation**：先实现 `policy_agent` (regime_score)，因为它是其他 agent 的上游输入；其次 cornerstone_signal + sentiment；最后 4 个普通 agent
- **港股研究agent 的多 provider 路由不能搬**：必须删除 Kimi / DeepSeek 分支，只保留 Anthropic
  - **Mitigation**：本仓库已有 `src/hk_ipo_agent/common/llm_client.py`（Anthropic only + cost tracking），直接复用，不引入新 provider 抽象

### Neutral
- 港股研究agent 的 `bull.py` / `bear.py` / `debate.py` / `risk.py` / `decision.py` **属于 Phase 6 范畴**（Critic + Synthesizer），本 ADR 不涉及，留到 Phase 6 启动时再写一篇 ADR 评估
- 港股研究agent 的 chromaDB → spec 已用 Qdrant（ADR 0003），无冲突
- 港股研究agent 的 akshare / iFinD / hkex 客户端 → spec 已在 Phase 2 完成 iFind 合并（ADR 0008 §4），可参考但无需移植

## Progress

- [x] **现在**：本 ADR 0009 写就，记录方案
- [x] **Phase 5 (2026-05-16)**：`agents/base.py` BaseAgent 抽象（async + AgentContext + AgentOutput + citation 强制） — frontmatter-aware loader + LLM cost 自动归集
- [x] **Phase 5 (2026-05-16)**：`agents/scoring.py` 7 个子类 ScoreCard + `schema_instruction()` + `extract_json_block()`
- [x] **Phase 5 (2026-05-16)**：`agents/workflow_extras.py` 强类型容器 + 4 个 NACS 字段（`regime_score` / `cluster_bonus_multiplier` / `theme_heat` / `ai_gilding_flag`）
- [x] **Phase 5 (2026-05-16)**：`agents/policy_agent.py` 含 `compute_regime_score()` 确定性计算（ADR 0005 §2）
- [x] **Phase 5 (2026-05-16)**：`agents/cornerstone_signal_agent.py` 含 `cluster_by_ultimate_holder()` 聚类（ADR 0005 §2）
- [x] **Phase 5 (2026-05-16)**：`agents/sentiment_agent.py` 读 KB themes/* + `detect_ai_gilding()` 检测（ADR 0005 §5）
- [x] **Phase 5 (2026-05-16)**：`agents/fundamental_agent.py` / `industry_agent.py` / `valuation_agent.py` / `liquidity_agent.py`（含 deterministic primitives）
- [x] **Phase 5 (2026-05-16)**：`prompts/agents/*.md` 7 个 prompts v1.0 frontmatter + body
- [x] **Phase 5 (2026-05-16)**：`tests/unit/agents/` 55 单测 + 3 DONE-condition smoke（7 agent fanout via asyncio.gather）
- [ ] **可选 Phase 9**：将港股研究agent 8 agent 输出作为黄金 e2e 测试的人工对照基线
