# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
The project follows the Phase-based versioning of `PROJECT_SPEC.md` §4.

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
