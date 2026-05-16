# HK IPO Cornerstone Investment Agent

港股 IPO 基石投资决策 Multi-Agent LLM 系统。在仅有招股说明书、尚无最终基石名单和招股价格的时间点，输出：

1. **是否参与基石投资**（参与 / 部分参与 / 不参与）
2. **合理价格区间** [P_low, P_fair, P_high]，附 6/12 个月收益分布
3. **关键监控触发器**（定价、基石披露、超额认购数据更新时重判断）
4. **可解释的投决备忘录 + 风险评分卡**
5. **预测后的全生命周期追踪**（T+1/+10/+30/+90/+180/+360 自动校验、归因、学习）
6. **完全自主的生命周期管理**（状态机 + 三层调度器，用户失踪 30 天系统照常运转）

权威规范：[PROJECT_SPEC.md](PROJECT_SPEC.md)（后端 v1.2.1）+ [PROJECT_SPEC_UI.md](PROJECT_SPEC_UI.md)（前端 v1.3）。

---

## Status

**当前阶段**：Phase 0 — 项目骨架。本仓库从 NACS v8（量化评分模型）原地重构为 spec v1.2.1 定义的多 Agent LLM 系统。

| Phase | 内容 | 状态 |
|---|---|---|
| 0 | 项目骨架 + 工具链 | **进行中** |
| 1 | 核心基础设施（schemas / LLM client / ORM） | 待启动 |
| 2 | 数据层（iFind / HKEX / 知识库 + SQLite→PG 迁移） | 待启动 |
| 3 | 招股书处理（LlamaParse + Qdrant） | 待启动 |
| 4 | 估值模型层 | 待启动 |
| 5 | 7 个专家 Agent | 待启动 |
| 6 | 编排 + Critic + Synthesizer | 待启动 |
| 7 | 报告 + API + UI 集成层（v1.2.1） | 待启动 |
| 7.5 | 预测档案 + 生命周期追踪 + 学习闭环 | 待启动 |
| 8 | 回测与校准 | 待启动 |
| 9 | 端到端验证 | 待启动 |
| 10 | 持续学习闭环 | 待启动 |

NACS v8 legacy 代码暂留原位（`src/nacs_model.py` 等），将在 Phase 2 数据 ETL 完成后归档。

---

## Quickstart (Phase 0)

### 1. 安装 uv 与 Docker

```bash
# uv (https://docs.astral.sh/uv/)
curl -LsSf https://astral.sh/uv/install.sh | sh   # macOS / Linux
# Windows: irm https://astral.sh/uv/install.ps1 | iex

# Docker Desktop（postgres / qdrant / redis 容器）
```

### 2. 安装依赖

```bash
make install         # = uv sync --all-extras
```

### 3. 起本地基础设施

```bash
cp .env.example .env
make db-up           # postgres + qdrant + redis
```

### 4. 验证骨架

```bash
make lint            # ruff check src/hk_ipo_agent + tests + scripts
make typecheck       # mypy strict on src/hk_ipo_agent
make test            # pytest tests/unit
make migrate         # alembic upgrade head
```

**Windows 用户**：GNU make 不随 Windows 默认安装。可通过 `choco install make` 安装，
或使用本仓库提供的等价 Python 包装：

```bash
uv run python scripts/dev.py lint
uv run python scripts/dev.py typecheck
uv run python scripts/dev.py test
uv run python scripts/dev.py migrate
uv run python scripts/dev.py help     # 列出全部 target
```

`scripts/dev.py` 镜像了 Makefile 的全部常用 target，且自动注入 `PYTHONUTF8=1`
（中文路径下 alembic.ini 解析需要）。

---

## Architecture (high level)

```
招股书 PDF + iFind/HKEX 数据
        │
        ▼
[ ingestion + extraction ] ────► Qdrant 向量库 + PostgreSQL 结构化抽取
        │
        ▼
[ LangGraph 主编排 ] —► 7 Agent 并行 (fundamental/industry/policy/liquidity/
        │                          cornerstone_signal/sentiment + valuation)
        ▼
[ valuation 子图 ] ──► comparable / DCF / AH premium / milestones / MC ensemble
        ▼
[ critic 子图 ] ────► Bull / Bear / Devils Advocate / Cross-checker
        ▼
[ Synthesizer (Opus) ] ──► FinalDecision + 价格区间 + scorecard
        ▼
[ Prediction Registry ] —► 不可变快照（DB trigger 强制）
        ▼
[ IPO 状态机 + 三层调度器 ] ──► T+1/+5/+10/+22/+30/+60/+90/+126/+180/+252/+360
                                自动 outcome + attribution + review draft
        ▼
[ Learning Loop ] ──► drift detection → propose adjustments → reviewer accepts
                      → adjustment_applier (+ small backtest verification)
```

详见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)（Phase 1 写）。

---

## Tech stack (locked by PROJECT_SPEC.md §1)

| 类别 | 选型 |
|---|---|
| 语言 / 包管理 | Python ≥3.11, uv |
| LLM 编排 | LangGraph ≥0.2 |
| LLM | Claude Sonnet 4 + Opus 4.7 (Synthesizer) |
| 数据校验 | Pydantic v2 |
| 数据库 / ORM | PostgreSQL 16 + SQLAlchemy 2.0 + Alembic |
| 向量库 | Qdrant |
| 嵌入模型 | BGE-large-zh-v1.5 (local) / Voyage-3 (prod) |
| PDF 解析 | LlamaParse 主 + PyMuPDF/Camelot 备 |
| Web 框架 | FastAPI |
| 测试 | pytest + pytest-asyncio |
| 代码质量 | ruff + mypy |
| 日志 | structlog (JSON) |
| 数据源 | iFind Python SDK (核心) + 自建 HKEX 爬虫 |

**禁止**：LangChain Agents / CrewAI / AutoGen。统一通过 LangGraph 编排。

---

## Documentation map

- [PROJECT_SPEC.md](PROJECT_SPEC.md) — 权威规范 v1.2.1
- [PROJECT_SPEC_UI.md](PROJECT_SPEC_UI.md) — 前端规范 v1.3（UI 独立项目消费）
- [CLAUDE.md](CLAUDE.md) — Claude Code 工作准则
- [docs/](docs/) — 架构 / Schema / Agent 设计 / API / RBAC / SSE / WS / 学习协议 / 部署
- [docs/decisions/](docs/decisions/) — ADR（架构决策记录）

---

## Contributing

每个 Phase 完成后必须停下来等用户确认，才能进入下一个 Phase。所有 commit 前必须 `make lint && make typecheck && make test`。
详见 [CLAUDE.md](CLAUDE.md) §严格约束 + §工作流。
