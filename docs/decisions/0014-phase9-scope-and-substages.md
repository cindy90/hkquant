# ADR 0014: Phase 9 范围 + 3 子阶段切片

- **Status**: Accepted
- **Date**: 2026-05-17
- **Deciders**: project lead
- **Supersedes**: 无
- **Superseded by**: 无

## Context

PROJECT_SPEC.md §4 Phase 9 deliverables + ADR 0005 §Progress（归档段）
列出端到端验证产物：

- **3-5 家已上市公司案例回测**：晶泰 (2228.HK) / 黑芝麻 / 越疆 / 宁德 H /
  地平线机器人；每家做完整 walk-forward 并文档化预测 vs 实际差异
- **性能压测**：每个 IPO 完整分析 ≤ 30 分钟（spec §13 SLO）
- **`tests/e2e/`** 端到端测试套件
- **NACS legacy 归档**（ADR 0005 §Progress 三项 Phase 9）：
  - `themes/` → `legacy/`
  - `data/nacs_real.db` → `legacy/`
  - 顶层脚本（`build_perf_cache.py` / `check_health.py` /
    `run_v7_backtest.py` / `nacs_checklist_tool.html`）→ `legacy/`

**外部依赖现状**：
- 真实招股书 PDF：默认 gitignore（用户数据），不存在仓库里
- iFind 凭证：用户提供，本地 dev 不一定有
- LLM 调用成本：每个完整分析 ~$5；5 个案例 ~$25
- 现有 `tests/e2e/test_quantumpharm_case.py` 是 stub

参考 ADR 0011 / 0012 / 0013 模式，本 ADR 锁定 **3 个子阶段**的切片，
保证每子阶段独立 commit + 测试 + 停下来等用户确认。

## Decision

**Phase 9 切成 3 子阶段 9a → 9b → 9c，每个子阶段独立 commit +
测试 + 停下来等确认；最后子阶段完结后打 tag `v0.9`。**

切片原则：
- **9a 先做无外部依赖的 NACS legacy 归档**：纯机械文件移动，
  可独立 review + 验证（grep 看引用 / 跑全仓单测）
- **9b 实装 FullPipelineScorer + e2e 测试骨架**：fixture-driven 端
  到端测试 + 30 分钟性能 SLO 探针 — 不调用真实 LLM 也能跑
- **9c 文档真实案例 stub + ADR 0005 收尾 + tag `v0.9`**：真实 5
  家案例的执行由用户提供 PDF + 凭证后单独跑（Phase 9 收尾后即可），
  本 ADR 仅保证骨架就位

---

### Phase 9a — NACS legacy 归档（~0.5 天）

**范围**：

| 子项 | 文件 / 模块 | 关键动作 |
|---|---|---|
| **themes/ 迁移** | `themes/` → `legacy/themes/` | 先跑 `theme_loader.py` 把 5 文件复制到 `data/knowledge_base/themes/`（如未完成）；然后整目录归档到 `legacy/themes/`；保留 `themes/README.md` 标记 archived |
| **SQLite 归档** | `data/nacs_real.db` + 4 个 `.bak_*` 备份 → `legacy/data/` | ETL 已完成（Phase 2 + 8d），SQLite 不再是 source of truth |
| **顶层脚本归档** | `build_perf_cache.py` / `check_health.py` / `run_v7_backtest.py` / `nacs_checklist_tool.html` → `legacy/scripts/` | 这些不再 lint，pyproject 已排除 |
| **旧 src/ 子目录** | `src/config.py` / `src/nacs_model.py` / `src/data/` / `src/data_sources/` → `legacy/src/` | spec 没明列但与 ADR 0005 精神一致 |
| **configs/ 旧目录** | `configs/` → `legacy/configs/` | 新 `config/`（单数）已是权威 |
| **legacy/ README** | `legacy/README.md` 新增 | 说明归档原因 + git 历史保留 + 何时清理 |

**DONE 条件**：
- `legacy/` 目录就位 + README 解释归档原因
- `themes/` / `configs/` / `src/{config,nacs_model,data,data_sources}` /
  4 顶层脚本 + `data/nacs_real.db*` 已移动
- 全仓单测 0 regression（确认无人引用归档物）
- ADR 0005 §Progress Phase 9 三项已勾
- `.gitignore` 更新（legacy/data/nacs_real.db 太大可考虑 LFS 或 ignore）

---

### Phase 9b — FullPipelineScorer + e2e 测试骨架（~1 天）

**范围**：

| 子项 | 文件 / 模块 | 关键功能 |
|---|---|---|
| **FullPipelineScorer** | `backtest/runner.py` 内新增 / 同级 `full_scorer.py` | 实装 `BacktestScorer` Protocol — 接 LangGraph orchestrator (`orchestrator/graph.py`) 跑真实 pipeline；输入 AsOfDataProvider，输出 `ScoreOutput`。**仍受 30 分钟 SLO 约束**；用 `asyncio.wait_for` 超时返回 SKIP + 警告 note |
| **e2e fixture pipeline** | `tests/e2e/test_full_pipeline_smoke.py` | 用 fixture ProspectusExtraction + MarketData + V8LiteScorer 跑完整 orchestrator → snapshot；验证 schema + 写入 prediction_snapshots + outcome 创建。**Mock LLM client，0 真实成本** |
| **晶泰 case stub** | `tests/e2e/test_quantumpharm_case.py` | 把 2228.HK 真实交易日期 + listing_type 灌入 fixture；用 `V8LiteScorer` 走 walk-forward；断言：as_of 严格小于 pricing_date，realized 60d return 来自实际 ipo_postmarket（已 ETL）。**不调用 LLM**，跑 < 5 秒 |
| **性能探针** | `scripts/perf_smoke.py` | 跑 1 IPO V8LiteScorer + persist_run_to_pg → 测时间；写 `reports/perf_smoke_*.md`；assert < 5s（V8Lite 远小于 30min SLO）|

**DONE 条件**：
- FullPipelineScorer 类存在 + 8 单测覆盖 happy / timeout / regime fail
- e2e fixture pipeline 跑通（≤ 5 秒，mock LLM）
- 晶泰 case 用真实 ipo_postmarket 数据跑通断言
- 性能探针 ≤ 5 秒；写 markdown
- 全仓单测 0 regression + 新增 ~10 单测 + 2 e2e

---

### Phase 9c — 真实 5 家案例 stub + ADR 0005 收尾 + tag v0.9（~0.5 天）

**范围**：

| 子项 | 文件 / 模块 | 关键动作 |
|---|---|---|
| **5 家案例文档** | `docs/case_studies/` 5 文件 stub | 每家公司一份 markdown：基本信息 + 上市日期 + listing_type + 实际 30/60/180/360 收益（来自 ipo_postmarket）+ V8Lite 预测结果。**不跑真实 LLM**；用户后续提供 PDF + 凭证再跑 FullPipelineScorer 补全 |
| **ADR 0005 收尾** | 3 个 Phase 9 条目 ✓ | 归档段全部 ticked |
| **CLAUDE.md 更新** | Phase 9 marked DONE | Phase 进度看板 ✓ |
| **CHANGELOG** | v0.9 entry | release notes |
| **Tag** | `v0.9` | `git tag -a v0.9` |

**DONE 条件**：
- 5 个 case study markdown 文件就位（结构一致）
- ADR 0005 §Progress 100% ✓
- CLAUDE.md Phase 9 marked DONE
- tag `v0.9` 打上

---

## Consequences

### Positive
- **每子阶段 ~0.5-1 天**，比 8c 短，节奏与 8d 类似
- **9a 是机械工作**：风险低，先做不打断思考链
- **9b 是核心技术产出**：FullPipelineScorer + e2e 骨架就位，未来真实
  case study 直接复用
- **9c 是文档收尾**：可独立 review，确保 ADR 0005 完结
- **真实 5 家案例的 LLM 执行由用户在自己环境跑**：成本可控，
  本仓库不入仓 PDF（spec §11 数据安全约束）

### Negative
- **5 家真实案例的"完整跑"延后到用户提供 PDF + 凭证**
  - **Mitigation**：9c case stub 已含真实 ipo_postmarket 数据，
    用户运行时是"补全"而非"从头建"
- **legacy/ 目录会让仓库根目录看起来杂乱**
  - **Mitigation**：README.md 标记 archived；Phase 10+ 可考虑
    git filter-repo 永久剥离
- **FullPipelineScorer 在 9b 只测 mock-LLM 路径**
  - **Mitigation**：真实 LLM 路径通过 Phase 5 + 6 + 7 的单测已经
    验证过；9b 关注的是 backtest harness 与 orchestrator 的接口

### Neutral
- 子阶段命名沿用 9a/b/c 而非 10.0/10.1
- Phase 10 持续学习闭环不被本 ADR 影响

## Progress

- [x] **现在**: 本 ADR 0014 写就
- [x] **Phase 9a (~0.5d)**: NACS legacy 归档（themes / data SQLite + 4 backups / 4 顶层脚本 / configs / src/{config,nacs_model,data,data_sources}）+ legacy/README.md + kb_tool / theme_loader / migrate_sqlite_to_pg / export_market_env_cache 路径更新 + 全仓 642 单测 0 regression + ADR 0005 §Progress Phase 9 三项 ✓
- [ ] **Phase 9b (~1d)**: FullPipelineScorer + e2e fixture pipeline + 晶泰 case + 性能探针 + ~10 新单测
- [ ] **Phase 9c (~0.5d)**: 5 家 case study 文档 + ADR 0005 收尾 + CLAUDE.md Phase 9 ✓ + tag `v0.9`
