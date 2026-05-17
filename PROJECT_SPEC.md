# HK IPO Cornerstone Investment Agent — 项目规范 v1.2.1

> **本文件用途**：作为 Claude Code 的严格指令文档。所有代码生成、目录创建、文件编写均必须严格遵循本文件。本规范优先级最高，与其他文档冲突时以本文件为准。

> **v1.1 新增**：预测档案与生命周期追踪（Prediction Registry）+ 持续学习闭环（Learning Loop）。这是把 system 从"一次性预测器"升级为"自我校准的决策系统"的核心组件。新增 Phase 7.5 和 Phase 10。

> **v1.2 新增**：IPO 生命周期状态机 + 三层自动化调度器 + 公司代码自动映射 + 财报自动比对 + 终态处理（撤回/聆讯失败/默默失效）+ 警报路由。这把 system 从"被动等待用户喂数据"升级为"真正自主运行的追踪系统"。同时新增 §15 运行时部署要求。Phase 7.5 deliverables 大幅扩展。

> **v1.2.1 新增**：UI 工作台集成支撑层。新增完整 API 设计（REST + SSE + WebSocket）、RBAC 角色与认证、审计日志、What-If 估值 endpoint、招股书 PDF 服务、聊天会话管理。新增 §16 "UI 集成要求" 完整章节。配套姊妹文档 `PROJECT_SPEC_UI.md` (v1.3) 描述前端实施。Phase 7 扩展为"API + 报告 + UI 集成层"。

---

## 0. 项目使命

构建一个针对香港IPO（重点：18C特专科技、主板科技股、A+H股）**基石投资决策**的 AI Agent 系统。在仅有招股说明书、尚无最终基石名单和招股价格的时间点，输出：

1. **是否参与基石投资**（参与 / 部分参与 / 不参与）
2. **合理价格区间** [P_low, P_fair, P_high]，附 6/12 个月收益分布
3. **关键监控触发器**（定价、基石披露、超额认购数据更新时重判断）
4. **可解释的投决备忘录 + 风险评分卡**
5. **预测后的全生命周期追踪**（T+1/+10/+30/+90/+180/+360 自动校验、归因、学习）— 系统必须能从每个真实预测中学习，自动检测偏差、提议参数调整。**v1.1 新增**。
6. **完全自主的生命周期管理**（v1.2 新增）— 系统必须能自动识别 IPO 状态变化（招股 → 定价 → 上市 / 撤回 / 失败 / 默默失效）、自动从 iFind 拉取 checkpoint 数据、自动比对实际财报与招股书预测、自动触发 review。用户的角色只剩三个：触发初始分析 / 在 review_draft 上签字 / 收到警报时介入特殊情况。**任何"需要用户定期主动反馈数据"的设计都是失败设计**。

---

## 1. 核心技术栈（必须严格使用，不得替换）

| 类别 | 选型 | 版本要求 |
|---|---|---|
| 语言 | Python | >=3.11 |
| 包管理 | uv | latest |
| LLM 编排 | LangGraph | >=0.2 |
| 主 LLM | Anthropic Claude (Sonnet 4 默认, Opus 用于Synthesizer) | claude-sonnet-4 / claude-opus-4-7 |
| 数据校验 | Pydantic | v2 |
| 数据库 | PostgreSQL | 16+ |
| ORM | SQLAlchemy + Alembic | 2.0+ |
| 向量数据库 | Qdrant | latest（本地 docker） |
| 嵌入模型 | BGE-large-zh-v1.5（本地） + Voyage-3（生产备选） | - |
| PDF 解析 | LlamaParse（主）+ PyMuPDF（兜底，表格用） | - |
| Web 框架 | FastAPI | latest |
| 模板引擎 | Jinja2 | - |
| 数据处理 | Polars（主）+ Pandas（兼容） | - |
| HTTP 客户端 | httpx | - |
| 测试 | pytest + pytest-asyncio | - |
| 代码质量 | ruff + mypy | - |
| 配置 | pydantic-settings + YAML | - |
| 日志 | structlog（JSON输出） | - |
| 数据源 | 同花顺 iFind Python SDK（核心）+ 自建 HKEX 爬虫 | - |

**禁止**：自行引入 LangChain Agents（仅可用 LangChain 的 LLM/embedding wrapper）、CrewAI、AutoGen。统一通过 LangGraph 编排。

---

## 2. 完整目录结构

```
hk-ipo-cornerstone-agent/
├── README.md                              # 项目概述、quickstart
├── CLAUDE.md                              # Claude Code 工作规则（见 §11）
├── PROJECT_SPEC.md                        # 本文件
├── pyproject.toml                         # 项目元数据 + 依赖（uv 管理）
├── uv.lock
├── .env.example                           # 环境变量模板
├── .gitignore
├── .pre-commit-config.yaml                # 提交前自动跑 ruff/mypy
├── Makefile                               # 常用命令
├── docker-compose.yml                     # 本地 Postgres + Qdrant
│
├── docs/                                  # 项目文档
│   ├── ARCHITECTURE.md                    # 总体架构图 + 数据流
│   ├── DATA_SCHEMA.md                     # 数据库 schema + ER图
│   ├── AGENT_DESIGN.md                    # 每个 agent 的输入/输出/提示词策略
│   ├── VALUATION_MODELS.md                # 估值模型公式 + 适用场景
│   ├── PROSPECTUS_EXTRACTION.md           # 招股书抽取字段清单 + 抽取策略
│   ├── BACKTEST_PROTOCOL.md               # 回测方法论 + walk-forward规则
│   ├── PROMPT_ENGINEERING.md              # 提示词写作规范
│   ├── REGULATORY_TIMELINE.md             # 港股上市规则版本历史
│   ├── API_REFERENCE.md                   # FastAPI 接口文档
│   └── decisions/                         # ADR (Architecture Decision Records)
│       ├── 0001-use-langgraph.md
│       ├── 0002-claude-as-primary-llm.md
│       ├── 0003-qdrant-over-chroma.md
│       ├── 0004-llamaparse-for-pdf.md
│       └── README.md
│
├── config/                                # 配置（YAML，所有环境共享结构）
│   ├── settings.yaml                      # 全局配置
│   ├── llm_models.yaml                    # LLM 路由配置（哪个agent用哪个模型）
│   ├── agents.yaml                        # 各 agent 的运行配置
│   ├── valuation_weights.yaml             # 各模型权重（按公司类型）
│   ├── data_sources.yaml                  # 数据源端点 + 限流
│   └── regulations/                       # 规则版本化配置
│       ├── ipo_rules_pre_20250804.yaml    # 旧规则
│       ├── ipo_rules_post_20250804.yaml   # 新规则（35%回拨/机制A/B）
│       ├── ch18c_pre_20240901.yaml        # 18C 旧门槛
│       └── ch18c_post_20240901.yaml       # 18C 下调后门槛
│
├── src/hk_ipo_agent/                      # 主代码包（src layout）
│   ├── __init__.py
│   │
│   ├── common/                            # 共用基础
│   │   ├── __init__.py
│   │   ├── schemas.py                     # ★ 核心 Pydantic 模型（见 §6）
│   │   ├── enums.py                       # 枚举（ListingType, AgentRole 等）
│   │   ├── exceptions.py                  # 自定义异常
│   │   ├── logging.py                     # structlog 配置
│   │   ├── llm_client.py                  # 统一 LLM 调用（带重试/超时/cost跟踪）
│   │   ├── cache.py                       # Redis/磁盘缓存装饰器
│   │   ├── settings.py                    # pydantic-settings 加载 YAML+ENV
│   │   └── utils.py
│   │
│   ├── data/                              # 数据层
│   │   ├── __init__.py
│   │   ├── sources/                       # 外部数据连接器
│   │   │   ├── __init__.py
│   │   │   ├── ifind_client.py            # iFind SDK 封装（核心）
│   │   │   ├── hkex_scraper.py            # HKEXnews + 披露易爬虫
│   │   │   ├── disclosure_scraper.py      # 港交所披露易（董事/股东持股）
│   │   │   ├── web_search.py              # 通用搜索包装（带白名单）
│   │   │   └── news_client.py             # 新闻聚合
│   │   ├── repositories/                  # 数据访问层（CRUD）
│   │   │   ├── __init__.py
│   │   │   ├── base.py                    # BaseRepository 抽象类
│   │   │   ├── ipo_repo.py
│   │   │   ├── cornerstone_repo.py
│   │   │   ├── comparable_repo.py
│   │   │   ├── sponsor_repo.py
│   │   │   └── prospectus_repo.py
│   │   ├── models/                        # SQLAlchemy ORM
│   │   │   ├── __init__.py
│   │   │   ├── base.py                    # Declarative base + mixins
│   │   │   ├── ipo.py                     # IPOEvent, IPOPricing, IPOAllocation
│   │   │   ├── cornerstone.py             # CornerstoneInvestor, CornerstoneInvestment
│   │   │   ├── comparable.py              # ComparableCompany
│   │   │   ├── sponsor.py                 # Sponsor, SponsorRecord
│   │   │   ├── prospectus.py              # ProspectusDoc, ProspectusExtraction
│   │   │   └── company.py                 # Company, FinancialSnapshot
│   │   ├── migrations/                    # Alembic 迁移
│   │   │   ├── env.py
│   │   │   ├── alembic.ini
│   │   │   └── versions/
│   │   └── builders/                      # 知识库构建脚本（offline）
│   │       ├── __init__.py
│   │       ├── cornerstone_profile_builder.py  # 构建基石投资人画像
│   │       ├── sponsor_track_record.py         # 保荐人 track record
│   │       ├── comparable_pool_builder.py      # 可比公司池
│   │       └── historical_ipo_loader.py        # 加载历史 IPO 数据
│   │
│   ├── prospectus/                        # 招股书处理
│   │   ├── __init__.py
│   │   ├── parser.py                      # PDF → text/table/figure（LlamaParse）
│   │   ├── extractor.py                   # 结构化抽取（LLM + JSON Schema）
│   │   ├── schema.py                      # ★ ProspectusExtraction 模型（见 §6）
│   │   ├── chunker.py                     # 章节感知分块
│   │   ├── embeddings.py                  # 嵌入管理
│   │   ├── vector_store.py                # Qdrant 封装
│   │   ├── retriever.py                   # RAG 检索（带 hybrid search）
│   │   ├── qa.py                          # 招股书 Q&A 接口（供 agent 调用）
│   │   └── validators.py                  # 抽取结果一致性校验
│   │
│   ├── agents/                            # 多 Agent 专家层
│   │   ├── __init__.py
│   │   ├── base.py                        # ★ BaseAgent 抽象类（见 §7）
│   │   ├── fundamental_agent.py           # 基本面 agent
│   │   ├── industry_agent.py              # 行业与可比公司 agent
│   │   ├── valuation_agent.py             # 估值 agent（编排 valuation/）
│   │   ├── policy_agent.py                # 政策与规则 agent
│   │   ├── liquidity_agent.py             # 流动性与盘面 agent
│   │   ├── cornerstone_signal_agent.py    # 基石/承销商信号 agent
│   │   ├── sentiment_agent.py             # 情绪与微观结构 agent
│   │   └── tools/                         # Agent 工具
│   │       ├── __init__.py
│   │       ├── prospectus_tool.py         # 调用 prospectus.qa
│   │       ├── ifind_tool.py              # 查 iFind 数据
│   │       ├── kb_tool.py                 # 查内部知识库
│   │       └── web_tool.py                # 网络搜索
│   │
│   ├── valuation/                         # 估值模型
│   │   ├── __init__.py
│   │   ├── base.py                        # ValuationModel ABC
│   │   ├── comparable.py                  # PS/PE/EV-Sales 可比法
│   │   ├── pre_ipo_anchor.py              # 最近一轮估值 + 折价回归
│   │   ├── dcf.py                         # DCF + 实物期权
│   │   ├── ah_premium.py                  # A+H 折价回归模型
│   │   ├── industry/                      # 行业专属模型
│   │   │   ├── __init__.py
│   │   │   ├── ai_arr.py                  # AI/SaaS 按 ARR/客户LTV
│   │   │   ├── semiconductor.py
│   │   │   ├── robotics.py
│   │   │   ├── biotech_18a.py             # 借鉴 18A 经验
│   │   │   └── ev_battery.py
│   │   ├── monte_carlo.py                 # 蒙特卡洛引擎
│   │   ├── ensemble.py                    # 加权集成
│   │   └── milestones.py                  # 未商业化公司里程碑期权模型
│   │
│   ├── critic/                            # 辩论层
│   │   ├── __init__.py
│   │   ├── bull.py
│   │   ├── bear.py
│   │   ├── devils_advocate.py             # 挑战可比池/假设/数据时效
│   │   ├── cross_checker.py               # 与历史已上市公司比对
│   │   └── debate_graph.py                # LangGraph 子图：辩论流程
│   │
│   ├── synthesizer/                       # 决策合成
│   │   ├── __init__.py
│   │   ├── scoring.py                     # 评分卡逻辑
│   │   ├── decision_engine.py             # Go/no-go 决策树
│   │   ├── price_range.py                 # 价格区间计算
│   │   ├── trigger_rules.py               # 监控触发规则
│   │   └── synthesizer.py                 # 主合成 agent（用 Opus）
│   │
│   ├── orchestrator/                      # LangGraph 主编排
│   │   ├── __init__.py
│   │   ├── graph.py                       # ★ 主图定义（见 §8）
│   │   ├── states.py                      # State Pydantic 模型
│   │   ├── nodes.py                       # 节点函数
│   │   ├── edges.py                       # 条件边逻辑
│   │   ├── checkpoint.py                  # Postgres checkpointer
│   │   └── hitl.py                        # Human-in-the-loop 中断点
│   │
│   ├── backtest/                          # 回测与校准
│   │   ├── __init__.py
│   │   ├── runner.py                      # Walk-forward 引擎
│   │   ├── as_of_data.py                  # ★ 时点数据快照（防泄漏）
│   │   ├── metrics.py                     # 命中率/IC/Sharpe等
│   │   ├── calibration.py                 # 权重校准（贝叶斯优化）
│   │   ├── regime_detection.py            # 检测规则切换（2025-08-04）
│   │   └── reports.py
│   │
│   ├── prediction_registry/               # ★ 预测档案库（v1.1 新增，v1.2 扩展）
│   │   ├── __init__.py
│   │   ├── registry.py                    # 快照 CRUD（核心 API）
│   │   ├── snapshot.py                    # 不可变快照逻辑 + hash 验证
│   │   ├── outcome_tracker.py             # T+N 自动追踪股价/超额收益
│   │   ├── event_detector.py              # 关键事件检测（财报/盈警/异动/披露）
│   │   ├── attribution.py                 # 单样本归因分析引擎
│   │   ├── review_workflow.py             # 自动生成 review_draft + 人工 review 工作流
│   │   ├── benchmarks.py                  # 恒指/恒科指/行业基准管理
│   │   ├── code_mapper.py                 # ★ v1.2 公司名 → 股票代码自动映射
│   │   ├── earnings_comparator.py         # ★ v1.2 实际财报 vs 招股书预测自动比对
│   │   ├── alerts.py                      # ★ v1.2 警报路由（email/Slack/PagerDuty）
│   │   ├── ipo_lifecycle/                 # ★ v1.2 IPO 状态机
│   │   │   ├── __init__.py
│   │   │   ├── state_machine.py           # 状态机核心
│   │   │   ├── states.py                  # 状态定义 + 合法转换规则
│   │   │   ├── state_detectors.py         # 各状态自动检测逻辑
│   │   │   ├── terminal_handlers.py       # WITHDRAWN/HEARING_FAILED 终态处理
│   │   │   ├── stale_detector.py          # 超时检测（默默失效）
│   │   │   └── ah_special.py              # A+H 特殊处理（A股已在交易）
│   │   └── schedulers/                    # ★ v1.2 三层调度器
│   │       ├── __init__.py
│   │       ├── base.py                    # BaseScheduler 抽象（含幂等保证）
│   │       ├── high_frequency_scheduler.py  # 每 15-30 分钟，状态识别 + 事件捕获
│   │       ├── daily_scheduler.py         # 每日凌晨，checkpoint 追踪 + 补漏
│   │       ├── event_driven_scheduler.py  # webhook / 实时触发
│   │       └── airflow_dags/              # 生产环境推荐用 Airflow
│   │           ├── high_freq_state_check.py
│   │           ├── daily_outcome_tracking.py
│   │           ├── monthly_learning_cycle.py
│   │           └── alert_dispatcher.py
│   │
│   ├── learning_loop/                     # ★ 持续学习闭环（v1.1 新增）
│   │   ├── __init__.py
│   │   ├── drift_detector.py              # CUSUM/PSI 漂移检测
│   │   ├── attribution_aggregator.py      # 跨样本归因汇总
│   │   ├── counterfactual.py              # 反事实分析（如果听了 Bear 会怎样）
│   │   ├── adjustment_proposer.py         # 自动提议参数/提示词调整
│   │   ├── adjustment_applier.py          # 应用调整（必须人工批准）
│   │   ├── version_manager.py             # config / prompt 版本管理
│   │   └── reports.py                     # 月度学习报告
│   │
│   ├── reporting/                         # 报告生成
│   │   ├── __init__.py
│   │   ├── report_builder.py
│   │   ├── templates/                     # Jinja2 模板
│   │   │   ├── investment_memo.md.j2      # 投决备忘录
│   │   │   ├── scorecard.md.j2            # 评分卡
│   │   │   ├── monitoring_brief.md.j2     # 监控简报
│   │   │   └── backtest_report.md.j2
│   │   ├── charts.py                      # matplotlib/plotly 图表
│   │   └── exporters/
│   │       ├── pdf.py
│   │       └── docx.py
│   │
│   └── api/                               # FastAPI 服务（v1.2.1 扩展支持 UI）
│       ├── __init__.py
│       ├── main.py                        # app 入口（CORS、middleware、生命周期）
│       ├── openapi.py                     # OpenAPI 3.1 schema 配置（必须可被 UI 消费）
│       ├── routers/
│       │   ├── __init__.py
│       │   ├── auth.py                    # /api/v1/auth/* — SSO 回调、me、logout
│       │   ├── dashboard.py               # /api/v1/dashboard/summary
│       │   ├── ipos.py                    # /api/v1/ipos — 列表 + 详情 + 重分析
│       │   ├── snapshots.py               # /api/v1/snapshots/{id}
│       │   ├── prospectus.py              # /api/v1/prospectus — PDF 服务 + 引用溯源
│       │   ├── analysis.py                # /api/v1/analysis/* — 触发完整分析流程
│       │   ├── reviews.py                 # /api/v1/reviews — 工作流接口
│       │   ├── proposals.py               # /api/v1/proposals — 调整提议
│       │   ├── drift.py                   # /api/v1/drift — 漂移信号
│       │   ├── alerts.py                  # /api/v1/alerts
│       │   ├── chat.py                    # /api/v1/chat — 会话管理（REST 部分）
│       │   ├── whatif.py                  # /api/v1/whatif — What-If 重算
│       │   ├── backtest.py                # /api/v1/backtest
│       │   ├── system.py                  # /api/v1/system — 健康、调度器、成本
│       │   ├── audit.py                   # /api/v1/audit/logs
│       │   ├── settings.py                # /api/v1/settings — 配置查看 + 提示词管理
│       │   └── health.py                  # /health, /ready, /metrics
│       ├── streaming/                     # SSE 实时推送
│       │   ├── __init__.py
│       │   ├── sse_endpoint.py            # GET /api/stream/events
│       │   ├── event_bus.py               # 内部事件总线（Redis pubsub）
│       │   ├── event_types.py             # 事件类型定义 + 序列化
│       │   └── connection_manager.py      # 多客户端连接管理 + 心跳
│       ├── websocket/                     # WebSocket（仅 chat 用）
│       │   ├── __init__.py
│       │   ├── chat_endpoint.py           # WS /api/ws/chat/{session_id}
│       │   ├── chat_handler.py            # 流式 LLM 响应
│       │   └── manager.py
│       ├── auth/                          # 认证与权限
│       │   ├── __init__.py
│       │   ├── sso.py                     # SAML / OIDC 集成
│       │   ├── jwt.py                     # JWT issue / verify
│       │   ├── rbac.py                    # 角色 + 权限矩阵
│       │   ├── dependencies.py            # FastAPI Depends（require_role）
│       │   └── audit_middleware.py        # 自动审计写入
│       ├── middleware/
│       │   ├── __init__.py
│       │   ├── cors.py                    # CORS 配置（生产白名单）
│       │   ├── rate_limit.py              # 按 user + endpoint 限流
│       │   ├── request_id.py              # 请求 ID 注入
│       │   ├── error_handler.py           # RFC 7807 Problem Details
│       │   └── cost_guard.py              # LLM 调用类 endpoint 成本守护
│       ├── dependencies.py                # 通用 Depends
│       └── schemas.py                     # API 层 请求/响应模型（薄包装 common/schemas）
│
├── prompts/                               # 集中提示词库（与代码分离）
│   ├── system/
│   │   ├── orchestrator.md
│   │   └── synthesizer.md
│   ├── extraction/
│   │   ├── prospectus_section_router.md   # 决定调用哪个抽取子任务
│   │   ├── financials_extractor.md
│   │   ├── business_extractor.md
│   │   ├── risks_extractor.md
│   │   ├── shareholders_extractor.md
│   │   └── ch18c_qualifier.md
│   ├── agents/
│   │   ├── fundamental.md
│   │   ├── industry.md
│   │   ├── valuation.md
│   │   ├── policy.md
│   │   ├── liquidity.md
│   │   ├── cornerstone_signal.md
│   │   └── sentiment.md
│   └── debate/
│       ├── bull.md
│       ├── bear.md
│       ├── devils_advocate.md
│       └── cross_checker.md
│
├── data/                                  # 数据文件（除示例外gitignore）
│   ├── raw/
│   │   └── prospectuses/                  # 招股书 PDF 原文
│   ├── processed/
│   │   └── extractions/                   # 抽取后 JSON
│   ├── knowledge_base/                    # 自建知识库
│   │   ├── cornerstones.parquet
│   │   ├── sponsors.parquet
│   │   ├── historical_ipos.parquet
│   │   └── comparable_pool.parquet
│   ├── benchmarks/                        # 回测产出
│   │   └── backtest_runs/
│   └── samples/                           # 单元测试用小样本（入仓）
│       └── sample_prospectus_5pages.pdf
│
├── notebooks/                             # 探索性分析
│   ├── 01_data_exploration.ipynb
│   ├── 02_cornerstone_pattern_analysis.ipynb
│   ├── 03_ah_premium_regression.ipynb
│   ├── 04_18c_postlist_performance.ipynb
│   ├── 05_sponsor_track_record.ipynb
│   └── 06_backtest_calibration.ipynb
│
├── tests/                                 # 测试套件
│   ├── conftest.py                        # 共用 fixtures
│   ├── unit/                              # 单元测试（不打外部服务）
│   │   ├── common/
│   │   ├── prospectus/
│   │   ├── valuation/
│   │   ├── agents/
│   │   └── synthesizer/
│   ├── integration/                       # 集成测试（用 Docker 起依赖）
│   │   ├── test_full_pipeline.py
│   │   ├── test_db_repositories.py
│   │   └── test_rag_qa.py
│   ├── e2e/                               # 端到端（跑一个真实历史案例）
│   │   └── test_quantumpharm_case.py      # 用晶泰控股作回归测试
│   ├── fixtures/
│   │   ├── sample_prospectus.pdf
│   │   ├── sample_extraction.json
│   │   └── sample_ifind_response.json
│   └── golden/                            # 黄金回归数据集
│       └── 18c_listed_companies.json
│
├── scripts/                               # CLI 工具脚本
│   ├── ingest_prospectus.py               # 摄入新招股书
│   ├── run_analysis.py                    # 运行完整分析
│   ├── update_knowledge_base.py           # 增量更新知识库
│   ├── run_backtest.py
│   ├── calibrate_weights.py
│   └── export_memo.py
│
└── workflows/                             # 工作流定义（YAML，便于配置化）
    ├── full_analysis.yaml                 # 完整分析流程
    ├── monitoring.yaml                    # 监控模式（数据变更时触发）
    └── backtest.yaml
```

---

## 3. 关键文件职责详解

### 3.1 顶层文件

**`README.md`** — 用户视角说明：项目目的、quickstart（如何 setup、跑一个示例）、技术架构图链接。约 200-300 行。

**`CLAUDE.md`** — 给 Claude Code 的工作准则，详见 §11。这是 Claude Code 启动时第一份要读的文件。

**`PROJECT_SPEC.md`** — 本文件，权威规范。

**`pyproject.toml`** — 必须使用 uv 管理；定义 src layout；ruff/mypy/pytest 配置全在此文件。

**`Makefile`** — 必须包含：`make install`, `make test`, `make lint`, `make typecheck`, `make db-up`, `make migrate`, `make analyze IPO=xxx`, `make backtest`。

**`docker-compose.yml`** — 启动 postgres:16、qdrant、redis。所有服务端口必须在 .env 中可覆盖。

### 3.2 `config/` 详解

所有配置文件用 YAML，禁止把配置写死在代码里。

**`settings.yaml`** — 全局：环境（dev/prod）、数据目录、日志级别。
**`llm_models.yaml`** — 哪个 agent 用哪个模型，例如：
```yaml
agents:
  fundamental: { model: claude-sonnet-4, max_tokens: 4096, temperature: 0.3 }
  synthesizer: { model: claude-opus-4-7, max_tokens: 8192, temperature: 0.2 }
extraction:
  prospectus: { model: claude-sonnet-4, max_tokens: 4096 }
```
**`valuation_weights.yaml`** — 按 ListingType (CH18C_COMMERCIALIZED / CH18C_PRE_COMMERCIAL / MAINBOARD_TECH / AH_DUAL) 给出不同的模型权重起点。
**`regulations/*.yaml`** — 规则版本化。每份配置含生效日期范围、对应分配机制、回拨阈值、公众持股量门槛。Policy Agent 必须从此处读取。

### 3.3 `src/hk_ipo_agent/common/` 详解

**`schemas.py`** — 见 §6，全项目最关键的 Pydantic 模型。

**`enums.py`** — 必须包含：
```python
class ListingType(str, Enum):
    CH18C_COMMERCIALIZED = "18C-COMM"
    CH18C_PRE_COMMERCIAL = "18C-PRE"
    CH18A_BIOTECH = "18A"
    MAINBOARD_TECH = "MB-TECH"
    AH_DUAL = "AH"
    MAINBOARD_OTHER = "MB-OTHER"

class AgentRole(str, Enum):
    FUNDAMENTAL = "fundamental"
    INDUSTRY = "industry"
    VALUATION = "valuation"
    POLICY = "policy"
    LIQUIDITY = "liquidity"
    CORNERSTONE_SIGNAL = "cornerstone_signal"
    SENTIMENT = "sentiment"

class DecisionType(str, Enum):
    PARTICIPATE = "participate"
    PARTIAL = "partial"
    SKIP = "skip"
    WAIT_FOR_SIGNAL = "wait"

class RegulatoryRegime(str, Enum):
    PRE_20250804 = "pre_new_pricing"
    POST_20250804 = "post_new_pricing"
```

**`llm_client.py`** — 必须实现：
- 统一 `acomplete(messages, model, ...)` 异步接口
- 自动重试（指数退避，最多 3 次）
- 成本跟踪（按 model 和 agent 标签累计 token 用量，写入 cost log）
- 超时保护（默认 120s）
- 同时支持 system prompt 缓存（Anthropic prompt caching）

**`settings.py`** — pydantic-settings；分层加载：默认值 < settings.yaml < .env < env vars。

### 3.4 `src/hk_ipo_agent/data/` 详解

**`sources/ifind_client.py`** — iFind SDK 封装。必须实现：
- `get_financials(ticker, start, end, fields)` → DataFrame
- `get_ipo_history(market="HK", start, end)` → 历史 IPO 列表
- `get_comparable_companies(industry_code, market)` → List
- `get_ah_premium_history(ticker_pair)` → 时序
- 全部带 retry + rate limit（iFind 有 QPS 限制，必须在 data_sources.yaml 配置）
- **所有方法必须支持 as_of_date 参数**（回测防泄漏）

**`sources/hkex_scraper.py`** — 爬 HKEXnews 公告 + 披露易：
- `download_prospectus(stock_code)` → PDF 路径
- `get_listing_documents(stock_code)` → 上市文件清单
- `get_disclosure_filings(stock_code)` → 持股变动
- 遵守 robots.txt + 限速（每秒不超过 2 次请求）

**`models/ipo.py`** — 核心表 schema：
- `IPOEvent`：基本信息（公司、行业、ListingType、聆讯日、定价日、上市日）
- `IPOPricing`：定价区间、最终价、超额认购倍数（含国际/公开）、孖展倍数
- `IPOAllocation`：基石/锚定/公开/国际配售实际分配
- `IPOPostMarket`：T+1, T+5, T+22, T+126(6个月), T+252(12个月) 收益率

**`builders/cornerstone_profile_builder.py`** — 核心知识库。要求：
- 输入：历史 IPO 列表 + 各 IPO 招股书 cornerstone section
- 处理：基石按身份分类（主权基金、地方国资、产业战投、外资长线、家办、对冲、险资、银行理财、上下游战投）
- 输出：`cornerstones.parquet`，每条记录含：基石名、身份分类、历次参与 IPO、各次锁定期解禁前后股价、是否减持、计算"基石信号强度"分数

### 3.5 `src/hk_ipo_agent/prospectus/` 详解

**`parser.py`** — PDF 解析。策略：
- 主路径：LlamaParse（对表格识别好）
- 兜底：PyMuPDF（提文本）+ Camelot/Tabula（提表格）
- 输出：结构化 `ParsedDocument(sections, tables, figures)`，保留**每个块的页码**

**`extractor.py`** — 结构化抽取。流程：
1. 用 `prompts/extraction/prospectus_section_router.md` 让 LLM 识别章节
2. 对每个章节调用对应抽取提示词
3. 用 `ProspectusExtraction` Pydantic 模型严格校验
4. 失败时降级：先用 Sonnet，失败用 Opus，再失败标记为 needs_human_review
5. **所有抽取结果必须可溯源到原文页码**

**`vector_store.py`** — Qdrant 封装：
- 集合按公司 + 招股书版本隔离
- 元数据必须含：page, section, subsection, char_offset
- 支持 hybrid search（BM25 + 向量）

**`qa.py`** — Agent 调用接口：
- `ask(question, prospectus_id, top_k=5)` → `Answer(text, citations[(page, chunk_id)])`
- 必须返回 citations，禁止无引用回答

### 3.6 `src/hk_ipo_agent/agents/` 详解

每个 agent 必须继承 `BaseAgent`，实现 `async def run(state) → AgentOutput`。

**所有 agent 的输出必须是结构化 `AgentOutput`**（不是自由文本），包含：
- `scores`: Dict[str, float] — 各维度评分（0-100）
- `key_findings`: List[Finding] — 每条 finding 含 evidence + page citation
- `uncertainty_flags`: List[str] — 数据不足或矛盾点
- `data_used`: List[DataSource] — 用了哪些数据源（用于审计）

详细 agent 设计见 §7。

### 3.7 `src/hk_ipo_agent/valuation/` 详解

**`base.py`**：
```python
class ValuationModel(ABC):
    @abstractmethod
    async def value(self, extraction: ProspectusExtraction, 
                    market_data: MarketData) -> ValuationOutput: ...
    
    @property
    @abstractmethod
    def applicable_types(self) -> List[ListingType]: ...
```

**`comparable.py`** — 可比公司法。要求：
- 自动从 iFind 拉同业 PS/PE/EV-Sales 分位数（25/50/75）
- 对每个倍数生成估值，输出分布
- 必须支持跨市场（A/H/US ADR）的可比，带流动性折价调整

**`ah_premium.py`** — A+H 折价回归。必须：
- 用历史 AH 双重上市新股做训练样本
- 因子至少含：Beta差、流通市值差、流动性差、股息率、行业、AH溢价指数当时点位
- 输出：点估计 + 90% 置信区间

**`monte_carlo.py`** — 关键假设跑 10000 次，输出估值分布而非点值。

**`ensemble.py`** — 按 `valuation_weights.yaml` 配置 + 当前 ListingType 加权。每个估值模型贡献一份分布，最终输出加权后的分布。

**`milestones.py`** — 未商业化公司专用。把技术商业化路径拆为里程碑（Phase I/II/III 等），按概率加权。

### 3.8 `src/hk_ipo_agent/orchestrator/` 详解

**`graph.py`** — 主 LangGraph 定义。状态机：

```
START
  → ingest (招股书解析 + 抽取)
  → parallel_agents (7 个专家 agent 并行)
  → valuation (调 valuation 子图)
  → debate (Bull/Bear/Devil 子图)
  → cross_check (历史样本核对)
  → synthesize (Opus 模型)
  → hitl_review (人工审核检查点)
  → report
  → END
```

**`states.py`** — `AnalysisState`（TypedDict 或 Pydantic）。必须包含：
- `ipo_id`, `prospectus_id`, `as_of_date`
- `extraction`: ProspectusExtraction
- `agent_outputs`: Dict[AgentRole, AgentOutput]
- `valuation_result`: ValuationEnsembleOutput
- `debate_result`: DebateOutput
- `decision`: FinalDecision

**`checkpoint.py`** — 用 PostgresSaver。每个节点完成后保存，崩溃可恢复。

**`hitl.py`** — 在 `synthesize` 后插入中断点；只有人工确认才能继续。生产环境必须开启。

### 3.9 `src/hk_ipo_agent/backtest/` 详解

**`as_of_data.py`** — ★★★ 防数据泄漏最关键文件：
- 所有数据访问必须经过 `AsOfDataProvider(as_of_date)`
- 任何时点 T 的回测，只能看到 T 时点（不含）之前的数据
- 包括：财务、市场行情、新闻、政策（规则版本切换）

**`runner.py`** — Walk-forward：
- 对每个历史 IPO，以 (招股书披露日 - 1) 为 as_of_date 跑完整 pipeline
- 与实际定价、首日表现、6/12 个月表现对比
- 输出每个案例的：预测决策 vs 实际表现，预测价区间 vs 实际价

**`calibration.py`** — 用回测结果反向调 `valuation_weights.yaml` 和 `agents.yaml`（贝叶斯优化）。

**`regime_detection.py`** — 自动检测规则切换点（2024-09-01 18C下调、2025-08-04 定价新规），分段评估。

### 3.10 `prompts/` 详解

**所有提示词必须是 .md 文件，与代码分离**。每个文件结构：

```markdown
---
role: fundamental_agent
version: 1.2
last_updated: 2026-01-15
input_schema: AgentInput
output_schema: AgentOutput
---

# Role
你是一位资深港股新股研究分析师...

# Task
...

# Output Format (JSON)
严格按以下 JSON Schema 输出：
{schema}

# Examples
## Example 1
...
```

所有提示词在 LLM 调用前都必须经过 Jinja2 渲染（注入 schema、上下文）。

### 3.11 `src/hk_ipo_agent/prediction_registry/` 详解（v1.1 新增）

这是 system 从"一次性预测器"升级为"自校准决策系统"的核心。**任何完整分析的输出必须先经过 registry 创建快照才能输出决策**。

**`registry.py`** — 核心 CRUD API：
- `async create_snapshot(state: AnalysisState) -> SnapshotId` — 把完整分析状态写入不可变快照
- `async get_snapshot(id) -> PredictionSnapshot` — 读取（带 hash 验证）
- `async list_active_predictions(as_of_date) -> List[Snapshot]` — 仍在追踪窗口（≤360天）内的活跃预测
- `async attach_review(snapshot_id, review)` — 追加 review 笔记，**只有 prediction_reviews 表允许此操作**
- 严禁实现任何 update_snapshot 接口

**`snapshot.py`** — 不可变性实现：
- 写入前计算完整快照的 SHA256 hash 存入 `input_data_hash` 字段
- 每次读取自动验证 hash，不一致抛 `SnapshotIntegrityError`
- DB 层用 trigger 拦截 UPDATE 操作（见 §5 SQL）
- 配合 audit log 记录所有访问

**`outcome_tracker.py`** — T+N checkpoint 数据拉取：
- `async track(snapshot_id, checkpoint_day)` 主入口
- 从 iFind 拉股价数据（必须用 as_of_date 模式防泄漏，虽然这里是已发生的数据但格式一致）
- 计算超额收益（vs 恒指、恒科指、行业可比池中位数）
- 调用 event_detector 扫描期内事件
- 自动判定 `price_in_predicted_range` 和 `decision_correct`
- 写入 `prediction_outcomes` 表
- 幂等：同 (snapshot_id, checkpoint_day) 重复调用不重复写

**`event_detector.py`** — 关键事件检测：
- 数据源：HKEX 公告流（announcements API）+ iFind 价格异动 + 新闻聚合
- 事件类型：earnings / profit_warning / major_contract / regulatory / management_change / cornerstone_disclosure / placement / share_buyback / other
- 价格异动判定：单日收益 > ±5% 或 5日累计 > ±10%
- LLM（Sonnet）对事件做 severity 分类（critical/major/minor）
- 写入 `post_ipo_events` 表

**`attribution.py`** — 单样本归因引擎（核心智能模块）：
- 输入：snapshot + outcome
- 输出 `Attribution` 对象
- 三层归因：
  1. **Agent 层**：每个 agent 的 finding 是否被事实验证；critical_misses 和 critical_correct_calls
  2. **估值模型层**：实际价 vs 各模型预测分布的偏差；是否落在 p10-p90 区间
  3. **辩论质量层**：Bear 提出的风险事后看应验率；Bull 论点的应验率；被 Synthesizer 忽略但应验的关键风险
- LLM（Opus）综合诊断输出文字分析
- 自动生成 `ProposedAdjustment` 候选列表（写入 review_draft，不直接 apply）

**`review_workflow.py`** — 人工 review：
- 在 30/90/180/360 天 checkpoint 自动调用 attribution → 生成 review_draft
- review_draft 包含：what_we_got_right / what_we_got_wrong / 归因结果 / 提议调整
- 等待 reviewer 填写最终意见（CLI 或 API 接口）
- 提交后写入 `prediction_reviews` 表
- 高优先级触发：决策错误 + 实际收益 < -20%，立即生成 critical_review

**`checkpoint_scheduler.py`** — 已被 v1.2 的 `schedulers/` 子目录取代，见 §3.11.2。如果在 v1.1 已实现，需迁移到新结构。

**`benchmarks.py`** — 基准管理：
- 恒指、恒科指、对应行业可比池中位数三个基准
- 每个基准的历史价格自动维护
- 行业基准动态构建：根据 ListingType + industry_code 查 comparable_pool

**`code_mapper.py`** — 公司名 → 股票代码自动映射（v1.2 新增）：
- 招股书时点公司还**没有股票代码**，只有名称。上市后才生成 HK code
- `async resolve_code(ipo_id) -> Optional[CodeMapping]`：自动尝试映射
- 映射策略（按优先级）：
  1. HKEX 上市公告中直接提取（最权威）
  2. iFind `search_by_name(company_name)` 模糊匹配
  3. 通过保荐人 + 上市日期窗口反查
- 必须输出置信度（high/medium/low）；low 必须触发人工 review
- 对 A+H 股：同时映射 H 股代码和 A 股代码
- 失败时不要假装确定，写 `code_mappings.confidence='low'` 并发警报

**`earnings_comparator.py`** — 实际财报 vs 招股书预测自动比对（v1.2 新增）：
- 触发：event_detector 检测到 earnings 发布事件
- `async compare(snapshot_id, filing) -> EarningsComparison`
- 比对维度：
  1. 营收（同口径，注意非IFRS adjustments 的差异）
  2. 净利润 / adjusted 净利润
  3. 毛利率
  4. 关键运营指标（KPI）— 按行业不同（如 AI 看 ARR、半导体看出货量）
  5. 业务分部表现
- **重要**：招股书预测和年报口径可能不一致，必须维护 `mapping_rules.yaml`（按公司类型定义口径转换规则）
- 输出 `EarningsComparison`（beat / in_line / miss / significant_miss）
- 前 3 次比对必须人工 review 口径正确性后才能完全信任

**`alerts.py`** — 警报路由（v1.2 新增）：
- 三个 level：info（日志即可）/ warning（24h 内 review）/ critical（立即通知）
- 输出渠道：Email / Slack / PagerDuty / 短信（按 level 路由）
- 配置在 `config/alerts.yaml`
- 自动去重：同 `(category, ipo_id, level)` 在 24h 内只发一次
- 警报必须含可操作信息（不能只说"出错了"，要说"应该做什么"）

### 3.11.1 `ipo_lifecycle/` — IPO 状态机（v1.2 核心新增）

这是 system 自主运行的关键。每个 snapshot 创建后，对应 IPO 进入状态机，由调度器每天扫描并推动状态变化。

**状态定义**（见 `states.py`）：
- `PRE_LISTING` — 招股书已披露，尚未发布招股价（snapshot 默认进入）
- `PRICING` — 招股期，已发布招股价区间
- `LISTED` — 已上市，开始 T+N 追踪
- `WITHDRAWN` — 主动撤回招股
- `HEARING_FAILED` — 聆讯失败
- `PRICING_PULLED` — 进入 PRICING 但未在合理窗口内上市（已发价但取消发行）
- `TERMINATED` — 终态（含完整 360 天 lifecycle 完成后归档）

**合法转换规则**：写死在 `states.py`，违反必抛异常。例如：
- `PRE_LISTING → PRICING / WITHDRAWN / HEARING_FAILED`
- `PRICING → LISTED / WITHDRAWN / PRICING_PULLED`
- `LISTED → TERMINATED`（仅经过 360 天）
- 任何状态 → 任何状态的"回退"都禁止

**`state_machine.py`** — 状态机核心：
- `async get_state(ipo_id) -> IPOLifecycleState`
- `async transition_to(ipo_id, new_state, triggered_by, evidence)` — 严格走合法路径
- 每次转换写 audit log 到 `ipo_state_transitions` 表

**`state_detectors.py`** — 各状态自动检测：

| 状态 | 检测方法 | 数据源 |
|---|---|---|
| **PRICING** | 招股书 PHIP/AP 后续版本发布、iFind 出现 price_range 字段 | HKEXnews + iFind |
| **LISTED** | **三重验证**：1. HKEX 上市公告 2. iFind 出现首日行情 3. 股票代码激活 | HKEXnews + iFind |
| **WITHDRAWN** | 主动发布撤回公告 / 公司从 HKEX active filings 消失 | HKEXnews 状态字段 |
| **HEARING_FAILED** | 聆讯结果公告"未获通过" / 公司公开声明 | HKEXnews + 新闻 |

**`stale_detector.py`** — 超时检测（最容易漏的）：
- `PRE_LISTING > 180 天` → 招股书 6 个月有效期失效 → 警报，可能"默默失效"
- `PRICING > 21 天` → 招股期通常 1-2 周，超时强烈暗示问题
- 触发警报但不自动转 WITHDRAWN（必须人工确认，因为公司可能很快重新递表）

**`terminal_handlers.py`** — 终态处理：
- 公司未上市也必须触发归因（防 survivorship bias）
- 撤回情况下，如果系统当时建议"参与" → 这是 false positive（资金被锁但根本没机会用）
- 自动生成 `terminal_review_draft`：聚焦"漏看了什么信号"
- 写入 `prediction_outcomes`（用 `checkpoint_day = -1` 标记终态 outcome）

**`ah_special.py`** — A+H 特殊处理：
- A 股部分在 H 股发行前一直在交易，本身就是重要参考
- 上市日定义：H 股首日（不是 A 股）
- checkpoint 计时从 H 股上市日开始
- 但归因时必须考虑：A 股期间的表现可能已"消化"了部分信息

### 3.11.2 `schedulers/` — 三层调度器（v1.2 核心新增）

**调度器是系统能自主运行的真正引擎**。没有它，所有"自动追踪"都是空谈。

**`base.py`** — BaseScheduler 抽象：
- 每次运行写 `scheduler_runs` 表（含 run_id、started_at、processed counts、errors）
- 幂等保证：每次运行带 lock，禁止重叠
- 失败时自动重试 + 升级警报

**`high_frequency_scheduler.py`** — 每 15-30 分钟（轻量任务）：
- 扫描 active snapshot 的 IPO 状态变化（PRE_LISTING → PRICING → LISTED）
- 调用 state_detectors，识别 LISTED 时调用 code_mapper 解析代码
- 调用 event_detector.scan_recent_events(lookback="2h")
- **不做计算密集任务**（归因、回测等不在这层）

**`daily_scheduler.py`** — 每天凌晨 2-3 点（重活）：
- 对所有 LISTED 状态的 snapshot，计算 days_since_listing
- 触达任何 checkpoint 日（1/5/10/22/30/60/90/126/180/252/360）→ 调 outcome_tracker
- 补漏机制：扫描历史所有 checkpoint，未记录的用 historical close price 补跑
- 大 checkpoint（30/90/180/360）触发 review_workflow.generate_draft
- 检查超时（stale_detector）
- 完成 360 天 → 转 TERMINATED → 归档

**`event_driven_scheduler.py`** — 实时触发：
- 监听 HKEX 公告流（RSS / webhook / 轮询）
- 监听 iFind 价格异动（|日收益| > 5% 或 |5日累计| > 10%）
- 监听披露易持股变动
- 各类事件触发对应处理器（earnings_comparator / cornerstone_tracker / alerts）

**`airflow_dags/`** — 生产推荐：
- 开发可用 APScheduler，生产强烈建议 Airflow（更好的可观测性、重试、告警）
- 关键 DAG：`daily_outcome_tracking` 必须有 SLA 监控（失败 6h 内通知）

### 3.12 `src/hk_ipo_agent/learning_loop/` 详解（v1.1 新增）

跨多个 outcome 累积后做系统性诊断，提议参数调整。**严格人工批准制**。

**`drift_detector.py`** — 漂移检测：
- 维护滑动窗口（最近 20/50 个完成 6 个月 checkpoint 的预测）
- 关键指标：
  - 决策准确率（CUSUM 检测均值突变）
  - 估值偏差（实际/预测中位价的 log-ratio 分布）
  - Agent score 校准（高分项目实际表现）
  - Bear 漏报率
- 分维度切片：按 ListingType / 行业 / RegulatoryRegime
- 输出 `DriftSignal` 列表

**`attribution_aggregator.py`** — 跨样本归因汇总：
- 聚合所有 prediction_reviews 中的 attribution
- 找出系统性偏差模式：某 agent 在某类公司上系统性高估、某估值模型在某行业偏差大
- 生成 aggregated_findings，作为 adjustment_proposer 的输入

**`counterfactual.py`** — 反事实分析：
- 对每个 outcome 重跑：如果当时听了 Bear Agent 决策，准确率如何？
- 如果只用某单一估值模型，价格区间命中率如何？
- 用于辨别 Synthesizer 的权衡逻辑是否合理
- 输出反事实报告（不直接修改系统）

**`adjustment_proposer.py`** — 自动提议：
- 输入：drift_signals + aggregated_findings + counterfactuals
- 输出：`ProposedAdjustment` 列表，每条含：
  - target_path（哪个 config 或 prompt 文件）
  - adjustment_type（weight_change / prompt_edit / factor_add / logic_change）
  - current_value / proposed_value
  - rationale + evidence_snapshot_ids
  - expected_impact
- 写入 prediction_reviews 表，状态 `proposed`

**`adjustment_applier.py`** — 应用调整：
- **强制约束：必须 reviewer 在 prediction_reviews 中标记 status=accepted 才能 apply**
- apply 流程：
  1. 验证 reviewer 字段非空
  2. 把当前 config / prompt bump version（写 git tag 或 version_manager）
  3. 修改目标文件
  4. 自动触发一次小回测验证（用最近 5 个样本）
  5. 写入 `prediction_reviews.adjustment_status = implemented`
- 任何步骤失败 → rollback + 标 `rejected`，记录原因

**`version_manager.py`** — 版本管理：
- 维护 config / prompt 的版本历史
- 每个版本对应一个 git commit 或独立的 versioned 文件
- 任何 snapshot 必须能定位到当时的版本（保证归因可复现）

**`reports.py`** — 月度报告：
- 系统校准状态：近 30/60/90 天准确率
- Drift 检测摘要
- 待批准调整列表
- 已应用调整的事后效果

---

## 4. 构建阶段（Build Phases）

**Claude Code 必须严格按以下阶段顺序推进**，每阶段完成后停下来等待人工确认才能进入下一阶段。

### Phase 0: 项目骨架（0.5 天）
**Deliverables：**
- [x] 完整目录树（空文件 + 占位 docstring）
- [x] `pyproject.toml` + uv 初始化
- [x] `docker-compose.yml` + `.env.example`
- [x] `Makefile` 基础命令
- [x] `.gitignore`, `.pre-commit-config.yaml`
- [x] `README.md` 骨架
- [x] `CLAUDE.md`

**DONE 条件：** `make install && make lint` 通过；docker 起得来。

### Phase 1: 核心基础设施（1-2 天）
**Deliverables：**
- [x] `common/schemas.py` 完整 Pydantic 模型（见 §6）
- [x] `common/enums.py`、`common/exceptions.py`、`common/logging.py`
- [x] `common/llm_client.py`（带 retry、成本跟踪、prompt caching）
- [x] `common/settings.py`
- [x] `data/models/*` SQLAlchemy ORM
- [x] Alembic 初始 migration
- [x] `tests/unit/common/` 全覆盖

**DONE 条件：** `make migrate` 成功；ORM 单元测试通过；可成功调用一次 Claude API 并落 cost log。

### Phase 2: 数据层（2-3 天）
**Deliverables：**
- [x] `data/sources/ifind_client.py`（含 as_of_date 支持）
- [x] `data/sources/hkex_scraper.py`
- [x] `data/repositories/*` 全部
- [x] `data/builders/historical_ipo_loader.py`（先把 2022-至今所有港股 IPO 加载进库）
- [x] `data/builders/cornerstone_profile_builder.py`（构建基石画像）
- [x] `data/builders/sponsor_track_record.py`
- [x] `data/builders/comparable_pool_builder.py`

**DONE 条件：** 数据库中有完整的 2022-至今港股 IPO 历史数据 + 基石画像表 + 保荐人 track record + 可比公司池。`tests/integration/test_db_repositories.py` 通过。

### Phase 3: 招股书处理（2 天）
**Deliverables：**
- [x] `prospectus/parser.py`（LlamaParse + PyMuPDF 双路径）
- [x] `prospectus/schema.py` — `ProspectusExtraction` 全字段
- [x] `prospectus/extractor.py`（章节路由 + 子任务抽取）
- [x] `prospectus/chunker.py`
- [x] `prompts/extraction/*` 全部提示词
- [x] `prospectus/embeddings.py`、`vector_store.py`、`retriever.py`、`qa.py`
- [x] 用 `tests/fixtures/sample_prospectus.pdf` 做端到端测试

**DONE 条件：** 给定一份真实招股书 PDF，能在 5 分钟内输出完整 `ProspectusExtraction` JSON + 建立 Qdrant 索引 + `qa.ask()` 能回答任意问题并带页码引用。

### Phase 4: 估值模型层（3 天）
**Deliverables：**
- [x] `valuation/base.py` + `valuation/comparable.py` + `valuation/dcf.py` + `valuation/pre_ipo_anchor.py`
- [x] `valuation/ah_premium.py`（含历史回归训练脚本）
- [x] `valuation/milestones.py`（未商业化公司）
- [x] `valuation/industry/*` 至少先实现 AI/SaaS 和半导体
- [x] `valuation/monte_carlo.py`
- [x] `valuation/ensemble.py`
- [x] `tests/unit/valuation/*` 每个模型独立测试

**DONE 条件：** 给定一份 ProspectusExtraction，能输出包含至少 4 个独立模型估值 + 蒙特卡洛分布 + 加权集成的完整 `ValuationEnsembleOutput`。

### Phase 5: Agent 层（3-4 天）
**Deliverables：**
- [x] `agents/base.py` 抽象类
- [x] 7 个专家 agent 全部实现
- [x] `agents/tools/*` 工具实现
- [x] `prompts/agents/*` 全部提示词
- [x] 每个 agent 的单元测试（用 mock LLM）

**DONE 条件：** 每个 agent 都能独立运行，输出符合 `AgentOutput` schema，所有 finding 都带 citation。

### Phase 6: 编排 + Critic + Synthesizer（2-3 天）
**Deliverables：**
- [x] `orchestrator/states.py` + `graph.py` + `nodes.py` + `edges.py`
- [x] `orchestrator/checkpoint.py`
- [x] `orchestrator/hitl.py`
- [x] `critic/bull.py` / `bear.py` / `devils_advocate.py` / `cross_checker.py`
- [x] `critic/debate_graph.py`
- [x] `synthesizer/*` 全部

**DONE 条件：** `scripts/run_analysis.py --ipo SAMPLE` 端到端跑通，输出完整投决备忘录。崩溃可从 checkpoint 恢复。

### Phase 7: 报告 + API + UI 集成层（v1.2.1 扩展至 3-4 天）
**Deliverables：**

**v1.0 报告与基础 API 部分：**
- [x] `reporting/templates/*` Jinja2 模板
- [x] `reporting/charts.py`
- [x] `reporting/exporters/pdf.py` + `docx.py`
- [x] 基础 FastAPI 服务（health + dashboard + ipos + snapshots + analysis）
- [x] `scripts/run_analysis.py`、`scripts/export_memo.py`

**v1.2.1 UI 集成扩展（必须完成）：**
- [x] OpenAPI 3.1 schema 完整暴露（所有 endpoint），UI 端可用 `openapi-typescript` 自动生成类型
- [x] 完整 API routers（见 §16.2 全清单）
- [x] SSE 实时推送（`streaming/sse_endpoint.py` + `event_bus.py` + `event_types.py`）
- [x] WebSocket chat（`websocket/chat_endpoint.py` 含流式响应）
- [x] 认证层：SSO（SAML/OIDC）+ JWT + Auth.js 兼容
- [x] RBAC：6 角色 + 完整权限矩阵 + `require_role` 装饰器
- [x] CORS 配置（生产白名单 + 招股书 PDF 跨域支持）
- [x] 速率限制中间件（按 user + endpoint）
- [x] 审计中间件（所有 write 操作自动写 audit_logs）
- [x] 错误格式标准化（RFC 7807 Problem Details）
- [x] DB migration: `audit_logs`、`chat_sessions`、`chat_messages`、`user_accounts`、`user_roles` 5 张表
- [x] What-If endpoint（`/api/v1/whatif/valuation` + `/api/v1/whatif/comparable`）
- [x] 招股书 PDF 服务 endpoint（含 Range request 支持、PDF.js 兼容 CORS）
- [x] 文档：`docs/API_REFERENCE.md` + `docs/RBAC.md` + `docs/SSE_PROTOCOL.md` + `docs/WS_PROTOCOL.md`

**DONE 条件：** 
- CLI、API、UI 三种触发方式都能跑完整分析；输出 PDF/DOCX 投决备忘录
- UI 项目 `pnpm run generate-api-types` 能从后端 OpenAPI 自动生成完整类型
- SSE 事件能在测试客户端订阅并接收所有定义的事件类型
- WebSocket 聊天能流式响应并保持会话状态
- RBAC 对抗测试：低权限角色访问高权限 endpoint 必须 403
- 招股书 PDF 在 PDF.js 中能正确加载（CORS + Range request）

**DONE 条件：** CLI 和 API 都能触发完整分析；输出 PDF/DOCX 投决备忘录。

### Phase 7.5: 预测档案与生命周期追踪（v1.2 扩展至 6-7 天）★ 与回测同等重要
**Deliverables（v1.1 基础 + v1.2 自动化扩展）：**

**v1.1 部分：**
- [x] `prediction_registry/registry.py` + `snapshot.py` 实现不可变快照
- [x] DB migration: prediction_snapshots, prediction_outcomes, post_ipo_events, prediction_reviews 4 张表
- [x] DB trigger 强制 prediction_snapshots 表 immutable（拒绝 UPDATE）
- [x] `outcome_tracker.py` 完整实现，能拉 T+N 时点股价 + 计算超额收益（3 个基准）
- [x] `event_detector.py` 接入 HKEX 公告流 + iFind 价格异动检测
- [x] `attribution.py` 完整三层归因引擎
- [x] `review_workflow.py` 自动生成 review_draft + CLI/API 接收人工 review
- [x] `benchmarks.py` 三个基准维护
- [x] 修改 orchestrator/graph.py：在 `synthesize` 后强制插入 `create_snapshot` 节点，没创建快照不能输出决策

**v1.2 自动化扩展（必须完成）：**
- [x] `code_mapper.py` 完整实现，三策略自动映射 + 置信度评分
- [x] `earnings_comparator.py` + `mapping_rules.yaml` 财报口径映射
- [x] `alerts.py` 警报路由 + `config/alerts.yaml`
- [x] DB migration: ipo_lifecycle_states, ipo_state_transitions, code_mappings, scheduler_runs, alerts 5 张表
- [x] `ipo_lifecycle/state_machine.py` + `states.py` + `state_detectors.py`
- [x] `ipo_lifecycle/terminal_handlers.py` 撤回 / 聆讯失败处理
- [x] `ipo_lifecycle/stale_detector.py` 超时检测 + 警报
- [x] `ipo_lifecycle/ah_special.py` A+H 特殊处理
- [x] `schedulers/base.py` + 三层调度器全部实现
- [x] `schedulers/airflow_dags/` 至少 4 个生产 DAG
- [x] 调度器配置文件 `config/schedulers.yaml`（cron 表达式、重试策略、并发度）
- [x] 完整对抗测试套件（见下方 DONE 条件）

**DONE 条件（v1.2）：**
- 一次完整分析必须先创建 snapshot 才能输出决策（写入流程的强制点）
- **三个状态机仿真测试必须通过**：
  - 仿真"正常上市"：PRE_LISTING → PRICING → LISTED → 自动跑 11 个 checkpoint → TERMINATED
  - 仿真"撤回"：PRE_LISTING → WITHDRAWN → 自动生成 terminal_review_draft
  - 仿真"默默失效"：PRE_LISTING 停留 181 天 → stale_detector 触发 critical alert
- **代码映射准确率测试**：用 30 家历史已上市公司测试，high confidence 映射准确率 ≥ 95%
- **财报比对测试**：用 5 家已上市公司的 PHIP 招股书 + 上市后首份年报，自动比对结果与人工比对吻合（口径差异在容忍范围内）
- **调度器对抗测试**：
  - 重复运行同一 checkpoint 必须幂等（同 snapshot_id + checkpoint_day 不重复写）
  - 调度器中断后重启必须从 last successful run 恢复
  - 错过的 checkpoint 必须用 historical close price 补跑
- **对抗测试**：attempt to UPDATE prediction_snapshots 必须在 DB 层失败
- **真实端到端**：用 1 家已上市公司（推荐晶泰 2228.HK）做完整 lifecycle 模拟：从招股书时点跑分析 → 创建 snapshot → 状态机自动推进 → outcome_tracker 自动跑完 11 个 checkpoint → attribution 自动跑 → 生成 review_draft → 与人工事后判断对比

### Phase 8: 回测与校准（4-5 天）★ 最关键
**Deliverables：**
- [x] `backtest/as_of_data.py`（防泄漏严格审核）
- [x] `backtest/runner.py` Walk-forward
- [x] `backtest/regime_detection.py`
- [x] `backtest/metrics.py`
- [x] `backtest/calibration.py`
- [x] 对 2022 至今所有港股科技/AH IPO 做回测
- [x] 校准后的 `valuation_weights.yaml` 写回 config

**DONE 条件：** 回测报告显示系统在 50+ 历史样本上的预测准确率，且权重已校准。

### Phase 9: 端到端验证（1-2 天）
**Deliverables：**
- [x] 用 3-5 家已上市公司（晶泰、黑芝麻、越疆、宁德H、地平线）作完整回测案例
- [x] 文档化每个案例的预测 vs 实际差异
- [x] 性能压测（每个 IPO 完整分析应在 30 分钟内完成）

**DONE 条件：** 端到端测试 `tests/e2e/` 全过；性能达标。

### Phase 10: 持续学习闭环（3-4 天）— v1.1 新增
**Deliverables：**
- [x] `learning_loop/drift_detector.py` 完整实现（CUSUM + PSI 双指标）
- [x] `learning_loop/attribution_aggregator.py` 跨样本归因汇总
- [x] `learning_loop/counterfactual.py` 反事实分析
- [x] `learning_loop/adjustment_proposer.py` 自动提议
- [x] `learning_loop/adjustment_applier.py` 应用调整（强制人工批准 gate）
- [x] `learning_loop/version_manager.py` 版本管理
- [x] `learning_loop/reports.py` 月度学习报告
- [x] 在 Phase 8 跑出的 50+ 历史回测样本上做完整 drift 检测和首轮调整提议
- [x] 至少完成 1 轮完整的 propose → review → apply → re-backtest 闭环
- [x] CLI: `scripts/run_learning_cycle.py` 一键执行月度学习
- [x] 文档：`docs/LEARNING_PROTOCOL.md` 写明哪些调整能自动提议、哪些必须人工设计

**DONE 条件：**
- 系统能在累积 N 个 outcome 后自动产出 DriftSignal 和 ProposedAdjustment 写入 prediction_reviews
- 人工 review CLI（`scripts/review_proposals.py`）能接受/拒绝提议
- 应用调整必须 bump 版本号，写入 config_snapshot 历史
- 对抗测试：未经 reviewer 批准的 proposal 不能被 applier 应用
- 闭环测试：propose → accept → apply → 用最近 5 个样本验证调整有效（不能让指标变差）

---

## 5. 数据库 Schema 核心要点

完整 schema 见 `docs/DATA_SCHEMA.md`。关键表：

```sql
-- IPO 主表
CREATE TABLE ipo_events (
    id UUID PRIMARY KEY,
    stock_code VARCHAR(10),
    company_name_zh VARCHAR(200),
    company_name_en VARCHAR(200),
    listing_type VARCHAR(20),  -- enum ListingType
    industry_code VARCHAR(20),
    sponsor_ids UUID[],
    a1_filing_date DATE,       -- 首次递表
    hearing_date DATE,
    pricing_date DATE,
    listing_date DATE,
    issue_size_hkd NUMERIC,
    use_of_proceeds JSONB,
    regulatory_regime VARCHAR(30),  -- enum RegulatoryRegime
    is_18c_pre_commercial BOOLEAN,
    ah_pair_a_code VARCHAR(10),  -- A股代码（如果是AH）
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ
);

-- 基石投资人（实体）
CREATE TABLE cornerstone_investors (
    id UUID PRIMARY KEY,
    name_zh VARCHAR(200),
    name_en VARCHAR(200),
    category VARCHAR(50),  -- sovereign/local_govt/strategic/foreign_LT/family_office/hedge/insurance/bank_wm
    parent_org VARCHAR(200),
    home_country VARCHAR(50),
    signal_strength_score NUMERIC  -- 0-100, 周期性 recompute
);

-- 基石投资事件
CREATE TABLE cornerstone_investments (
    id UUID PRIMARY KEY,
    ipo_id UUID REFERENCES ipo_events,
    investor_id UUID REFERENCES cornerstone_investors,
    commitment_amount_hkd NUMERIC,
    pct_of_offering NUMERIC,
    lockup_months INT,
    disclosure_date DATE,
    is_anchor BOOLEAN  -- 锚定投资人 vs 基石
);

-- IPO 定价与认购
CREATE TABLE ipo_pricings (
    ipo_id UUID PRIMARY KEY REFERENCES ipo_events,
    price_range_low NUMERIC,
    price_range_high NUMERIC,
    final_price NUMERIC,
    intl_oversubscription NUMERIC,
    retail_oversubscription NUMERIC,
    margin_subscription_multiple NUMERIC,  -- 孖展认购倍数
    allocation_mechanism VARCHAR(10),  -- A or B (post-20250804)
    final_public_allocation_pct NUMERIC
);

-- IPO 上市后表现（关键 KPI）
CREATE TABLE ipo_postmarket (
    ipo_id UUID PRIMARY KEY REFERENCES ipo_events,
    day1_return NUMERIC,
    day5_return NUMERIC,
    day22_return NUMERIC,
    day126_return NUMERIC,  -- 6个月（解禁前）
    day127_return NUMERIC,  -- 解禁日
    day252_return NUMERIC,  -- 12个月
    max_drawdown_d126 NUMERIC,
    cornerstone_held_after_lockup BOOLEAN  -- 锁定期后是否减持（通过持股变动追踪）
);

-- 招股书原文与抽取
CREATE TABLE prospectus_docs (
    id UUID PRIMARY KEY,
    ipo_id UUID REFERENCES ipo_events,
    version VARCHAR(50),  -- PHIP/AP1/AP2/listing
    filing_date DATE,
    pdf_path VARCHAR(500),
    page_count INT
);

CREATE TABLE prospectus_extractions (
    id UUID PRIMARY KEY,
    prospectus_id UUID REFERENCES prospectus_docs,
    extraction JSONB,  -- 完整 ProspectusExtraction 序列化
    extraction_version VARCHAR(20),
    extracted_at TIMESTAMPTZ,
    needs_human_review BOOLEAN DEFAULT FALSE
);

-- 保荐人 track record
CREATE TABLE sponsors (
    id UUID PRIMARY KEY,
    name VARCHAR(200),
    is_sfc_licensed BOOLEAN,
    track_record_score NUMERIC,
    cases_count_24m INT,
    avg_day1_return NUMERIC,
    avg_6m_return NUMERIC
);

-- ========== v1.1 新增：预测生命周期 ==========

-- 预测快照（严格不可变）
CREATE TABLE prediction_snapshots (
    id UUID PRIMARY KEY,
    ipo_id UUID REFERENCES ipo_events,
    as_of_date DATE NOT NULL,
    prospectus_version VARCHAR(50),
    
    -- 完整快照（用于复现）
    input_data_hash VARCHAR(64) NOT NULL,  -- SHA256 of full snapshot
    input_data_snapshot JSONB NOT NULL,
    agent_outputs JSONB NOT NULL,
    valuation_output JSONB NOT NULL,
    debate_output JSONB NOT NULL,
    decision JSONB NOT NULL,
    
    -- 版本元数据
    system_version VARCHAR(50) NOT NULL,
    model_versions JSONB NOT NULL,
    config_snapshot JSONB NOT NULL,
    total_cost_usd NUMERIC,
    runtime_seconds NUMERIC,
    
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (ipo_id, as_of_date, prospectus_version)
);

-- 强制 immutability：阻止 UPDATE 和 DELETE
CREATE OR REPLACE FUNCTION prevent_snapshot_modification()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'prediction_snapshots is immutable. Use prediction_reviews to attach notes.';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER snapshot_no_update
    BEFORE UPDATE ON prediction_snapshots
    FOR EACH ROW EXECUTE FUNCTION prevent_snapshot_modification();

CREATE TRIGGER snapshot_no_delete
    BEFORE DELETE ON prediction_snapshots
    FOR EACH ROW EXECUTE FUNCTION prevent_snapshot_modification();

-- 预测后的 checkpoint outcome
CREATE TABLE prediction_outcomes (
    id UUID PRIMARY KEY,
    snapshot_id UUID REFERENCES prediction_snapshots,
    checkpoint_day INT NOT NULL,  -- T+1, 5, 10, 22, 30, 60, 90, 126, 180, 252, 360
    
    return_since_ipo NUMERIC,       -- vs 发行价
    return_since_listing NUMERIC,   -- vs 首日收盘
    max_drawdown NUMERIC,
    relative_return_hsi NUMERIC,
    relative_return_hstech NUMERIC,
    relative_return_industry NUMERIC,
    
    events_in_window JSONB,
    earnings_released BOOLEAN DEFAULT FALSE,
    earnings_beat_extraction BOOLEAN,
    
    cornerstone_held_pct NUMERIC,
    cornerstone_reduced BOOLEAN,
    
    price_in_predicted_range BOOLEAN,
    decision_correct BOOLEAN,
    
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (snapshot_id, checkpoint_day)
);

-- 上市后关键事件流
CREATE TABLE post_ipo_events (
    id UUID PRIMARY KEY,
    ipo_id UUID REFERENCES ipo_events,
    event_date DATE NOT NULL,
    event_type VARCHAR(50),  -- earnings/profit_warning/major_contract/regulatory/management_change/cornerstone_disclosure/placement/share_buyback/other
    severity VARCHAR(20),    -- critical/major/minor
    description TEXT,
    source_url VARCHAR(500),
    price_impact_1d NUMERIC,
    price_impact_5d NUMERIC,
    detected_at TIMESTAMPTZ DEFAULT NOW()
);

-- 人工 review 笔记（唯一允许写入的"追加"表）
CREATE TABLE prediction_reviews (
    id UUID PRIMARY KEY,
    snapshot_id UUID REFERENCES prediction_snapshots,
    review_checkpoint_day INT,
    reviewer VARCHAR(100),
    
    what_we_got_right TEXT,
    what_we_got_wrong TEXT,
    
    primary_attribution VARCHAR(50),
    attribution_details JSONB,
    
    proposed_adjustments JSONB,
    adjustment_status VARCHAR(20),  -- proposed/accepted/rejected/implemented
    applied_at TIMESTAMPTZ,
    applied_version VARCHAR(50),
    
    notes_md TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 配置 / 提示词版本历史
CREATE TABLE config_versions (
    id UUID PRIMARY KEY,
    target_path VARCHAR(500) NOT NULL,  -- e.g. "config/valuation_weights.yaml"
    version VARCHAR(50) NOT NULL,
    content_hash VARCHAR(64),
    content JSONB,                       -- 完整内容快照
    change_type VARCHAR(50),             -- manual/learning_loop_applied/rollback
    source_review_id UUID REFERENCES prediction_reviews,
    applied_by VARCHAR(100),
    applied_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (target_path, version)
);

-- 索引
CREATE INDEX idx_snapshots_ipo ON prediction_snapshots (ipo_id);
CREATE INDEX idx_outcomes_snapshot ON prediction_outcomes (snapshot_id);
CREATE INDEX idx_outcomes_checkpoint ON prediction_outcomes (checkpoint_day);
CREATE INDEX idx_events_ipo_date ON post_ipo_events (ipo_id, event_date);
CREATE INDEX idx_reviews_status ON prediction_reviews (adjustment_status);

-- ========== v1.2 新增：IPO 状态机 + 调度器 + 警报 ==========

-- IPO 生命周期状态（每个 IPO 只有一条当前状态）
CREATE TABLE ipo_lifecycle_states (
    id UUID PRIMARY KEY,
    ipo_id UUID REFERENCES ipo_events UNIQUE,
    current_state VARCHAR(30) NOT NULL,
    state_entered_at TIMESTAMPTZ NOT NULL,
    state_metadata JSONB,  -- 例: LISTED 时存 {"listing_date": "2025-01-15", "first_day_close": 12.5}
    last_checked_at TIMESTAMPTZ NOT NULL,
    is_terminal BOOLEAN DEFAULT FALSE
);

-- 状态转换审计日志
CREATE TABLE ipo_state_transitions (
    id UUID PRIMARY KEY,
    ipo_id UUID REFERENCES ipo_events,
    from_state VARCHAR(30),
    to_state VARCHAR(30) NOT NULL,
    transition_at TIMESTAMPTZ NOT NULL,
    triggered_by VARCHAR(50),  -- 'auto_detector'/'manual_reviewer'/'timeout'/'event_driven'
    detection_evidence JSONB,
    reviewer VARCHAR(100)
);
CREATE INDEX idx_transitions_ipo ON ipo_state_transitions (ipo_id, transition_at);

-- 公司名 → 股票代码映射
CREATE TABLE code_mappings (
    id UUID PRIMARY KEY,
    ipo_id UUID REFERENCES ipo_events UNIQUE,
    company_name_zh VARCHAR(200),
    company_name_en VARCHAR(200),
    hk_stock_code VARCHAR(10),
    a_share_code VARCHAR(10),
    us_adr_code VARCHAR(10),
    confirmed_at TIMESTAMPTZ,
    confirmation_source VARCHAR(50),  -- 'hkex_announcement'/'ifind_match'/'manual'/'hybrid'
    confidence VARCHAR(20),  -- 'high'/'medium'/'low'
    requires_review BOOLEAN DEFAULT FALSE
);

-- 调度器执行记录
CREATE TABLE scheduler_runs (
    id UUID PRIMARY KEY,
    scheduler_type VARCHAR(30),  -- 'high_freq'/'daily'/'event_driven'
    run_id VARCHAR(100) UNIQUE NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    snapshots_processed INT DEFAULT 0,
    events_detected INT DEFAULT 0,
    errors_encountered INT DEFAULT 0,
    error_details JSONB,
    status VARCHAR(20) DEFAULT 'running'  -- 'running'/'completed'/'failed'
);
CREATE INDEX idx_scheduler_runs_started ON scheduler_runs (scheduler_type, started_at DESC);

-- 警报记录
CREATE TABLE alerts (
    id UUID PRIMARY KEY,
    level VARCHAR(20) NOT NULL,  -- 'info'/'warning'/'critical'
    category VARCHAR(50) NOT NULL,
    related_ipo_id UUID REFERENCES ipo_events,
    related_snapshot_id UUID REFERENCES prediction_snapshots,
    message TEXT NOT NULL,
    actionable_info TEXT,  -- 应该做什么
    detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    acknowledged_at TIMESTAMPTZ,
    acknowledged_by VARCHAR(100),
    metadata JSONB
);
CREATE INDEX idx_alerts_unacked ON alerts (acknowledged_at, level) WHERE acknowledged_at IS NULL;

-- 财报比对结果
CREATE TABLE earnings_comparisons (
    id UUID PRIMARY KEY,
    snapshot_id UUID REFERENCES prediction_snapshots,
    report_period VARCHAR(20),  -- 'FY2025'/'H1-2025'/'Q1-2025'
    filing_date DATE,
    actual_revenue NUMERIC,
    predicted_revenue_from_prospectus NUMERIC,
    revenue_deviation_pct NUMERIC,
    actual_net_profit NUMERIC,
    predicted_net_profit NUMERIC,
    profit_deviation_pct NUMERIC,
    actual_gross_margin NUMERIC,
    predicted_gross_margin NUMERIC,
    margin_deviation_pp NUMERIC,
    qualitative_deviations JSONB,
    overall_assessment VARCHAR(30),  -- 'beat'/'in_line'/'miss'/'significant_miss'
    confidence VARCHAR(20),
    notes TEXT,
    requires_human_review BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (snapshot_id, report_period)
);

-- ========== v1.2.1 新增：UI 集成支撑 ==========

-- 用户账号
CREATE TABLE user_accounts (
    id UUID PRIMARY KEY,
    email VARCHAR(200) NOT NULL UNIQUE,
    display_name VARCHAR(100),
    sso_provider VARCHAR(50),       -- 'okta'/'azure_ad'/'local'
    sso_subject VARCHAR(200),        -- SSO 提供方的用户 ID
    is_active BOOLEAN DEFAULT TRUE,
    last_login_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (sso_provider, sso_subject)
);

-- 用户角色（多对多支持）
CREATE TABLE user_roles (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES user_accounts ON DELETE CASCADE,
    role VARCHAR(50) NOT NULL,       -- 'viewer'/'reviewer'/'senior_reviewer'/'operator'/'admin'/'auditor'
    granted_at TIMESTAMPTZ DEFAULT NOW(),
    granted_by UUID REFERENCES user_accounts,
    expires_at TIMESTAMPTZ,
    UNIQUE (user_id, role)
);
CREATE INDEX idx_user_roles_user ON user_roles (user_id);

-- 审计日志（所有 write 操作自动写入）
CREATE TABLE audit_logs (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES user_accounts,
    user_email VARCHAR(200),         -- 冗余存储，便于审计查询
    action VARCHAR(100) NOT NULL,    -- e.g. 'review.submitted'/'proposal.accepted'/'config.modified'
    resource_type VARCHAR(50),       -- 'snapshot'/'review'/'proposal'/'config'
    resource_id VARCHAR(200),
    before_state JSONB,
    after_state JSONB,
    diff JSONB,                      -- 计算好的 diff
    ip_address INET,
    user_agent TEXT,
    request_id VARCHAR(100),
    api_endpoint VARCHAR(200),
    success BOOLEAN DEFAULT TRUE,
    error_message TEXT,
    occurred_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_audit_user_time ON audit_logs (user_id, occurred_at DESC);
CREATE INDEX idx_audit_resource ON audit_logs (resource_type, resource_id);
CREATE INDEX idx_audit_action ON audit_logs (action, occurred_at DESC);

-- 审计日志 immutable 保护
CREATE TRIGGER audit_no_update
    BEFORE UPDATE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION prevent_snapshot_modification();
CREATE TRIGGER audit_no_delete
    BEFORE DELETE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION prevent_snapshot_modification();

-- 聊天会话
CREATE TABLE chat_sessions (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES user_accounts,
    snapshot_id UUID REFERENCES prediction_snapshots,
    ipo_id UUID REFERENCES ipo_events,
    title VARCHAR(200),              -- 自动生成或用户命名
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_active_at TIMESTAMPTZ DEFAULT NOW(),
    archived BOOLEAN DEFAULT FALSE
);
CREATE INDEX idx_chat_sessions_user ON chat_sessions (user_id, last_active_at DESC);

-- 聊天消息
CREATE TABLE chat_messages (
    id UUID PRIMARY KEY,
    session_id UUID REFERENCES chat_sessions ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL,       -- 'user'/'assistant'/'system'/'tool'
    content TEXT,
    content_json JSONB,              -- 富内容（工具调用、引用等）
    citations JSONB,                  -- 引用列表（招股书页码 + 数据源）
    tools_used JSONB,                 -- 此消息调用了哪些工具
    cost_usd NUMERIC,
    tokens_input INT,
    tokens_output INT,
    model_used VARCHAR(100),
    runtime_ms INT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    sequence INT NOT NULL             -- 会话内消息序号
);
CREATE INDEX idx_chat_messages_session ON chat_messages (session_id, sequence);

-- What-If 计算记录（用于复现和归因）
CREATE TABLE whatif_calculations (
    id UUID PRIMARY KEY,
    snapshot_id UUID REFERENCES prediction_snapshots,
    user_id UUID REFERENCES user_accounts,
    modified_assumptions JSONB NOT NULL,   -- 用户改了什么
    original_distribution JSONB,            -- 原估值分布
    new_distribution JSONB,                 -- 新估值分布
    cost_usd NUMERIC,
    runtime_ms INT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_whatif_snapshot ON whatif_calculations (snapshot_id, created_at DESC);

-- SSE / WebSocket 事件持久化（用于追溯和重放）
CREATE TABLE realtime_events (
    id UUID PRIMARY KEY,
    event_type VARCHAR(100) NOT NULL,      -- 'alert.created'/'snapshot.updated'/...
    related_ipo_id UUID REFERENCES ipo_events,
    related_snapshot_id UUID REFERENCES prediction_snapshots,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    broadcast_count INT DEFAULT 0
);
CREATE INDEX idx_realtime_events_time ON realtime_events (created_at DESC);
CREATE INDEX idx_realtime_events_type ON realtime_events (event_type, created_at DESC);

-- API rate limit 状态（如果不用 Redis 则用此表）
CREATE TABLE api_rate_limit_state (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES user_accounts,
    endpoint_pattern VARCHAR(200),
    window_start TIMESTAMPTZ,
    request_count INT,
    UNIQUE (user_id, endpoint_pattern, window_start)
);
```

---

## 6. 核心 Pydantic Schemas

**`src/hk_ipo_agent/common/schemas.py`** 必须定义以下模型：

```python
from datetime import date
from decimal import Decimal
from typing import List, Dict, Optional, Literal
from pydantic import BaseModel, Field
from .enums import ListingType, AgentRole, DecisionType, RegulatoryRegime

# ===== 招股书抽取 =====
class Citation(BaseModel):
    page: int
    section: Optional[str] = None
    chunk_id: Optional[str] = None
    text_snippet: Optional[str] = None

class FinancialSnapshot(BaseModel):
    fiscal_year: int
    fiscal_period: Literal["FY", "H1", "Q1", "Q2", "Q3", "5M", "9M"]
    revenue_rmb: Optional[Decimal] = None
    gross_profit_rmb: Optional[Decimal] = None
    gross_margin: Optional[float] = None
    rd_expense_rmb: Optional[Decimal] = None
    rd_pct_of_revenue: Optional[float] = None
    net_profit_rmb: Optional[Decimal] = None
    adjusted_net_profit_rmb: Optional[Decimal] = None
    operating_cash_flow_rmb: Optional[Decimal] = None
    cash_balance_rmb: Optional[Decimal] = None
    citation: Citation

class ShareholderEntry(BaseModel):
    name: str
    pct_pre_ipo: float
    is_controlling: bool
    is_pre_ipo_investor: bool
    last_round_valuation_rmb: Optional[Decimal] = None
    last_round_date: Optional[date] = None
    has_buyback_clause: bool = False
    citation: Citation

class CustomerConcentration(BaseModel):
    fiscal_year: int
    top1_pct: float
    top5_pct: float
    top1_name: Optional[str] = None
    citation: Citation

class RiskFactor(BaseModel):
    category: Literal["business", "industry", "financial", "regulatory", "macro", "structural"]
    description: str
    severity: Literal["high", "medium", "low"]
    citation: Citation

class Ch18CQualification(BaseModel):
    is_commercialized: bool
    revenue_threshold_met: bool  # ≥2.5亿RMB
    rd_intensity_met: bool       # 取决于商业化状态
    market_cap_threshold_hkd: Decimal
    lead_investors: List[str]    # 领航投资人
    citation: Citation

class ProspectusExtraction(BaseModel):
    """招股书完整结构化抽取结果。这是整个系统最核心的数据对象。"""
    prospectus_id: str
    company_name_zh: str
    company_name_en: Optional[str] = None
    stock_code: Optional[str] = None  # 上市后才有
    listing_type: ListingType
    industry_code: str
    industry_description: str
    
    # 业务
    business_model: str
    revenue_streams: List[Dict]  # [{name, fy, amount, pct}, ...]
    customer_concentration: List[CustomerConcentration]
    supplier_concentration: List[CustomerConcentration]
    
    # 财务
    financials: List[FinancialSnapshot]
    
    # 股东结构
    shareholders: List[ShareholderEntry]
    pre_ipo_valuation_rmb: Optional[Decimal] = None
    last_round_date: Optional[date] = None
    
    # 18C / 18A 特定
    ch18c_qualification: Optional[Ch18CQualification] = None
    
    # AH 特定
    a_share_code: Optional[str] = None
    a_share_price_at_filing: Optional[Decimal] = None
    
    # 募资用途
    use_of_proceeds: List[Dict]  # [{purpose, pct}, ...]
    
    # 风险
    risk_factors: List[RiskFactor]
    
    # 中介
    sponsors: List[str]
    
    # 元数据
    extraction_version: str
    extracted_at: str
    needs_human_review: bool = False
    review_reasons: List[str] = Field(default_factory=list)


# ===== Agent 输出 =====
class Finding(BaseModel):
    statement: str
    evidence: str
    citations: List[Citation]
    confidence: Literal["high", "medium", "low"]

class DataSource(BaseModel):
    source: Literal["prospectus", "ifind", "hkex", "kb_cornerstones", 
                    "kb_sponsors", "kb_comparables", "web_search"]
    detail: str

class AgentOutput(BaseModel):
    agent_role: AgentRole
    scores: Dict[str, float]  # 0-100 各维度
    overall_score: float
    key_findings: List[Finding]
    uncertainty_flags: List[str]
    data_sources_used: List[DataSource]
    cost_usd: float
    runtime_seconds: float


# ===== 估值输出 =====
class ValuationDistribution(BaseModel):
    p10: Decimal
    p25: Decimal
    p50: Decimal  # median
    p75: Decimal
    p90: Decimal
    mean: Decimal
    std: Decimal

class SingleModelValuation(BaseModel):
    model_name: str
    applicable: bool
    valuation_distribution: ValuationDistribution
    key_assumptions: Dict
    citations: List[Citation]

class ValuationEnsembleOutput(BaseModel):
    company_id: str
    single_models: List[SingleModelValuation]
    weights_used: Dict[str, float]
    ensemble_distribution: ValuationDistribution
    implied_price_range: Dict[str, Decimal]  # {low, fair, high}
    notes: List[str]


# ===== 辩论输出 =====
class DebateRound(BaseModel):
    round_number: int
    bull_argument: str
    bear_argument: str
    devil_challenge: str
    resolution: Optional[str] = None

class DebateOutput(BaseModel):
    rounds: List[DebateRound]
    final_consensus: str
    unresolved_issues: List[str]


# ===== 最终决策 =====
class TriggerRule(BaseModel):
    condition: str  # 自然语言描述
    action: str
    severity: Literal["info", "warning", "critical"]

class FinalDecision(BaseModel):
    decision: DecisionType
    confidence: float  # 0-1
    suggested_allocation_pct: Optional[float] = None  # 占基金 NAV 比例
    price_range_low: Decimal
    price_range_fair: Decimal
    price_range_high: Decimal
    expected_return_6m: ValuationDistribution
    expected_return_12m: ValuationDistribution
    
    scorecard: Dict[str, float]  # 综合评分
    
    key_reasons_for: List[str]
    key_reasons_against: List[str]
    
    trigger_rules: List[TriggerRule]
    
    references_to_agent_outputs: List[str]  # 各 agent finding id


# ===== v1.1 新增：预测生命周期 =====
from uuid import UUID
from datetime import datetime
from typing import Any
from pydantic import ConfigDict

class PredictionSnapshot(BaseModel):
    """不可变预测快照。一旦创建，任何字段都不可修改。"""
    model_config = ConfigDict(frozen=True)
    
    id: UUID
    ipo_id: UUID
    as_of_date: date
    prospectus_version: str
    
    input_data_hash: str  # SHA256 of (input_data_snapshot + agent_outputs + valuation + debate + decision)
    input_data_snapshot: Dict[str, Any]
    
    agent_outputs: Dict[str, AgentOutput]  # role -> output
    valuation_output: ValuationEnsembleOutput
    debate_output: DebateOutput
    decision: FinalDecision
    
    system_version: str
    model_versions: Dict[str, str]  # e.g. {"fundamental": "sonnet-4@v1.2", ...}
    config_snapshot: Dict[str, Any]
    total_cost_usd: Decimal
    runtime_seconds: float
    
    created_at: datetime

class PostIPOEvent(BaseModel):
    event_date: date
    event_type: Literal["earnings", "profit_warning", "major_contract", 
                        "regulatory", "management_change", 
                        "cornerstone_disclosure", "placement", 
                        "share_buyback", "other"]
    severity: Literal["critical", "major", "minor"]
    description: str
    source_url: Optional[str] = None
    price_impact_1d: Optional[float] = None
    price_impact_5d: Optional[float] = None

class PredictionOutcome(BaseModel):
    """T+N 时点的实际结果"""
    snapshot_id: UUID
    checkpoint_day: int  # 1, 5, 10, 22, 30, 60, 90, 126, 180, 252, 360
    
    return_since_ipo: float
    return_since_listing: Optional[float] = None
    max_drawdown: float
    relative_return_hsi: float
    relative_return_hstech: float
    relative_return_industry: float
    
    events_in_window: List[PostIPOEvent]
    earnings_released: bool = False
    earnings_beat_extraction: Optional[bool] = None
    
    cornerstone_held_pct: Optional[float] = None
    cornerstone_reduced: Optional[bool] = None
    
    price_in_predicted_range: bool
    decision_correct: bool
    
    recorded_at: datetime

class AgentErrorAnalysis(BaseModel):
    agent_role: AgentRole
    score_calibration: float  # 与实际结果的校准度（如 Brier score）
    findings_accuracy: float  # finding 被事实验证的比例
    critical_misses: List[str]      # 重要但漏掉的判断
    critical_correct_calls: List[str]

class ValuationErrorAnalysis(BaseModel):
    model_name: str
    predicted_p50: Decimal
    actual_price: Decimal
    pct_error: float
    in_p10_p90_range: bool

class DebateQualityAnalysis(BaseModel):
    bear_predictions_validated: int
    bear_predictions_total: int
    bull_predictions_validated: int
    bull_predictions_total: int
    unaddressed_critical_risks: List[str]  # Bear 提了但 Synthesizer 没采纳，结果应验

class ProposedAdjustment(BaseModel):
    target_path: str  # e.g. "config/valuation_weights.yaml" or "prompts/agents/fundamental.md"
    adjustment_type: Literal["weight_change", "prompt_edit", "factor_add", 
                             "factor_remove", "logic_change", "agent_disable"]
    current_value: Any
    proposed_value: Any
    rationale: str
    evidence_snapshot_ids: List[UUID]  # 支撑此提议的样本
    expected_impact: str
    confidence: Literal["high", "medium", "low"]

class Attribution(BaseModel):
    snapshot_id: UUID
    checkpoint_day: int
    
    agent_errors: List[AgentErrorAnalysis]
    valuation_errors: List[ValuationErrorAnalysis]
    debate_quality: DebateQualityAnalysis
    
    primary_attribution: str  # 主要责任方（agent/model name）
    llm_diagnosis: str        # Opus 综合诊断
    proposed_adjustments: List[ProposedAdjustment]

class PredictionReview(BaseModel):
    """人工 review 记录。这是唯一允许在生命周期数据上追加的表。"""
    snapshot_id: UUID
    review_checkpoint_day: int
    reviewer: str
    
    what_we_got_right: str
    what_we_got_wrong: str
    
    primary_attribution: str
    attribution_details: Attribution
    
    proposed_adjustments: List[ProposedAdjustment]
    adjustment_status: Literal["proposed", "accepted", "rejected", "implemented"]
    applied_at: Optional[datetime] = None
    applied_version: Optional[str] = None
    
    notes_md: str
    created_at: datetime

class DriftSignal(BaseModel):
    detection_time: datetime
    signal_type: Literal["accuracy_drop", "valuation_bias", 
                         "agent_calibration_drift", "missing_factor",
                         "regime_break", "bear_miss_rate_high"]
    severity: Literal["critical", "warning", "info"]
    affected_dimensions: Dict[str, str]  # e.g. {"listing_type": "CH18C", "industry": "AI"}
    metric_value: float
    threshold: float
    sample_count: int
    evidence: str
    related_snapshot_ids: List[UUID]


# ===== v1.2 新增：IPO 生命周期状态机 + 自动化 =====
class IPOLifecycleStateType(str, Enum):
    PRE_LISTING = "pre_listing"
    PRICING = "pricing"
    LISTED = "listed"
    WITHDRAWN = "withdrawn"
    HEARING_FAILED = "hearing_failed"
    PRICING_PULLED = "pricing_pulled"
    TERMINATED = "terminated"

# 合法转换规则（在 states.py 强制实施）
VALID_TRANSITIONS = {
    IPOLifecycleStateType.PRE_LISTING: [
        IPOLifecycleStateType.PRICING,
        IPOLifecycleStateType.WITHDRAWN,
        IPOLifecycleStateType.HEARING_FAILED,
    ],
    IPOLifecycleStateType.PRICING: [
        IPOLifecycleStateType.LISTED,
        IPOLifecycleStateType.WITHDRAWN,
        IPOLifecycleStateType.PRICING_PULLED,
    ],
    IPOLifecycleStateType.LISTED: [
        IPOLifecycleStateType.TERMINATED,
    ],
    # WITHDRAWN/HEARING_FAILED/PRICING_PULLED/TERMINATED 都是终态
}

class IPOLifecycleState(BaseModel):
    ipo_id: UUID
    current_state: IPOLifecycleStateType
    state_entered_at: datetime
    state_metadata: Dict[str, Any]  # 例: LISTED 状态下存 {"listing_date": "2025-01-15"}
    last_checked_at: datetime
    is_terminal: bool

class StateTransition(BaseModel):
    ipo_id: UUID
    from_state: Optional[IPOLifecycleStateType]
    to_state: IPOLifecycleStateType
    transition_at: datetime
    triggered_by: Literal["auto_detector", "manual_reviewer", "timeout", "event_driven"]
    detection_evidence: Dict[str, Any]
    reviewer: Optional[str] = None

class CodeMapping(BaseModel):
    ipo_id: UUID
    company_name_zh: str
    company_name_en: Optional[str] = None
    hk_stock_code: Optional[str] = None
    a_share_code: Optional[str] = None
    us_adr_code: Optional[str] = None
    confirmed_at: datetime
    confirmation_source: Literal["hkex_announcement", "ifind_match", "manual", "hybrid"]
    confidence: Literal["high", "medium", "low"]
    requires_review: bool = False

class SchedulerRun(BaseModel):
    scheduler_type: Literal["high_freq", "daily", "event_driven"]
    run_id: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    snapshots_processed: int = 0
    events_detected: int = 0
    errors_encountered: int = 0
    error_details: Optional[List[Dict]] = None
    status: Literal["running", "completed", "failed"]

class Alert(BaseModel):
    level: Literal["info", "warning", "critical"]
    category: str
    related_ipo_id: Optional[UUID] = None
    related_snapshot_id: Optional[UUID] = None
    message: str
    actionable_info: str  # 必填：应该做什么
    detected_at: datetime
    acknowledged_at: Optional[datetime] = None
    acknowledged_by: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

class EarningsComparison(BaseModel):
    snapshot_id: UUID
    report_period: str  # e.g. "FY2025", "H1-2025"
    filing_date: date
    
    actual_revenue: Optional[Decimal] = None
    predicted_revenue_from_prospectus: Optional[Decimal] = None
    revenue_deviation_pct: Optional[float] = None
    
    actual_net_profit: Optional[Decimal] = None
    predicted_net_profit: Optional[Decimal] = None
    profit_deviation_pct: Optional[float] = None
    
    actual_gross_margin: Optional[float] = None
    predicted_gross_margin: Optional[float] = None
    margin_deviation_pp: Optional[float] = None  # percentage points
    
    qualitative_deviations: List[str] = Field(default_factory=list)
    overall_assessment: Literal["beat", "in_line", "miss", "significant_miss"]
    confidence: Literal["high", "medium", "low"]
    notes: str = ""
    requires_human_review: bool = False


# ===== v1.2.1 新增：UI 集成支撑 =====
class UserRole(str, Enum):
    VIEWER = "viewer"
    REVIEWER = "reviewer"
    SENIOR_REVIEWER = "senior_reviewer"
    OPERATOR = "operator"
    ADMIN = "admin"
    AUDITOR = "auditor"

class UserAccount(BaseModel):
    id: UUID
    email: str
    display_name: Optional[str] = None
    sso_provider: Literal["okta", "azure_ad", "local"]
    sso_subject: str
    is_active: bool = True
    roles: List[UserRole]
    last_login_at: Optional[datetime] = None

class Permission(str, Enum):
    """细粒度权限定义。前后端双方使用。"""
    # 读权限
    READ_SNAPSHOTS = "snapshots.read"
    READ_REVIEWS = "reviews.read"
    READ_PROPOSALS = "proposals.read"
    READ_AUDIT = "audit.read"
    READ_SETTINGS = "settings.read"
    # 写权限
    SUBMIT_REVIEW = "reviews.submit"
    PROPOSE_ADJUSTMENT = "proposals.propose"
    ACCEPT_PROPOSAL = "proposals.accept"
    REJECT_PROPOSAL = "proposals.reject"
    ACK_ALERT = "alerts.acknowledge"
    TRIGGER_ANALYSIS = "analysis.trigger"
    RUN_WHATIF = "whatif.run"
    CHAT_WITH_AGENT = "chat.use"
    # 系统权限
    MANAGE_CONFIG = "config.manage"
    MANAGE_USERS = "users.manage"
    MANAGE_SCHEDULER = "scheduler.manage"

# 权限矩阵（在 auth/rbac.py 实现，此处为定义）
ROLE_PERMISSIONS: Dict[UserRole, List[Permission]] = {
    UserRole.VIEWER: [
        Permission.READ_SNAPSHOTS, Permission.READ_REVIEWS,
        Permission.READ_PROPOSALS, Permission.READ_SETTINGS,
    ],
    UserRole.REVIEWER: [
        Permission.READ_SNAPSHOTS, Permission.READ_REVIEWS,
        Permission.READ_PROPOSALS, Permission.READ_SETTINGS,
        Permission.SUBMIT_REVIEW, Permission.PROPOSE_ADJUSTMENT,
        Permission.ACK_ALERT, Permission.TRIGGER_ANALYSIS,
        Permission.RUN_WHATIF, Permission.CHAT_WITH_AGENT,
    ],
    UserRole.SENIOR_REVIEWER: [
        # ... Reviewer 全部 +
        Permission.ACCEPT_PROPOSAL, Permission.REJECT_PROPOSAL,
    ],
    UserRole.OPERATOR: [
        # ... Reviewer 全部 +
        Permission.MANAGE_CONFIG, Permission.MANAGE_SCHEDULER,
    ],
    UserRole.ADMIN: [
        # 全部 +
        Permission.MANAGE_USERS,
    ],
    UserRole.AUDITOR: [
        # 只读，包括审计日志
        Permission.READ_SNAPSHOTS, Permission.READ_REVIEWS,
        Permission.READ_PROPOSALS, Permission.READ_SETTINGS,
        Permission.READ_AUDIT,
    ],
}

class AuditLog(BaseModel):
    id: UUID
    user_id: Optional[UUID] = None
    user_email: Optional[str] = None
    action: str
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    before_state: Optional[Dict[str, Any]] = None
    after_state: Optional[Dict[str, Any]] = None
    diff: Optional[Dict[str, Any]] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    request_id: Optional[str] = None
    api_endpoint: Optional[str] = None
    success: bool = True
    error_message: Optional[str] = None
    occurred_at: datetime

class ChatSession(BaseModel):
    id: UUID
    user_id: UUID
    snapshot_id: Optional[UUID] = None
    ipo_id: Optional[UUID] = None
    title: str
    created_at: datetime
    last_active_at: datetime
    archived: bool = False

class ChatMessage(BaseModel):
    id: UUID
    session_id: UUID
    role: Literal["user", "assistant", "system", "tool"]
    content: str
    content_json: Optional[Dict[str, Any]] = None
    citations: List[Citation] = Field(default_factory=list)
    tools_used: List[str] = Field(default_factory=list)
    cost_usd: Optional[Decimal] = None
    tokens_input: Optional[int] = None
    tokens_output: Optional[int] = None
    model_used: Optional[str] = None
    runtime_ms: Optional[int] = None
    sequence: int
    created_at: datetime

class WhatIfRequest(BaseModel):
    """What-If 估值请求"""
    snapshot_id: UUID
    modified_assumptions: Dict[str, Any]
    # 例如：{
    #   "comparable_pool": ["new_ticker_list"],
    #   "wacc": 0.12,
    #   "terminal_growth": 0.03,
    #   "revenue_cagr_y1_y3": 0.45,
    #   "steady_state_gross_margin": 0.55,
    # }

class WhatIfResponse(BaseModel):
    calculation_id: UUID
    original_distribution: ValuationDistribution
    new_distribution: ValuationDistribution
    delta_summary: Dict[str, float]  # 关键变化量化
    affected_models: List[str]
    cost_usd: Decimal
    runtime_ms: int

class RealtimeEventType(str, Enum):
    """SSE / WebSocket 事件类型"""
    # Alert 类
    ALERT_CREATED = "alert.created"
    ALERT_ACKNOWLEDGED = "alert.acknowledged"
    # Snapshot 类
    SNAPSHOT_CREATED = "snapshot.created"
    SNAPSHOT_UPDATED = "snapshot.updated"
    # Outcome 类
    OUTCOME_RECORDED = "outcome.recorded"
    CHECKPOINT_COMPLETED = "checkpoint.completed"
    # 状态机
    STATE_TRANSITION = "lifecycle.state_transition"
    # 调度器
    SCHEDULER_STARTED = "scheduler.started"
    SCHEDULER_COMPLETED = "scheduler.completed"
    SCHEDULER_FAILED = "scheduler.failed"
    # 学习闭环
    DRIFT_DETECTED = "drift.detected"
    PROPOSAL_CREATED = "proposal.created"
    PROPOSAL_ACCEPTED = "proposal.accepted"
    ADJUSTMENT_APPLIED = "adjustment.applied"
    # 系统
    DASHBOARD_REFRESH = "dashboard.refresh"
    DATA_SOURCE_DEGRADED = "datasource.degraded"
    COST_THRESHOLD_HIT = "cost.threshold_hit"

class RealtimeEvent(BaseModel):
    event_type: RealtimeEventType
    related_ipo_id: Optional[UUID] = None
    related_snapshot_id: Optional[UUID] = None
    payload: Dict[str, Any]
    created_at: datetime

class APIError(BaseModel):
    """RFC 7807 Problem Details 格式"""
    type: str  # URI ref
    title: str
    status: int
    detail: str
    instance: Optional[str] = None  # 请求 URI
    # 扩展字段
    request_id: Optional[str] = None
    validation_errors: Optional[List[Dict]] = None

class PaginationMeta(BaseModel):
    total: int
    limit: int
    offset: int
    has_next: bool

class PaginatedResponse(BaseModel):
    """所有列表 API 必须用此格式"""
    data: List[Any]
    meta: PaginationMeta

class DashboardSummary(BaseModel):
    """主控台数据汇总"""
    critical_alerts_count: int
    pending_reviews_count: int
    pending_proposals_count: int
    overdue_checkpoints_count: int
    active_snapshots: List[Dict]  # 简化版 snapshot 列表
    upcoming_events: List[Dict]
    system_health: Dict[str, str]
    cost_summary: Dict[str, Decimal]
```

---

## 7. Agent 设计规范

### 7.1 BaseAgent

```python
# src/hk_ipo_agent/agents/base.py
from abc import ABC, abstractmethod
from ..common.schemas import AgentOutput
from ..orchestrator.states import AnalysisState

class BaseAgent(ABC):
    role: AgentRole
    prompt_path: str  # 相对 prompts/ 路径
    model_key: str    # 在 llm_models.yaml 中的 key
    
    @abstractmethod
    async def run(self, state: AnalysisState) -> AgentOutput: ...
    
    async def _call_llm(self, rendered_prompt: str, **kwargs): ...
    
    def _validate_output(self, raw_output: dict) -> AgentOutput: ...
```

### 7.2 七大 Agent 概要

| Agent | 主要任务 | 必查数据 | 输出维度 |
|---|---|---|---|
| **Fundamental** | 业务实质、财务质量、治理 | 招股书 + iFind 财务异常因子 | business_quality, financial_health, governance |
| **Industry** | 可比公司池 + 行业地位 | iFind 同业 + 自建 comparable_pool | competitive_position, growth_outlook, comp_valuation |
| **Valuation** | 调 valuation/ 子模块 | extraction + market data | (调子模块) |
| **Policy** | 规则版本匹配、政策红利 | regulations/*.yaml | regime_fit, policy_tailwind |
| **Liquidity** | 流通盘、解禁压力、南向资金 | extraction + 历史 IPO | float_quality, lockup_risk, southbound_eligibility |
| **CornerstoneSignal** | 预测基石画像 + 保荐人信号 | 自建知识库 | sponsor_quality, predicted_cornerstone_strength |
| **Sentiment** | 同期热度、媒体情绪 | web search + 同期 IPO 表现 | market_temperature, narrative_risk |

每个 agent 的提示词必须强制 LLM 输出 JSON，并通过 Pydantic 严格校验。

---

## 8. LangGraph 编排细节

### 8.1 主图节点

```python
# src/hk_ipo_agent/orchestrator/graph.py 关键骨架
from langgraph.graph import StateGraph, START, END

def build_main_graph():
    g = StateGraph(AnalysisState)
    
    g.add_node("ingest", ingest_node)
    g.add_node("extract", extract_node)
    g.add_node("validate_extraction", validate_extraction_node)
    g.add_node("fundamental", fundamental_node)
    g.add_node("industry", industry_node)
    g.add_node("policy", policy_node)
    g.add_node("liquidity", liquidity_node)
    g.add_node("cornerstone_signal", cornerstone_signal_node)
    g.add_node("sentiment", sentiment_node)
    g.add_node("valuation", valuation_subgraph)  # 子图
    g.add_node("debate", debate_subgraph)         # 子图
    g.add_node("cross_check", cross_check_node)
    g.add_node("synthesize", synthesize_node)
    g.add_node("hitl", hitl_node)
    g.add_node("report", report_node)
    
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "extract")
    g.add_edge("extract", "validate_extraction")
    
    # validate 后条件分支
    g.add_conditional_edges(
        "validate_extraction",
        route_after_validation,  # 返回 "human_review" 或 "parallel_agents"
        {"human_review": "hitl", "parallel_agents": "fundamental"}
    )
    
    # 7 个 agent 并行 — LangGraph fanout
    for agent_node in ["fundamental", "industry", "policy", "liquidity",
                       "cornerstone_signal", "sentiment"]:
        g.add_edge("validate_extraction", agent_node)
        g.add_edge(agent_node, "valuation")  # 所有 agent 完成后进入 valuation
    
    g.add_edge("valuation", "debate")
    g.add_edge("debate", "cross_check")
    g.add_edge("cross_check", "synthesize")
    g.add_edge("synthesize", "hitl")
    g.add_edge("hitl", "report")
    g.add_edge("report", END)
    
    return g.compile(checkpointer=postgres_saver)
```

### 8.2 状态合并规则

并行 agent 输出必须使用 `Annotated[Dict, operator.update]` 或显式 reducer 合并，避免覆盖。

---

## 9. 测试规范

- **单元测试**：不打外部服务，LLM 调用全部 mock；coverage ≥ 80%
- **集成测试**：用 docker-compose 起 Postgres + Qdrant；可用真实小样本招股书
- **E2E 测试**：选 1-2 家已上市公司（晶泰控股 2228.HK 推荐），作为黄金回归
- 所有测试必须确定性（设置 seed、固定 mock 响应）

`tests/conftest.py` 必须提供 fixtures：
- `mock_llm_client`、`mock_ifind_client`
- `sample_prospectus_extraction`
- `db_session`（用 testcontainers 起 Postgres）
- `temp_qdrant`

---

## 10. 开发规范

### 10.1 代码风格
- ruff 严格模式（包括 ALL 规则，按需 ignore）
- mypy strict（禁止 `Any`，除非有 `# type: ignore` + 理由注释）
- 函数 ≤ 50 行；类 ≤ 300 行；文件 ≤ 500 行
- 所有 public 函数 / 方法必须有完整 docstring（Google style）
- 所有 LLM 调用必须 `async`

### 10.2 异步约束
- 所有 IO（DB、HTTP、LLM、向量库）必须 async
- 阻塞 CPU 任务用 `asyncio.to_thread` 或 ProcessPoolExecutor
- Agent 并行用 `asyncio.gather`

### 10.3 错误处理
- 自定义异常继承 `HkIpoAgentException`
- LLM 失败必须降级（Sonnet → Opus → human review）
- 外部 API 失败必须 retry + circuit breaker

### 10.4 日志
- structlog JSON 输出
- 每条日志必须含 `ipo_id`, `agent_role`（如适用）, `cost_usd`（LLM 调用）
- 敏感数据（API key）禁止入日志

### 10.5 Git 规范
- 分支：`feature/phase-N-<topic>`、`fix/<topic>`
- Commit：Conventional Commits（feat/fix/refactor/test/docs/chore）
- 每个 Phase 完成后打 tag `v0.N`
- PR 必须 ruff/mypy/pytest 全过才能 merge

### 10.6 安全
- 所有 API key 走 env vars，禁止入仓
- 招股书 PDF 默认 gitignore（用户数据）
- 数据库连接字符串必须用 env vars
- 提交前用 detect-secrets 扫描

---

## 11. CLAUDE.md 内容（Claude Code 工作准则）

新建 `CLAUDE.md` 时必须包含以下内容：

```markdown
# Claude Code 工作准则

## 启动检查
1. 优先读 PROJECT_SPEC.md，本文件是项目的权威规范
2. 读 docs/ARCHITECTURE.md 理解架构
3. 检查当前所在 Phase（看 git tag 或 CHANGELOG）

## 严格约束
- 严禁跨 Phase 工作。当前 Phase 完成才能进入下一个
- 严禁引入 PROJECT_SPEC.md §1 技术栈之外的核心依赖
- 严禁在代码中硬编码任何配置（必须走 config/）
- 严禁写非 async 的 IO 代码
- 严禁输出无 citation 的 Finding
- 严禁跳过测试。每个新模块必须配单元测试

## 决策原则
- 遇到歧义停下来问，不要猜
- 遇到 spec 没说的小决策，先做 ADR 草稿放 docs/decisions/
- 修改 schema 必须同步更新 Alembic migration

## 工作流
- 每个新功能：写 schema → 写测试 → 写实现 → 跑测试 → 跑 lint/typecheck → commit
- 每个 commit 前必须 `make lint && make typecheck && make test`
- 大改动前先在 docs/decisions/ 写 ADR

## 提示词约束
- 所有提示词必须放 prompts/，不准内嵌在 .py 文件
- 提示词文件必须含 frontmatter（role/version/last_updated/schemas）
- 修改提示词必须 bump version

## 数据安全
- 招股书 PDF 默认 gitignore
- 测试不准用真实公司全文，只能用 fixtures
- API key 必须走 env

## 性能要求
- 单次完整分析必须 ≤ 30 分钟
- 单 agent 调用 ≤ 5 分钟
- LLM 重试不超过 3 次

## 我应该问而不是猜的场景
1. 是否要新增第三方依赖
2. 是否要修改 schema
3. 是否要修改 Phase 顺序
4. 数据源访问失败时的 fallback 策略
5. 任何涉及"删除"或"重构"超过 1 个文件

## 预测生命周期约束（v1.1 新增 — 重要程度等同于严格约束）

- **任何完整分析必须先创建 snapshot 才能输出决策**。orchestrator 图必须保证 synthesize → create_snapshot → report 的顺序，create_snapshot 失败则整个流程失败
- **prediction_snapshots 表绝对不可变**。禁止用 ORM update / delete 此表。DB trigger 会拦截，应用层也不准实现 update_snapshot 接口
- **任何 config / prompt 修改必须走 learning_loop**：propose（写入 prediction_reviews）→ reviewer 人工 accept → applier 应用 + bump version → 触发小回测验证
- 绕过此流程的紧急修改必须在 `docs/decisions/` 写 ADR 说明原因，并打 hotfix tag
- **outcome / attribution / review 数据的添加必须经过对应 workflow**：禁止直接 INSERT 这些表
- **checkpoint 日期固定**：T+1, +5, +10, +22, +30, +60, +90, +126, +180, +252, +360（自上市日算起），不可调整。即使错过当天，必须用 close_price of that exact date 补跑
- **系统不得自动应用任何调整**。所有 adjustment 必须 reviewer 字段非空 + status=accepted 才能 apply
- **撤回 / 聆讯失败的 IPO 也要建立 snapshot**：避免 survivorship bias
- **基石减持检测的不确定性必须显式标注**：港交所披露易数据不完整时，不能假装确定，要标 `tracking_unreliable=true`

## 自动化与状态机约束（v1.2 新增）

- **IPO 状态机的合法转换严格执行**。VALID_TRANSITIONS 之外的转换必须抛 InvalidStateTransition，不能为了"方便测试"绕过
- **LISTED 状态必须经过三重验证**（HKEX 公告 + iFind 行情 + 股票代码激活）。任一缺失不得转 LISTED
- **状态机不得回退**。即使误判，纠正方式是新建一个"correction transition"记录到 audit log，而不是改 current_state
- **三层调度器各司其职**：
  - high_frequency 严禁做归因、回测等重活
  - daily 严禁实时处理事件（那是 event_driven 的事）
  - 重叠运行用 DB 级 advisory lock 拦截
- **代码映射的不确定性必须传递**：low confidence 映射必须 `requires_review=True` 并发警报，不能默默上链
- **超时不等于失败**。stale_detector 触发的是警报，不是自动转 WITHDRAWN。必须人工确认（公司可能很快重新递表）
- **财报口径差异不能静默处理**：earnings_comparator 前 3 次必须 `requires_human_review=True`
- **生产环境必须用 Airflow 或同等编排**。APScheduler 仅用于 dev/test，生产不接受
- **所有警报必须含 actionable_info 字段**。`message="Failed"` 这种不可接受，必须说"应该做什么"
- **调度器失败必须升级**：daily_scheduler 失败 6h 内未恢复 → critical alert
- **数据源失败的降级是有序的**：iFind 主路径 → 等待重试（最多 3 次）→ 转 manual_pending 状态 + warning 警报。禁止用估算值代替真实数据

## UI 集成约束（v1.2.1 新增）

- **后端是 UI 的权威**。所有业务逻辑、计算、验证都必须在后端实现，UI 只做展示和交互。任何让 UI 自己算 "结论"的设计都是错的
- **OpenAPI schema 必须自动生成且完整**。任何 API 变更必须同步更新 schema；UI 的 `openapi-typescript` CI 必须 diff 为空
- **API 错误必须用 RFC 7807 Problem Details 格式**。禁止 `{"error": "something failed"}` 这种自由格式
- **所有 write 操作必须自动写 audit_log**。通过 `audit_middleware.py` 实现，不依赖各 endpoint 自觉
- **RBAC 检查是 endpoint 强制项**。所有 endpoint（除 health）必须用 `require_role()` 或 `require_permission()` 装饰
- **CORS 白名单严格控制**。生产环境不允许 `*`，必须明确列出 UI 域名
- **SSE 事件类型必须在 `event_types.py` 中先定义**，再发送。禁止随手发未注册的事件类型
- **WebSocket chat 的所有消息必须持久化到 chat_messages 表**，否则用户切换设备就丢历史
- **What-If 计算结果必须持久化到 whatif_calculations**，便于事后归因和复现
- **API 响应必须包含 `X-Request-Id` header**，UI 用于错误追踪
- **不允许 UI 直接连接数据库或向量库**。一切通过 API
- **不允许 UI 调用 LLM**。一切 LLM 调用走后端，前端只接受流式响应
- **敏感字段必须脱敏**。API 返回的 user_email 在审计列表中应脱敏（除非调用方有 READ_AUDIT 权限）
- **分页必须用 `PaginatedResponse` 标准格式**。禁止各 endpoint 自定义分页字段
- **金额字段必须用 string 类型在 JSON 中传输**，避免 JavaScript 数字精度问题
- **日期时间统一用 ISO 8601 + timezone**（如 `2026-05-16T14:30:00+08:00`）
```

---

## 12. 关键风险与防范

| 风险 | 防范措施 |
|---|---|
| **数据泄漏（look-ahead bias）** | 所有数据访问必经 `AsOfDataProvider`；回测专门审计 |
| **18C 样本不足导致过拟合** | 叠加 18A、A股科创板硬科技作迁移；权重必须用 Bayesian 校准而非穷举 |
| **规则切换断点（2025-08-04）** | `regulations/*.yaml` 版本化；回测分段评估 |
| **LLM 幻觉** | 所有 Finding 强制 citation；Critic 层专门挑战 |
| **iFind 数据滞后** | HKEX 原始公告优先；iFind 为备份 |
| **成本失控** | 每次调用记 cost；agent 配置 max_cost；超阈值人工审批 |
| **招股书抽取错误** | extraction 必经 validator；置信度低时标 needs_human_review |
| **预测档案被篡改（v1.1）** | DB trigger 拦截 UPDATE/DELETE；input_data_hash 每次读取验证；审计日志记录所有访问 |
| **Survivorship bias（v1.1）** | 撤回 / 聆讯失败 IPO 也必须建 snapshot；DoD 检查 active_predictions 表覆盖率 |
| **Checkpoint 日期漂移（v1.1）** | 调度器幂等；checkpoint_day 在 schema 中是固定枚举；补跑必须用 exact date 价格 |
| **基石减持追踪不完整（v1.1）** | 多源交叉验证（披露易 + iFind 大宗交易 + 媒体）；不确定时显式标 `tracking_unreliable` |
| **反馈循环自激（v1.1）** | 所有 adjustment 必须人工批准；applier 检查 reviewer 非空；apply 后必须小回测验证不退步 |
| **学习样本量不足（v1.1）** | drift_detector 设最小样本量（建议 20+）；不足时只报警不提议；跨市场迁移样本明确标 source_market |
| **预测系统校准漂移未察觉（v1.1）** | `drift_detector` 每天扫描，触发 critical signal 必须 24h 内人工 review |
| **状态机误判 LISTED（v1.2）** | 必须三重验证（HKEX 公告 + iFind 行情 + 代码激活）；任一缺失阻止转换 |
| **公司代码映射错误（v1.2）** | 三策略 + 置信度评分；low/medium 必须人工确认；映射错误会污染所有 outcome 数据 |
| **调度器漂移 / 漏跑 checkpoint（v1.2）** | 幂等 + 补漏机制；daily_scheduler SLA 监控；失败 6h 内升级 critical alert |
| **HKEX 公告流断流（v1.2）** | RSS / 轮询双路径；断流 > 2h 触发 warning；> 6h 触发 critical |
| **iFind 数据延迟 / 失败（v1.2）** | 重试 + 降级到 manual_pending 状态 + 警报；禁止用估算值代替 |
| **财报口径差异静默忽略（v1.2）** | 前 3 次比对强制人工 review；mapping_rules.yaml 版本化；招股书"非IFRS adjusted"必须有显式映射 |
| **公司默默失效未识别（v1.2）** | stale_detector 强制超时警报；reviewer 24h 内必须确认 |
| **A+H 上市日错误（v1.2）** | ah_special.py 强制以 H 股首日为 checkpoint 起点；A 股期间数据仅作参考不计入 outcome |

---

## 13. 项目里程碑指标（Definition of Done for v1.2.1）

- [ ] 全部 11 个 Phase 完成（Phase 0-7 + 7.5 + 8 + 9 + 10）
- [ ] 在 50+ 历史 IPO 上回测，决策准确率 ≥ 70%（首日 + 6 个月综合）
- [ ] 端到端单次分析 ≤ 30 分钟
- [ ] 单次完整分析 LLM 成本 ≤ $5
- [ ] 测试覆盖率 ≥ 80%
- [ ] 3 个真实活跃 IPO 案例完成完整投决备忘录
- [ ] 文档完备（docs/ 全部章节填充，含 LEARNING_PROTOCOL.md、DEPLOYMENT.md）

**v1.1 部分：**
- [ ] `prediction_snapshots` 通过对抗测试（UPDATE/DELETE 被 DB 拦截、hash 验证生效）
- [ ] 至少 1 家已上市公司完成完整 lifecycle：snapshot → 全 11 个 checkpoint outcome → attribution → review
- [ ] 至少 1 轮完整 learning_loop 闭环：drift_detect → propose → review → apply → re-backtest 验证
- [ ] 撤回 / 失败 IPO 已纳入 registry（survivorship bias 防范）
- [ ] config_versions 表有完整历史，每个 snapshot 都可定位到当时的配置版本
- [ ] 月度学习报告（reports.py）可自动生成

**v1.2 部分（自动化与状态机）：**
- [ ] IPO 状态机仿真测试全过（正常上市 / 撤回 / 默默失效三种场景）
- [ ] code_mapper 在 30 家历史已上市公司上 high confidence 映射准确率 ≥ 95%
- [ ] earnings_comparator 在 5 家已上市公司上的比对结果与人工吻合
- [ ] 三层调度器对抗测试通过（幂等、并发锁、补漏）
- [ ] Airflow DAG 全部部署并通过 SLA 监控（daily 失败 6h 内升级）
- [ ] alerts 路由测试通过（info/warning/critical 三级路由到对应渠道）
- [ ] 至少 30 天连续运行无人工干预（仅响应警报），证明系统真正自主
- [ ] 一次仿真"用户失踪 90 天"：系统在 90 天内自动完成所有 active snapshot 的 checkpoint 追踪、警报投递，无任何数据丢失

**v1.2.1 部分（UI 集成支撑）：**
- [ ] OpenAPI 3.1 schema 完整且 UI 可消费（`openapi-typescript` 生成成功）
- [ ] §16.2 完整 API 清单全部实现并通过 OpenAPI 验证
- [ ] SSE 实时事件：所有 §16.3 定义的事件类型可正确推送 + 客户端可订阅
- [ ] WebSocket chat：流式响应 + 断线重连 + 消息持久化全部通过
- [ ] RBAC：6 个角色的权限矩阵实施 + 对抗测试（低权限访问高权限 endpoint 必须 403）
- [ ] 审计日志：所有 write 操作自动记录 + immutability trigger 测试通过
- [ ] What-If endpoint：能正确重算 + 不影响原 snapshot + 持久化到 whatif_calculations
- [ ] 招股书 PDF：在 PDF.js 中正确加载 + Range request 工作正常 + CORS 配置正确
- [ ] 速率限制：单用户超限返回 429 + cost guard 单日上限保护生效
- [ ] 错误格式：所有 4xx/5xx 用 RFC 7807 Problem Details
- [ ] 与 UI 项目 v1.3 完整集成测试通过（端到端的 review → proposal → apply 闭环）

---

## 14. 给 Claude Code 的最终指令

1. **首先**：创建 Phase 0 的全部目录和占位文件，跑通 `make install`。然后**停下来等我确认**。
2. **不要**自作主张跳过任何 Phase 或 deliverable。
3. **不要**在没有写测试的情况下提交代码。
4. **不要**修改本 `PROJECT_SPEC.md` 或 `CLAUDE.md` 的约束部分；可以追加 ADR 提议变更。
5. **遇到任何与本文件冲突的情况，停下来问我。**

---

## 15. 运行时部署要求（v1.2 新增）

**系统设计目标是自主运行，但前提是生产环境基础设施必须就位**。本节定义最低要求。

### 15.1 基础设施

| 组件 | dev/test | 生产 | 备注 |
|---|---|---|---|
| **数据库** | PostgreSQL 16（Docker） | PostgreSQL 16+（托管或 K8s） | 必须启用 logical replication 备份 |
| **向量库** | Qdrant Docker | Qdrant Cluster 或 Managed | 向量数据备份策略必须有 |
| **缓存** | Redis（Docker） | Redis Cluster 或 ElastiCache | 用于幂等锁 + LLM 响应缓存 |
| **任务编排** | APScheduler（in-process） | **Airflow 强制** | dev 可用 APScheduler，生产不接受 |
| **应用** | uvicorn 单进程 | gunicorn + 多 worker + K8s | 健康检查 endpoint 必须 |
| **监控** | structlog → stdout | Prometheus + Grafana + Loki | 关键 metric 见 §15.4 |
| **警报通道** | console | Slack + Email + PagerDuty | 按 level 路由 |

### 15.2 外部数据源授权

**这些必须在系统上线前确认就位**：

| 数据源 | 用途 | 关键约束 |
|---|---|---|
| **iFind Python SDK** | 财务、行情、IPO 数据 | 必须有长期授权（建议 ≥ 2 年）；QPS 限制必须在 `config/data_sources.yaml` 配清楚；备用账号必须有（主账号挂时切换） |
| **HKEXnews RSS / API** | 公告流监听 | 公开但有限速；建议本地缓存最近 90 天公告 |
| **披露易（DI）** | 持股变动、基石追踪 | HTML 爬虫，必须 respect robots.txt |
| **LlamaParse API** | 招股书解析 | 付费；额度监控 + 月度报告 |
| **Anthropic API** | 所有 LLM 调用 | 必须企业账号；月度成本告警阈值必须配 |
| **新闻聚合（可选）** | sentiment + event detection | 选 NewsAPI / Tushare news / 自建爬虫 |

### 15.3 调度器配置（生产）

```yaml
# config/schedulers.yaml 必须含
high_frequency:
  cron: "*/20 * * * *"        # 每 20 分钟
  timeout_seconds: 600         # 10 min 超时
  retry_max: 2
  alert_on_failure_count: 3    # 连续 3 次失败升级 critical
  lock_key: "scheduler:high_freq"

daily:
  cron: "0 3 * * *"            # 每天凌晨 3:00 HKT
  timeout_seconds: 7200        # 2h 超时
  retry_max: 1
  sla_minutes: 360             # 必须 6h 内完成
  lock_key: "scheduler:daily"

event_driven:
  webhook_port: 8081
  poll_fallback_seconds: 300   # webhook 失效时降级到轮询
  
monthly_learning:
  cron: "0 4 1 * *"            # 每月 1 日凌晨 4:00
  timeout_seconds: 14400       # 4h
```

### 15.4 关键监控 metrics

必须输出到 Prometheus（或同等）：

```
# 调度器健康
hk_ipo_scheduler_runs_total{type, status}
hk_ipo_scheduler_run_duration_seconds{type}
hk_ipo_scheduler_last_success_timestamp{type}

# 业务指标
hk_ipo_active_snapshots_count{state}
hk_ipo_checkpoints_processed_total{checkpoint_day, status}
hk_ipo_alerts_total{level, category}
hk_ipo_alerts_unacked{level}                # critical 未确认必须告警
hk_ipo_state_transitions_total{from, to}

# 数据源健康
hk_ipo_ifind_request_duration_seconds{endpoint}
hk_ipo_ifind_failures_total{endpoint, reason}
hk_ipo_hkex_announcements_received_total

# LLM 成本
hk_ipo_llm_cost_usd_total{agent, model}
hk_ipo_llm_request_duration_seconds{agent, model}
hk_ipo_llm_token_usage_total{agent, model, direction}
```

### 15.5 灾难恢复

- **数据库**：PITR（Point-In-Time Recovery）必须配；每日全备 + 每小时增量
- **prediction_snapshots 特别保护**：除主备份外，每周导出 cold storage（S3 Glacier 或同等）
- **向量库**：每周快照
- **配置 / 提示词**：所有 yaml / md 必须 git 版本化，git remote 独立备份
- **RTO**：4 小时；**RPO**：1 小时

### 15.6 上线 checklist（Production Go-Live）

- [ ] 所有 Phase 0-10 完成
- [ ] 全部 §15.1 基础设施就位
- [ ] 全部 §15.2 数据源授权确认
- [ ] §15.4 Prometheus metrics 全部暴露并接入 Grafana
- [ ] §15.5 备份策略实施并完成首次演练（恢复测试）
- [ ] 警报路由测试：故意触发 critical alert，确认渠道收到
- [ ] 调度器 SLA 配置生效
- [ ] 至少 1 周影子运行（系统跑但不发警报、不接受决策）
- [ ] 1 个真实新 IPO 完成端到端流程并人工 review 输出质量
- [ ] 文档完备（DEPLOYMENT.md、RUNBOOK.md、ON_CALL.md）
- [ ] 至少 2 人能独立 on-call

### 15.7 运行时人工角色定义

系统设计目标：**用户失踪 30 天系统照常运转**。但生产环境需要明确人工角色：

| 角色 | 职责 | 期望响应时间 |
|---|---|---|
| **Reviewer**（投资分析师） | 接受 / 拒绝 learning_loop 提议；checkpoint review；critical alert 响应 | warning ≤ 24h, critical ≤ 4h |
| **Operator**（系统运维） | 调度器健康、数据源连通、警报渠道 | critical ≤ 1h |
| **PM** | 月度学习报告 review；季度系统评估 | 月度 |

绝不允许的设计模式：
- 任何"用户每天必须做某事，否则数据丢失"的设计
- 任何"用户每周必须导出某文件"的设计
- 任何"用户必须定期搜索互联网然后告诉系统"的设计
- 任何依赖人工进度推动的关键工作流

---

## 16. UI 集成要求（v1.2.1 新增）

**本节为后端提供给 UI 的完整接口契约**。配套姊妹文档 `PROJECT_SPEC_UI.md` (v1.3) 描述前端实施。后端 Phase 7 必须完成本节所有 deliverables。

### 16.1 API 设计原则

1. **REST 风格**，资源导向。资源用复数名词（`/snapshots` 而非 `/snapshot`）
2. **版本化**：所有业务 endpoint 在 `/api/v1/*` 下，未来不兼容变更走 `/api/v2/*`
3. **JSON 字段命名**：snake_case（与 Python 一致），与 TypeScript camelCase 的转换在 UI 端做
4. **URL 路径**：kebab-case
5. **HTTP 方法语义严格**：GET 只读 / POST 创建 / PUT 全量替换 / PATCH 部分更新 / DELETE 删除（但本系统几乎没有 DELETE）
6. **状态码**：200 / 201 创建 / 204 无内容 / 400 客户端错误 / 401 未认证 / 403 无权限 / 404 / 409 冲突 / 422 验证失败 / 429 限流 / 500 / 503
7. **请求 ID**：每个请求都有 `X-Request-Id` header，后端日志 + audit log 都用此 ID 关联
8. **OpenAPI 3.1**：完整 schema 在 `/openapi.json`，UI 自动消费

### 16.2 完整 API 清单

**认证**
```
GET    /api/v1/auth/me                              # 当前用户 + roles + permissions
POST   /api/v1/auth/logout
POST   /api/v1/auth/sso/initiate                    # 启动 SSO 登录
GET    /api/v1/auth/sso/callback                    # SSO 回调
```

**主控台**
```
GET    /api/v1/dashboard/summary                    # 主控台数据汇总
```

**IPO**
```
GET    /api/v1/ipos                                 # 列表 + 筛选 + 分页
GET    /api/v1/ipos/{ipo_id}                        # 详情
GET    /api/v1/ipos/{ipo_id}/snapshots              # 该 IPO 所有快照
GET    /api/v1/ipos/{ipo_id}/lifecycle              # 状态机历史
GET    /api/v1/ipos/{ipo_id}/outcomes               # 所有 checkpoint outcome
GET    /api/v1/ipos/{ipo_id}/events                 # 关键事件
GET    /api/v1/ipos/{ipo_id}/comparables            # 可比公司池
POST   /api/v1/ipos/{ipo_id}/reanalyze              # 触发重新分析（异步）
```

**快照**
```
GET    /api/v1/snapshots/{snapshot_id}              # 完整快照
GET    /api/v1/snapshots/{snapshot_id}/agent-outputs # 单独取 agent 输出
GET    /api/v1/snapshots/{snapshot_id}/valuation    # 估值结果
GET    /api/v1/snapshots/{snapshot_id}/debate       # 辩论记录
GET    /api/v1/snapshots/{snapshot_id}/audit        # 该 snapshot 相关审计
```

**招股书**
```
GET    /api/v1/prospectus/{prospectus_id}/extraction        # 抽取结果 JSON
GET    /api/v1/prospectus/{prospectus_id}/pdf               # PDF 文件流（支持 Range）
GET    /api/v1/prospectus/{prospectus_id}/citation/{cid}    # 单条引用原文 + 上下文
GET    /api/v1/prospectus/{prospectus_id}/page/{page_num}   # 单页文本
POST   /api/v1/prospectus/{prospectus_id}/search            # 全文检索
```

**分析触发**
```
POST   /api/v1/analysis                             # 启动完整分析（异步，返回 job_id）
GET    /api/v1/analysis/{job_id}                    # 查询分析任务状态
DELETE /api/v1/analysis/{job_id}                    # 取消任务
```

**Reviews**
```
GET    /api/v1/reviews                              # 列表（filter by status）
GET    /api/v1/reviews/{review_id}                  # 详情 + auto-generated draft
PATCH  /api/v1/reviews/{review_id}/draft            # 保存草稿（部分更新）
POST   /api/v1/reviews/{review_id}/submit           # 提交（写入 prediction_reviews）
```

**Proposals**
```
GET    /api/v1/proposals                            # 列表（filter by status）
GET    /api/v1/proposals/{proposal_id}              # 详情
POST   /api/v1/proposals/{proposal_id}/simulate     # 在历史样本上模拟
POST   /api/v1/proposals/{proposal_id}/decision     # accept/reject/edit
GET    /api/v1/proposals/{proposal_id}/diff         # 完整 diff 内容
```

**Drift**
```
GET    /api/v1/drift/signals                        # 当前活跃 drift signals
GET    /api/v1/drift/timeseries                     # 时序数据（metric + window）
GET    /api/v1/drift/attribution                    # 跨样本归因汇总
GET    /api/v1/drift/history                        # 历史调整应用效果
```

**Alerts**
```
GET    /api/v1/alerts                               # 列表
GET    /api/v1/alerts/{alert_id}
POST   /api/v1/alerts/{alert_id}/acknowledge
POST   /api/v1/alerts/{alert_id}/snooze             # body: {duration_hours}
POST   /api/v1/alerts/{alert_id}/escalate           # body: {target_user_id, message}
```

**Chat**
```
POST   /api/v1/chat/sessions                        # 新建会话
GET    /api/v1/chat/sessions                        # 当前用户的会话列表
GET    /api/v1/chat/sessions/{session_id}
GET    /api/v1/chat/sessions/{session_id}/messages
PATCH  /api/v1/chat/sessions/{session_id}           # 重命名 / 归档
WS     /api/ws/chat/{session_id}                    # 流式对话（见 §16.4）
```

**What-If**
```
POST   /api/v1/whatif/valuation                     # 重新估值
POST   /api/v1/whatif/comparable                    # 修改可比池
GET    /api/v1/whatif/calculations/{calc_id}        # 历史 what-if 结果
GET    /api/v1/whatif/calculations                  # 当前用户的历史 what-if
```

**Backtest**
```
GET    /api/v1/backtest/runs                        # 回测运行列表
GET    /api/v1/backtest/runs/{run_id}               # 单次回测结果
POST   /api/v1/backtest/runs                        # 启动新回测（admin 权限）
GET    /api/v1/backtest/runs/{run_id}/samples       # 该回测的样本结果
```

**System**
```
GET    /api/v1/system/health                        # 整体健康
GET    /api/v1/system/schedulers                    # 调度器状态
GET    /api/v1/system/data-sources                  # 数据源连通状态
GET    /api/v1/system/costs                         # 成本汇总（period 查询参数）
GET    /api/v1/system/metrics                       # Prometheus 兼容 metrics
```

**Settings**
```
GET    /api/v1/settings/configs                     # 列出所有 YAML 配置
GET    /api/v1/settings/configs/{path:path}         # 单个文件内容
GET    /api/v1/settings/configs/{path:path}/history # 该文件的版本历史
GET    /api/v1/settings/prompts                     # 提示词列表
GET    /api/v1/settings/prompts/{prompt_id}
# 注意：修改 config / prompts 必须走 proposals 流程，没有直接 PUT/PATCH
```

**Audit**
```
GET    /api/v1/audit/logs                           # 全局审计日志（带 filter）
GET    /api/v1/audit/logs/{log_id}                  # 单条详情
GET    /api/v1/audit/logs/export                    # 导出 CSV（auditor 权限）
```

**Users（admin only）**
```
GET    /api/v1/users
GET    /api/v1/users/{user_id}
POST   /api/v1/users
PATCH  /api/v1/users/{user_id}/roles                # 修改角色
POST   /api/v1/users/{user_id}/deactivate
```

**实时**
```
GET    /api/stream/events                           # SSE 实时事件（见 §16.3）
WS     /api/ws/chat/{session_id}                    # WebSocket 聊天（见 §16.4）
```

**健康检查（无认证）**
```
GET    /health                                      # 简单存活
GET    /ready                                       # 含依赖服务（DB/Qdrant/Redis）
GET    /metrics                                     # Prometheus
```

### 16.3 SSE 实时事件协议

**Endpoint**: `GET /api/stream/events`

**协议**:
- Content-Type: `text/event-stream`
- 必须含 `Cache-Control: no-cache`
- 客户端用浏览器原生 `EventSource` 订阅
- 自动重连（`retry: 5000` 字段）

**消息格式**:
```
event: alert.created
data: {"event_type": "alert.created", "related_ipo_id": "...", "payload": {...}, "created_at": "..."}
id: 12345

event: snapshot.updated
data: {...}
id: 12346

: heartbeat
```

**事件类型完整清单**（必须与 `RealtimeEventType` enum 一致）:

| 事件类型 | 触发时机 | 负载关键字段 |
|---|---|---|
| `alert.created` | 新警报产生 | alert_id, level, category, message, actionable_info |
| `alert.acknowledged` | 警报被确认 | alert_id, acknowledged_by |
| `snapshot.created` | 新快照创建 | snapshot_id, ipo_id |
| `snapshot.updated` | 快照状态变化 | snapshot_id, change_type |
| `outcome.recorded` | T+N outcome 写入 | snapshot_id, checkpoint_day |
| `checkpoint.completed` | 完整 checkpoint 流程完成 | snapshot_id, checkpoint_day, return_pct |
| `lifecycle.state_transition` | IPO 状态机转换 | ipo_id, from_state, to_state |
| `scheduler.started` | 调度器开始 | scheduler_type, run_id |
| `scheduler.completed` | 调度器完成 | scheduler_type, run_id, stats |
| `scheduler.failed` | 调度器失败 | scheduler_type, run_id, error |
| `drift.detected` | 检测到 drift | signal_type, severity |
| `proposal.created` | 新调整提议 | proposal_id, target |
| `proposal.accepted` | 提议被接受 | proposal_id, accepted_by |
| `adjustment.applied` | 调整已应用 | proposal_id, applied_version |
| `dashboard.refresh` | 主控台需要刷新 | reason |
| `datasource.degraded` | 数据源降级 | source, severity |
| `cost.threshold_hit` | 成本达阈值 | threshold_type, current_value |

**实现细节**:
- 用 Redis pub/sub 作为内部事件总线
- 服务端按 user 权限过滤事件（user 只能收到自己有权限看的资源相关事件）
- 心跳：每 15s 一次 `: heartbeat` 注释行
- 客户端断线重连用 `Last-Event-ID` header 实现增量

### 16.4 WebSocket 聊天协议

**Endpoint**: `WS /api/ws/chat/{session_id}`

**握手认证**: 通过 query param `?token=<jwt>` 或子协议 `Sec-WebSocket-Protocol: bearer.<jwt>`

**消息类型**（双向）:

```json
// 客户端 → 服务端
{"type": "user_message", "content": "为什么 Bull Agent 给的估值这么高？"}
{"type": "stop", "reason": "user_cancelled"}
{"type": "ping"}

// 服务端 → 客户端（流式）
{"type": "message_start", "message_id": "..."}
{"type": "content_block_start", "block_type": "text"}
{"type": "content_delta", "delta": "因为该 agent..."}
{"type": "content_block_stop"}
{"type": "tool_use_start", "tool_name": "get_ifind_data", "tool_input": {...}}
{"type": "tool_use_result", "tool_name": "get_ifind_data", "result": {...}}
{"type": "citation", "citation": {"page": 142, "section": "...", "text": "..."}}
{"type": "message_stop", "stop_reason": "end_turn", "cost_usd": 0.023, "tokens": {...}}
{"type": "error", "error": {...}}
{"type": "pong"}
```

**断线处理**:
- 服务端必须把每条消息（含工具调用、引用）持久化到 chat_messages 表
- 客户端重连后通过 GET /api/v1/chat/sessions/{id}/messages 恢复历史
- 进行中的消息中断 → 服务端记录 partial content 并标记为 incomplete

### 16.5 认证与 RBAC

**认证方式**:
- 主要：SSO（SAML 2.0 / OIDC）— Okta、Azure AD、Google Workspace
- 次要：本地账号（仅 dev/test）
- 不允许：API key（除非未来加 service-to-service 场景）

**Session 管理**:
- 后端 issue JWT，访问 token 有效期 1h，refresh token 有效期 7d
- JWT 通过 `Authorization: Bearer <token>` 传递
- UI 端用 httpOnly + Secure cookie 存 refresh token（防 XSS）
- 强制 MFA：所有有 write 权限的角色（Reviewer 及以上）

**6 个角色**（详见 §6 `UserRole` enum + `ROLE_PERMISSIONS` 矩阵）:
- **Viewer**: 只读
- **Reviewer**: + 提交 review、propose adjustment、ack alert、触发分析、what-if、chat
- **Senior Reviewer (IC)**: + accept/reject proposal
- **Operator**: + 管理 config、调度器
- **Admin**: + 用户管理
- **Auditor**: 只读 + 审计日志读取

**Endpoint 权限装饰器**:
```python
@router.post("/proposals/{id}/decision")
async def decide_proposal(
    id: UUID,
    decision: ProposalDecision,
    user: User = Depends(require_permission(Permission.ACCEPT_PROPOSAL)),
):
    ...
```

权限检查失败 → 返回 403 + RFC 7807 错误格式 + 写 audit log。

### 16.6 CORS 配置

**生产环境严格白名单**（`config/cors.yaml`）:
```yaml
allowed_origins:
  - https://workbench.example.com
  - https://staging-workbench.example.com
allowed_methods: [GET, POST, PUT, PATCH, DELETE, OPTIONS]
allowed_headers: [Authorization, Content-Type, X-Request-Id]
expose_headers: [X-Request-Id, X-Total-Count]
allow_credentials: true
max_age: 3600
```

**招股书 PDF endpoint 特殊处理**:
- PDF.js 需要 `Range` request 支持
- 必须暴露 `Accept-Ranges`、`Content-Range`、`Content-Length`
- 单独的 CORS 配置允许 PDF.js worker 访问

### 16.7 速率限制

**默认限制**（按 user + endpoint pattern）:
- 普通查询: 60 req/min
- 触发分析: 5 req/min  
- What-If: 20 req/min
- Chat 消息: 30 req/min
- 批量导出: 5 req/min

**LLM 调用类 endpoint 额外保护**:
- `cost_guard` 中间件：单用户每天 LLM 成本上限（默认 $20，可在 settings 配）
- 达上限 → 429 + `Retry-After` header

**响应 headers**:
```
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 42
X-RateLimit-Reset: 1735603200
```

### 16.8 错误格式（RFC 7807 Problem Details）

**所有 4xx / 5xx 必须返回此格式**:
```json
{
  "type": "https://api.example.com/errors/validation-failed",
  "title": "Validation Failed",
  "status": 422,
  "detail": "The 'price_range_low' field must be a positive decimal",
  "instance": "/api/v1/reviews/abc-123/submit",
  "request_id": "req_xyz789",
  "validation_errors": [
    {"field": "price_range_low", "message": "must be positive"}
  ]
}
```

`Content-Type`: `application/problem+json`

### 16.9 What-If Endpoint 详细规范

**POST `/api/v1/whatif/valuation`**

请求:
```json
{
  "snapshot_id": "abc-...",
  "modified_assumptions": {
    "comparable_pool": ["MEMS.HK", "0700.HK"],
    "wacc": 0.12,
    "terminal_growth": 0.03,
    "revenue_cagr_y1_y3": 0.45,
    "steady_state_gross_margin": 0.55
  }
}
```

响应:
```json
{
  "calculation_id": "...",
  "original_distribution": {"p10": "...", "p50": "...", ...},
  "new_distribution": {"p10": "...", "p50": "...", ...},
  "delta_summary": {
    "median_change_pct": -8.5,
    "p10_change_pct": -12.3,
    "p90_change_pct": -5.1
  },
  "affected_models": ["comparable", "dcf"],
  "cost_usd": "0.045",
  "runtime_ms": 3200
}
```

**强制约束**:
- 不修改原 snapshot
- 必须持久化到 whatif_calculations 表
- 同一用户 + 同一 snapshot 的多次 what-if 可累积比较
- 不允许通过 what-if 修改决策；UI 必须明确显示"这是 what-if 模拟，不影响原决策"

### 16.10 招股书 PDF 服务

**GET `/api/v1/prospectus/{id}/pdf`**

- 必须支持 HTTP Range request（PDF.js 渐进加载需要）
- 必须返回 `Accept-Ranges: bytes`
- Content-Type: `application/pdf`
- 必须 inline disposition（`Content-Disposition: inline; filename="..."`），让浏览器在 PDF.js 中显示
- 缓存策略: `Cache-Control: private, max-age=3600`（用户级缓存，1h）
- 大文件流式传输（不一次性加载到内存）

**安全**:
- 用户必须有 READ_SNAPSHOTS 权限
- 访问必须写 audit log
- 防盗链：检查 Referer（生产环境）

### 16.11 性能 SLA

| Endpoint 类型 | P50 | P95 | P99 |
|---|---|---|---|
| 简单查询（dashboard summary、list） | 100ms | 300ms | 500ms |
| 详情查询（snapshot detail） | 200ms | 500ms | 1s |
| 招股书 PDF（首字节） | 200ms | 500ms | 1s |
| What-If 计算 | 2s | 5s | 10s |
| 触发分析（异步返回 job_id） | 50ms | 150ms | 300ms |
| Chat 流式响应（首 token） | 1s | 3s | 5s |
| SSE 事件投递延迟 | 100ms | 500ms | 1s |

### 16.12 API 安全要求

- 所有输入用 Pydantic 严格校验
- SQL 注入：用 SQLAlchemy ORM / 参数化查询，禁止字符串拼接
- XSS：API 响应不包含 HTML，UI 用 React 自动转义
- CSRF：JWT + Bearer header，不易 CSRF；但敏感操作（如修改用户角色）额外 CSRF token
- 敏感字段加密：DB 中 `sso_subject`、`api_keys`（如有）加密存储
- 日志脱敏：JWT、密码等不入日志
- 依赖扫描：`pip-audit` 在 CI 中强制运行
- API key（如果将来有 service token）轮换机制

### 16.13 文档要求

后端必须维护以下文档（在 `docs/` 目录）:
- `API_REFERENCE.md` — 完整 endpoint 清单 + 示例
- `RBAC.md` — 角色权限矩阵
- `SSE_PROTOCOL.md` — SSE 事件协议详细规范
- `WS_PROTOCOL.md` — WebSocket 聊天协议
- `AUTH_INTEGRATION.md` — SSO 集成指南
- `UI_INTEGRATION.md` — UI 接入的 onboarding 指南

---

*Version: 1.2.1*  
*Last Updated: 2026-05-16*  
*v1.1 Changes: 新增预测档案库 (prediction_registry) 和持续学习闭环 (learning_loop)，对应 Phase 7.5 和 Phase 10，配套新增 4 张数据库表 + 不可变快照 trigger + 学习生命周期约束*  
*v1.2 Changes: IPO 状态机 + 三层自动化调度器 + 公司代码映射 + 财报比对 + 终态处理 + 警报路由；新增 §15 运行时部署要求；新增 5 张数据库表；Phase 7.5 deliverables 大幅扩展；CLAUDE.md 新增自动化与状态机约束。核心理念：系统设计目标是用户失踪 30 天照常运转，任何"需要用户定期主动搜集反馈"的设计都是失败。*  
*v1.2.1 Changes: UI 集成支撑层。新增 §16 完整 UI 集成要求（REST API 清单、SSE 事件协议、WebSocket 聊天协议、RBAC 6 角色、CORS、速率限制、错误格式、What-If、招股书 PDF 服务）；新增 7 张数据库表（用户、角色、审计、聊天会话、聊天消息、what-if 计算、实时事件）；新增 15+ Pydantic 模型（含 RBAC 权限矩阵）；Phase 7 扩展为"API + 报告 + UI 集成层"；CLAUDE.md 新增 UI 集成约束。配套姊妹文档 PROJECT_SPEC_UI.md v1.3 描述前端实施。*
