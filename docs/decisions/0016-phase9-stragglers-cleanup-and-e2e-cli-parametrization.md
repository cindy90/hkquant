# ADR 0016: Phase 9a 补归档 + 参数化 e2e CLI 入口

- **Status**: Accepted
- **Date**: 2026-05-17
- **Deciders**: project lead
- **Supersedes**: 无
- **Superseded by**: 无
- **Related**: [ADR 0005](0005-nacs-legacy-asset-migration.md)（NACS 归档权威）、
  [ADR 0014](0014-phase9-scope-and-substages.md)（Phase 9a 主归档批次）、
  [ADR 0001](0001-use-langgraph.md)（LangGraph DSL 替代 YAML workflow）

## Context

Phase 9a 完成后（tag `v0.9`，2026-05-17），仓库 review 时发现 5 类
"漏归档/废弃 stub" 仍在仓库工作区里：

### 一、NACS 同源遗漏脚本（未被 git 追踪，但留在 `scripts/` 顶层 / 根目录）

| 路径 | 性质 | 跑不起来的根因 |
|---|---|---|
| `scripts/evaluate_new_ipo.py` | NACS 端到端"一键评估"入口（iFinD→SQLite→analyze_deal→报告） | `from data.deal_loader` / `data.dao` / `src/log.py` 均已 Phase 9a 归档；`subprocess` 调 `scripts/analyze_deal.py` 已不存在 |
| `scripts/search_yifei_tech.py` | 一次性 iFinD 探查脚本（确认翼菲智能股票存在） | `from src.data_sources.ifind` 已 Phase 9a 归档 |
| `scripts/check_data_completeness.py` | 对 `data/nacs_real.db` SQLite 做完整性校验 | 依赖已归档的 SQLite + `src/log.py` |
| `scripts/migrate_schema_pk_fix.py` | NACS SQLite 表 PK 重建迁移 | SQLite 已归档；新栈用 Alembic |
| `run_cv_backtest.py` | NACS v8 时序 CV 回测（与 `run_v7_backtest.py` 同源） | `from src.data_sources.ifind` 已 Phase 9a 归档；NACS scorer 已废 |
| `tests/test_cv_backtest.py` | 测 `run_cv_backtest.py` | 依赖同上 |

成因：ADR 0014 Phase 9a 归档了 spec §11 + ADR 0005 §Progress 明列的资产
（4 顶层脚本 + themes / nacs_real.db / configs / src 子目录），但**没扫描**
`scripts/` 下其它 NACS 同源脚本。本 ADR 把这批"漏网鱼"按 ADR 0005 同样
策略处置。

### 二、被 LangGraph 完全替代的 YAML workflow stub（被 git 追踪）

`workflows/{backtest,full_analysis,monitoring}.yaml` 三个文件内容均为：

```yaml
name: TBD
nodes: []
edges: []
```

Phase 0 占位时设想"用 YAML DSL 描述图"，实施时按 ADR 0001 直接用
LangGraph Python DSL（`src/hk_ipo_agent/orchestrator/graph.py:31`
`build_main_graph()`，578 行 orchestrator 包），YAML DSL 路线作废。

### 三、e2e CLI 入口尚未参数化（新需求，不属归档）

`scripts/run_e2e_test.py`（Phase 9b 产物）是真实端到端入口
（PDF → PyMuPDF → chunker → KIMI extractor → LangGraph → snapshot），
但 PDF 路径与 stock_code **硬编码**在文件头：

```python
PDF_PATH = _ROOT / "测试案例" / "...6871.HK-翼菲智能...pdf"
PROSPECTUS_ID = "6871-HK-yifei-zhilian"
IPO_ID = "6871.HK"
```

后果：每来一个新 IPO 案例，用户倾向于"复制一份脚本改路径"——这正是
`search_yifei_tech.py` / `evaluate_new_ipo.py` 当年产生的成因。如果不
在归档同时建立参数化 CLI，"NACS 同源遗漏"会以"Phase 10+ 遗漏"的形式
重新长出来。

## Decision

**本 ADR 一并处置以上 3 类，但仅前两类在本次执行；第三类落地为
独立后续任务（避免清理与新功能耦合 commit）。**

### 第一类：NACS 同源遗漏脚本

按 ADR 0005 §"Cleanup policy" 同样策略——**有审计/复用价值的归档，
纯一次性的删除**：

| 路径 | 处置 | 理由 |
|---|---|---|
| `scripts/evaluate_new_ipo.py` | `mv → legacy/scripts/` | 684 行，含 iFinD → YAML → deal_loader 完整 pattern，未来参数化 `scripts/run_e2e_test.py` 时可能想参考 iFinD 字段拼装逻辑 |
| `scripts/check_data_completeness.py` | `mv → legacy/scripts/` | 含 SQLite-vs-iFinD 完整性校验思路，Phase 10+ 数据质量审计可参考 |
| `scripts/migrate_schema_pk_fix.py` | `mv → legacy/scripts/` | 含 SQLite 无 PK 表去重模式，Alembic 同类问题可参考 |
| `run_cv_backtest.py` | `mv → legacy/` | NACS v8 CV 框架，与 `legacy/scripts/run_v7_backtest.py` 同源；归档以保持 Phase 8 baseline 可复现性 |
| `tests/test_cv_backtest.py` | `mv → legacy/tests/` | 跟着 `run_cv_backtest.py` 走 |
| `scripts/search_yifei_tech.py` | **`rm`** | 一次性探查脚本（确认翼菲智能在 iFinD 有记录），无审计价值；翼菲智能本身的端到端测试由 `scripts/run_e2e_test.py` + `tests/e2e/test_quantumpharm_case.py` 覆盖 |

**注意**：以上 6 个文件全部为**未追踪文件**（`git status` 显示 `??`），
所以执行的是 `mv` / `rm` 而非 `git mv` / `git rm`。归档后 `git add`
新位置。

### 第二类：作废的 YAML workflow stub

```bash
git rm -r workflows/
```

理由：
- 内容全为 stub，无运行时引用（grep 0 命中）
- ADR 0001 明确 orchestrator 用 LangGraph，已落地
- 保留会误导新读代码的人以为"workflow 配置在 yaml"

### 第二类附：磁盘垃圾清理（4 个文件）

同次清理一并 `rm` 以下 4 个文件（均未被 git 追踪，无审计损失）：

| 路径 | 性质 |
|---|---|
| `nul` (0 byte) | Windows 下误把 `> /dev/null` 写成 `> nul` 生成的空文件 |
| `_pdf_preview.txt` (4.7 KB) | 某次 PDF preview 的临时残留 |
| `nacs.db` (0 byte, root) | 根目录的 0 字节空 SQLite，疑似某脚本误创建（真实数据库在 `legacy/data/nacs_real.db`） |
| `data/data_quality_report.json` (2 KB) | `legacy/scripts/check_data_completeness.py` 的产物（仅 2 个 IPO 的样本输出，dev 实验残留） |

### 第三类：参数化 e2e CLI（**单独任务，本 ADR 仅登记**）

新任务：把 `scripts/run_e2e_test.py` 的硬编码常量改为 argparse，
重命名为 `scripts/analyze_pdf.py`，并保留 `tests/e2e/test_yifei_case.py`
作为"真实案例 e2e 测试（用翼菲智能 PDF）"——把当前 `run_e2e_test.py`
的硬编码路径锁在测试 fixture 里。

**任务规格**：

| 子项 | 说明 |
|---|---|
| 入口 | `scripts/analyze_pdf.py --pdf <path> --stock-code <code> [--listing-type ...] [--max-chunks N] [--dry-run]` |
| 复用 | `scripts/run_e2e_test.py` 现有的 parser → chunker → extractor → `build_main_graph()` 链路代码全部抽到 `src/hk_ipo_agent/pipelines/pdf_to_snapshot.py` |
| 退化 | 原 `scripts/run_e2e_test.py` 变成 `tests/e2e/test_yifei_case.py` 的 fixture-driven 测试 |
| 验收 | `uv run python scripts/analyze_pdf.py --pdf tests/fixtures/sample_prospectus.pdf --stock-code TEST.HK --dry-run` 成功输出 plan |
| 副产物 | `docs/USAGE.md` 增"如何分析一份新 PDF"段；CLAUDE.md 更新「Phase 启动前必读」表的 Phase 10 行 |

**触发时机**：作为 Phase 10 的前置任务（在持续学习闭环开工前），
预计 1-2 小时。

**为什么独立**：归档是机械操作（grep 引用 + mv），可独立验证；
参数化是新功能开发（需测试、文档），混在一起会污染 commit 历史
并把"消除一次性脚本"的代码意图淹没在 mv 噪声里。

## Consequences

### Positive

- **`scripts/` 顶层干净**：归档后只剩 spec 定义的入口（`run_analysis.py` /
  `run_e2e_test.py` / `run_backtest.py` / `ingest_prospectus.py` /
  `perf_smoke.py` 等）+ Phase 2/8 builder/fixer
- **`workflows/` 目录消除**：避免"YAML 配置图"的认知噪声
- **NACS 归档 ADR 链路闭环**：ADR 0005 §Progress 加补归档条目后，
  Phase 9 真正 100% 完成（Phase 9a 主归档 + 本 ADR 补归档）
- **参数化 CLI 任务正式登记**：未来用户处理新 IPO 走标准入口而非复制脚本，
  从源头堵住"一次性脚本积累"

### Negative

- **legacy/scripts/ 文件数 +5**：但 ADR 0005 已明示 Phase 10+ 可考虑
  `git filter-repo` 永久剥离；增量在可接受范围
- **ADR 0014 Phase 9a 看起来"没做完"**：实际是范围漏扫，本 ADR 补；
  ADR 0014 §Progress 已显示 `[x]`，不退回，仅在 §Progress 增"post-tag
  stragglers" 注脚指向本 ADR

### Neutral

- `pyproject.toml` ruff `extend-exclude` 顶层段同时移除 **5 行**已归档
  到 `legacy/scripts/` 的入口（`build_perf_cache.py` / `check_health.py` /
  `run_v7_backtest.py` / `nacs_checklist_tool.html` 是 Phase 9a 主归档时
  漏清的；`run_cv_backtest.py` 是本 ADR 新归档的）——它们移到 `legacy/`
  后均被 `extend-exclude` 的 `legacy` 条目自然 cover，顶层重复声明是
  历史残留
- workflows/ 删除不影响 `pyproject.toml`（YAML 本不在 ruff/mypy 范围）

## Progress

- [x] **现在**：本 ADR 0016 写就
- [ ] **第一类**：6 个 NACS 同源遗漏脚本归档/删除
- [ ] **第二类**：`workflows/` 整目录删除
- [ ] **第二类附**：磁盘垃圾 `nul` / `_pdf_preview.txt` / `nacs.db` /
  `data/data_quality_report.json` 删除（均未被 git 追踪）
- [ ] **pyproject.toml**：移除 ruff `extend-exclude` 顶层 5 条已归档
  入口（`build_perf_cache.py` / `check_health.py` / `run_v7_backtest.py` /
  `run_cv_backtest.py` / `nacs_checklist_tool.html`）— 都被 `legacy`
  条目自然 cover
- [ ] **ADR 0005 §Progress**：加 3 条 "Phase 9a (post-tag stragglers)" 补归档
- [ ] **CLAUDE.md**：Phase 9 行补脚注指向本 ADR
- [ ] **全仓 `make lint && make typecheck && make test`** 0 regression
- [x] **第三类（参数化 e2e CLI）**：实装 `src/hk_ipo_agent/pipelines/{__init__,pdf_to_snapshot}.py` 抽出 PDF → snapshot 5-step pipeline；`scripts/analyze_pdf.py` 参数化 CLI（`--pdf` / `--stock-code` / `--company-name` / `--listing-type` / `--industry-code` / `--max-pages` / `--max-chunks-per-section` / `--budget-usd` / `--regime-score` / `--no-report` / `--out-dir` / `--dry-run`）；原 `scripts/run_e2e_test.py` 退化为 `tests/e2e/test_yifei_case.py`（3 测试：classify_chunk / group_chunks / 翼菲 PDF parse+chunk mocked extract+graph 全 pipeline，自动 skip 当 PDF 不在）；`_write_detailed_report` + `_dump_full_state_json` 报告扩展。lint clean / 3 e2e passed / dry-run 验收 UTF-8 中文正常输出
