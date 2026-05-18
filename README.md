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

**当前 release**：`v1.0.9`（post-v1.0 hardening — Phases R0–R9 完成）。原始 v1.0（10 Phase）于 2026-05-17 发布；自那以来共完成 9 个 hardening phase（约 90 个 Critical/Major 问题修复）。

### Phase 1 — 原始 v1.0（已完成）

| Phase | 内容 | 状态 |
|---|---|---|
| 0 | 项目骨架 + 工具链 | ✅ `v0.0` |
| 1 | 核心基础设施（schemas / LLM client / ORM） | ✅ `v0.1` |
| 2 | 数据层（iFind / HKEX / 知识库 + SQLite→PG 迁移） | ✅ `v0.2` |
| 3 | 招股书处理（LlamaParse + Qdrant） | ✅ `v0.3` |
| 4 | 估值模型层 | ✅ `v0.4` |
| 5 | 7 个专家 Agent | ✅ `v0.5` |
| 6 | 编排 + Critic + Synthesizer | ✅ `v0.6` |
| 7 | 报告 + API + UI 集成层（v1.2.1） | ✅ `v0.7` |
| 7.5 | 预测档案 + 生命周期追踪 + 学习闭环 | ✅ `v0.7.5` |
| 8 | 回测与校准 | ✅ `v0.8` |
| 9 | 端到端验证（5 case studies） | ✅ `v0.9` |
| 10 | 持续学习闭环（drift + propose + apply） | ✅ `v1.0` |

### Phase 2 — Post-v1.0 hardening（基于 8 个 review agent 报告，共 ~90 Critical/Major fix）

| Phase | 内容 | 状态 | tag |
|---|---|---|---|
| R0 | 工程基线 3 Blocker | ✅ | `v1.0.1-r0` |
| R1 | 数值正确性硬 bug（DCF 终值 / LlamaParse page / citation 兜底 / debate 早停） | ✅ | `v1.0.1` |
| R2 | 生产安全 + 不可变（HITL prod gate / snapshot immutable / state correction / audit user_id） | ✅ | `v1.0.2` |
| R3 | 流程伪完成治理（iFind stub / learning_cycle 4 extractors / calibration placebo / version_manager lock） | ✅ | `v1.0.3` |
| R4 | 模型路由 + Jinja2 + ADR（resolve_agent_model / Jinja2 prompt_renderer / inherited_inputs 校验） | ✅ | `v1.0.4` |
| R5 | async 一致性 + 类型 + ID（pdf_to_snapshot 异步 IO / UUID chunk_id / registry DI / Decimal JSON string） | ✅ | `v1.0.5` |
| R6 | RBAC + Auth 加固 + 脱敏（7 router require_permission / Argon2id / audit redaction / PG WS users） | ✅ | `v1.0.6` |
| R7 | 数据层修复（builders session DI / ContextVar engine / GIN aliases / IFindClient SecretStr） | ✅ | `v1.0.7` |
| R8 | 调度器 + 警报 + fixture fail-fast（regime raise / T+360 manual gate / fallback None / PGAlertStore / Airflow runners） | ✅ | `v1.0.8` |
| R9 | 测试缺口（+62 单测：pipelines / middleware / DCF pen-paper / state machine no-rewind / LlamaParse / slow marker） | ✅ | `v1.0.9` |
| R10 | scripts + docs + README | 🔄 进行中 | `v1.0.10` 预期 |
| R11 | 结构性收尾 | ⏸ | `v1.1.0` 预期 |

NACS v8 legacy 代码已归档到 [`legacy/`](legacy/)（Phase 9a + R7-7 完成）。新工具链不再扫描 legacy/。

详细修复路线图见 [docs/PLAN_post_v1.0.md](docs/PLAN_post_v1.0.md)。

---

## Quickstart — 完整前后端启动（dev）

> 前端是独立仓库：`../hk-ipo-cornerstone-ui/`（Next.js 16，pnpm 管理）。完整体验必须同时启动后端 + 前端。

### 0. 一次性环境准备

```bash
# uv (https://docs.astral.sh/uv/)
curl -LsSf https://astral.sh/uv/install.sh | sh        # macOS / Linux
# Windows: irm https://astral.sh/uv/install.ps1 | iex

# Node.js ≥ 20 + pnpm（前端用）
npm install -g pnpm

# Docker Desktop（postgres / qdrant / redis 容器，Windows/macOS 需手动启动 Docker Desktop 应用）
```

### 1. 安装依赖（后端 + 前端各一次）

```bash
# 后端（仓库根目录）
make install                                            # = uv sync

# 前端
cd ../hk-ipo-cornerstone-ui && pnpm install && cd -
```

### 2. 配置环境变量

```bash
cp .env.example .env                                    # 后端 — 填入 LLM / iFind / DB 密钥
cp ../hk-ipo-cornerstone-ui/.env.example \
   ../hk-ipo-cornerstone-ui/.env.local                  # 前端 — 默认指向 http://localhost:8000
```

### 3. 启动基础设施（postgres / qdrant / redis）

**重要**：仓库目录名含中文，docker compose 自动派生 project name 会失败，必须显式 `-p hkipo`。
`make db-up` 已硬编码 `-p hkipo`，直接用即可：

```bash
make db-up                                              # docker compose -p hkipo up -d
docker ps --filter "name=hkipo"                         # 三个容器都应 healthy
```

直接调 docker compose 时也务必带项目名：

```bash
docker compose -p hkipo up -d postgres qdrant redis
docker compose -p hkipo down                            # 收尾
```

### 4. 数据库迁移

```bash
make migrate                                            # alembic upgrade head
```

Windows / 中文路径用户用 Python 包装（自动注入 `PYTHONUTF8=1`，解决 alembic.ini 解析）：

```bash
uv run python scripts/dev.py migrate
```

### 5. 启动后端（终端 A）

```bash
make serve                                              # uvicorn @ :8000，热重载
# 或
uv run python scripts/dev.py serve
```

健康验证：

```bash
curl http://localhost:8000/health                       # {"status":"ok",...}
curl -o /dev/null -w "%{http_code}\n" http://localhost:8000/openapi.json
# Swagger UI:  http://localhost:8000/docs
# ReDoc:       http://localhost:8000/redoc
```

### 6. 同步前端 API types + 启动前端（终端 B）

**关键步骤**：前端的 TanStack Query 客户端依赖从 OpenAPI 自动生成的 TS 类型。每次后端 schema 变化必须重跑：

```bash
cd ../hk-ipo-cornerstone-ui
pnpm generate-api-types                                 # 拉 http://localhost:8000/openapi.json → src/lib/api/generated/schema.ts
pnpm dev                                                # Next.js @ :3000，Turbopack 热重载
```

打开 [http://localhost:3000](http://localhost:3000) 即可使用。

### 7. 验证骨架（CI 等价）

```bash
make lint                                               # ruff check
make typecheck                                          # mypy strict
make test                                               # pytest tests/unit
```

Windows / 中文路径等价命令：

```bash
uv run python scripts/dev.py lint
uv run python scripts/dev.py typecheck
uv run python scripts/dev.py test
uv run python scripts/dev.py help                       # 列出全部 target
```

`scripts/dev.py` 镜像 Makefile 的所有常用 target，且自动注入 `PYTHONUTF8=1`。

### 收尾

```bash
# 终端 A、B 各 Ctrl+C
make db-down                                            # 或 docker compose -p hkipo down
```

### 常见问题

| 症状 | 原因 / 解决 |
|---|---|
| `docker compose up` 报 `project name must not be empty` | 中文目录路径导致。用 `docker compose -p hkipo` 或 `make db-up` |
| `alembic upgrade head` 报 `Can't locate revision ...` | 本地分支与 DB 中记录的 alembic 版本不匹配。先 `git pull` 把缺失的 migration 文件取下来，再重跑；切勿手工改 `alembic_version` 表 |
| 前端 `pnpm generate-api-types` 连接失败 | 后端 (`make serve`) 必须先起，且监听 :8000 |
| 后端 `IFIND_USERNAME` / `LLAMA_CLOUD_API_KEY` 警告 | dev 可留空，仅在使用数据采集 / 招股书解析时必填 |

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
