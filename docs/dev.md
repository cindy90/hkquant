# NACS 开发者指南 (P1)

> 项目工程化约定 / 测试 / 代码质量门槛

---

## 1. 环境

```bash
pip install -r requirements.txt        # 含运行时 + dev 依赖
pre-commit install                     # 装 git hooks (一次性)
```

Python 版本: 3.10+ (实测 3.13 OK)。

---

## 2. 测试

测试套件在 `tests/`, 配置在 `pyproject.toml::[tool.pytest.ini_options]`。

```bash
# 全跑
python -m pytest

# 单文件 / 单测试
python -m pytest tests/test_nacs_model.py
python -m pytest tests/test_resolve.py::TestResolveStrategies::test_strategy4_token_jaccard_yuanxin

# 详细 + 短堆栈
python -m pytest -v --tb=short

# 覆盖率 (需 pip install pytest-cov)
python -m pytest --cov=src --cov-report=term-missing
```

**当前测试规模**: 87 个用例, ~5 秒, 100% 通过。

| 测试文件 | 覆盖范围 |
|---|---|
| `test_smoke.py` | 模块导入 + schema 初始化 |
| `test_config.py` | NacsConfig 加载/校验/默认值/单例 |
| `test_etl.py` | field_mappings 解析器 + load_to_db 幂等性 + load_delisted |
| `test_dao.py` | upsert + alias 解析 + as-of-date 边界 + universe 反幸存者 |
| `test_resolve.py` | normalize_cs_name + 5 阶段模糊匹配 |
| `test_nacs_model.py` | 5 个 T 用例 + position bands + cluster bonus |
| `test_parallel.py` | ProcessPoolExecutor: serial vs parallel 等价性 (需真实 db) |

---

## 3. pre-commit hooks

配置在 `.pre-commit-config.yaml`。包含:

| Hook | 作用 | stage |
|---|---|---|
| `trailing-whitespace` / `end-of-file-fixer` | 清理空白 | commit |
| `check-yaml` / `check-toml` | 配置文件语法检查 | commit |
| `check-added-large-files` (>2MB) | 防 SQLite/CSV 误入仓 | commit |
| `mixed-line-ending --fix=lf` | 统一 LF | commit |
| `ruff --fix` | lint + import sort + pyupgrade | commit |
| `black` | 代码格式化 | commit |
| `mypy` | 静态类型 | **manual** (默认不阻塞) |
| `nacs-config-validate` | configs/*.yaml 通过 NacsConfig 校验 | commit |

**典型工作流**:

```bash
# 装 hooks (clone 后一次性)
pre-commit install

# 正常开发: hooks 在 git commit 时自动跑
git add src/foo.py
git commit -m "..."   # 自动跑 ruff/black/yaml-check

# 想手动跑全部 (含 manual stage 的 mypy)
pre-commit run --all-files --hook-stage manual

# 紧急绕过 (慎用, 应仅用于"已知 hook 误报但 PR 已审")
git commit --no-verify -m "..."
```

**为什么 mypy 默认 manual**: 大型代码库初次跑 mypy 通常有几十到几百条遗留 issue, 强制 commit 会卡死。建议:
1. 单独跑 `pre-commit run mypy --all-files --hook-stage manual` 看清楚
2. 分模块清零 (按 src/data, src/config, src/nacs_model 优先级)
3. 全清零后改 `.pre-commit-config.yaml::stages` 为 `[pre-commit]`

---

## 4. 代码风格约定

定义在 `pyproject.toml`:

- **行长 100** (black + ruff 一致)
- **ruff lint rule**: E/F (pycodestyle/pyflakes) + I (isort) + UP (pyupgrade) + B (bugbear)
- **ignore**: E501 (行长由 black), B008 (dataclass 大量用函数默认值)
- **mypy**: ignore_missing_imports (允许第三方包无 stub)

`tests/*` 豁免 E402 (fixture 顺序导入)。

---

## 5. 数据字典

新增/修改 schema 必须同步更新 [`docs/data_dictionary.md`](data_dictionary.md)。流程见该文件附录 D。

---

## 6. 常用脚本

| 命令 | 用途 |
|---|---|
| `python check_health.py` | 老健康检查脚本 (兼容保留) |
| `python -m pytest` | 新 pytest 测试套件 (推荐) |
| `python -m src.data_sources.ifind.load_to_db --dry-run` | ETL 干跑 |
| `python -m src.data_sources.ifind.load_to_db --init-db` | 新建 DB 并灌库 |
| `python -m src.data_sources.ifind.load_to_db --tables delisted` | 仅刷新退市标记 (反幸存者偏差) |
| `python src/data_sources/ifind/delisted_pull.py` | 从 iFinD 拉退市港股 (本地客户端) |
| `python run_v7_backtest.py --config configs/nacs_v8.yaml` | 带配置回测 (串行) |
| `python run_v7_backtest.py --workers 4` | 4 进程并行回测 (ProcessPoolExecutor) |
| `python -c "from src.config import NacsConfig; print(NacsConfig().validate())"` | 配置自检 |

---

## 7. CI

GitHub Actions 配置在 `.github/workflows/ci.yml`, 触发条件:

- push 到 `main`/`master`/`develop`
- 任何针对上述分支的 PR
- 手动 (`workflow_dispatch`)

两个 job:

| Job | 内容 |
|---|---|
| `test` | matrix Python 3.11/3.12, pytest --cov, 上传 coverage.xml artifact |
| `lint` | `pre-commit run --all-files --show-diff-on-failure` |

并发策略: 同 ref 的 in-progress 任务被自动取消 (`concurrency.cancel-in-progress`)。

CI 上 `iFindPy` 不安装 (PyPI 无), 涉及 live 调用的测试自动 skip。

---

## 8. 反幸存者偏差 (P2-B)

`ipo_master.is_delisted/delisting_date/is_acquired` 是反幸存者偏差的核心字段, 任何回测必须先确认这些字段已刷新。

**数据流**:

```
iFinD「退市港股」block
    ↓ delisted_pull.py
data/raw/ifind/ifind_delisted_hk.csv  (4列: stock_code, delisting_date, delisting_reason, is_acquired)
    ↓ load_to_db.load_delisted()  (UPDATE ipo_master)
ipo_master.is_delisted = 1
    ↓ dao.list_ipos_in_universe_asof(asof)
回测在每个 asof 时点拿到"当时仍可观测的全集"
```

**关键查询** (在 src/data/dao.py):

```python
list_ipos_in_universe_asof(conn, asof) →
    SELECT ipo_id FROM ipo_master
    WHERE listing_date <= asof
      AND (is_delisted=0 OR delisting_date IS NULL OR delisting_date > asof)
```

⚠ `delisted_pull.py` 当前是骨架 (BLOCK_NAME 占位), 上线前需在 iFinD 数据浏览器人工核对真实板块名。

---

## 9. 回测并行化 (P2-C)

`run_v7_backtest.py` 支持 `concurrent.futures.ProcessPoolExecutor` 并行评分:

```bash
python run_v7_backtest.py --workers 1   # 串行 (默认)
python run_v7_backtest.py --workers 4   # 4 进程并行
```

**实现要点**:

- `score_one_ipo(args_tuple)` 是 module-level worker, 可 pickle
- 每个 worker 进程自开 sqlite conn (conn 不可跨进程共享)
- worker 重新加载 NacsConfig 单例 (从 `--config` 文件)
- chunksize = `len(ipo_ids) // (workers * 4)`, 减少 IPC 频次

**等价性保证**: `tests/test_parallel.py::test_serial_equals_parallel` 校验 serial vs workers=2 的 NACS/decision/position_pct 完全一致 (位元级)。

**何时不该开并行**:

- IPO < 100 只: spawn 开销 > 收益
- Windows 子进程启动慢, workers>=4 才显著加速
- 调试时用 workers=1 (异常 traceback 在主进程)

---

## 10. 数据质量与 schema 演进 (P3, migration v1)

### 10.1 raw + overrides 双层存储
原始 CSV 视为只读"原始档", 任何人工修正集中在 `data/raw/overrides.yaml`.
ETL 在 `read_csv_dict()` 之后调用 `apply_ipo_overrides()` 把覆盖项合并进 dict, 让
(raw + overrides) → DB 是确定性可重建过程.

```yaml
# data/raw/overrides.yaml
ipo_info:
  "2453.HK":
    listing_date: "2024-01-09"
    _reason: "raw CSV 是 typo (定价日 2023-12-29, 上市不可能在定价前 11 个月)"
    _source: "raw self-consistency: f034=2024-01-09"
```

可覆盖字段集合见 `src/data_sources/ifind/overrides.py::ALLOWED_IPO_FIELDS`.
新增覆盖时 `_reason` / `_source` 必填 (`lint_overrides()` 强制校验).

### 10.2 一次性迁移 `migrate_data_quality_v1.py`
`scripts/migrate_data_quality_v1.py` 把生产 DB 升级到 schema v1.
9 个步骤 (M0..M8) 都按 `db_metadata.migration_v1_M*` 标志做幂等控制.

```bash
# 干跑: 在 .dryrun.db 副本上演练, 完毕自动删除
python scripts/migrate_data_quality_v1.py --dry-run

# 真跑: 自动备份原 DB 到 nacs_real.db.bak_migrate_v1_<ts>
python scripts/migrate_data_quality_v1.py
```

| Step | 内容 |
|---|---|
| M0 | 合并 (ipo_id, cs_id) 重复行 (sum ticket / shares) |
| M1 | 补 `ipo_financials` / `ipo_concepts` / `ipo_industries` 表定义 |
| M2 | 加 5 个高频索引 (link 双向 + master.stock_code + financials.code+year) |
| M3 | `ipo_master.gross_proceeds_excl_greenshoe` 列 + 回填 (= price × shares) |
| M4 | `ipo_returns.is_d30_due / is_m6_due / is_m12_due / is_unlock_due` |
| M5 | `ipo_cornerstone_link.currency / ticket_size_native / fx_to_hkd` 归一 |
| M6 | 重建 link 表加 `CHECK (affiliation_flag IN (0, 1, 2))` |
| M7 | 缺失 share_capital 用 `actual_issued_shares` 反推 pre_ipo_shares |
| M8 | 创建 `mv_ipo_full` 视图 (探索/回测统一入口) |

### 10.3 `mv_ipo_full` 视图 — 探索的统一入口
所有探索 (`scripts/explore_*.py`) 和回测应优先 `SELECT * FROM mv_ipo_full`
而不是手写 ipo_master + cornerstone_link + ipo_returns 三连 join.
字段定义在一处, 加新列时只改 `schema.py` 的 VIEW DDL 一处.

### 10.4 业绩成熟标记
`ipo_returns.is_*_due` 区分"业绩还没到期"和"应该有业绩但缺数".

```sql
-- 错误用法: NULL 同时混了两种含义, 缺失率被高估
SELECT AVG(return_m6) FROM ipo_returns;

-- 正确: 只统计已到期样本
SELECT AVG(return_m6) FROM ipo_returns WHERE is_m6_due = 1;
```

`compute_ipo_returns()` 现在自动派生这 4 个标记.

### 10.5 货币归一
`ipo_cornerstone_link` 之前默认全 HKD, 但 raw CSV 里有 33 USD + 4 CNY 行.
现在 schema:

| 列 | 含义 |
|---|---|
| `currency` | HKD / USD / CNY |
| `ticket_size_native` | 招股书原文金额 |
| `fx_to_hkd` | 写入时锁定的换算率 |
| `ticket_size_hkd` | = `ticket_size_native × fx_to_hkd` (派生, 下游聚合用此值) |

汇率常量 (`FX_USD_HKD = 7.80`, `FX_CNY_HKD = 1.10`) 在
`load_to_db.py` + `migrate_data_quality_v1.py` 共享. 后续若改成按月查 fx,
两处一起改.

### 10.6 已知未填充表 / 未启用字段

| 项 | 状态 | 说明 |
|---|---|---|
| `price_history` | 0 行 | `ipo_returns` 已通过 `fix_p1_returns_via_ifind.py` 直接派生写入. **重跑 `dao.compute_ipo_returns()` 会清空 ipo_returns**. 长期方案: 拉日 K 入此表后改回派生路径. |
| `sponsor_performance_asof` | 0 行 | schema 预留, 与基石画像同思路按 as-of 物化, 待补 sponsor 派生脚本. |
| `ipo_master.last_round_premium` | 100% NULL | L1 否决条款 `last_round_premium > 0.50` 因此**未启用**. 数据补齐 (来自 wind/F&S 报告) 后自动生效. |
| `data/raw/ifind/ifind_blocks.csv.broken` | 损坏 | 18A/18C/AH/SPAC 章节自动校验当前不可用; 须重 pull, 见 `data/raw/ifind/README_blocks_broken.md`. |

---

## 11. Deal pipeline 与预测复盘 (P3, migration v2)

### 11.1 数据模型
聆讯通过的拟上市公司 = `ipo_master.status='prospectus'` 的行. 字段与 listed IPO
完全相同, 区别只在 status 反映数据完整度:

```
prospectus → pricing → listed → delisted / withdrawn
```

ETL (`load_ipo_info`) 自动按 `(listing_date, intl_oversub)` 推断 status:
- 未来 + 没 oversub → `prospectus`
- 未来 + 有 oversub → `pricing`
- 过去 → `listed`
- delisted CSV 命中 → `delisted` (load_delisted)

panel 探索查询时**务必加 `WHERE status='listed'`**, 否则 deal pipeline 数据
会污染 pe_peer_median / regime_score 等参考统计.

### 11.2 Deal YAML 输入
iFinD 没拉到的字段 (招股说明书细节、人工核实的基石名单) 走 `data/deals/<stock>.yaml`:

```bash
cp data/deals/TEMPLATE.yaml data/deals/1187.HK.yaml
# 编辑 YAML
python scripts/load_deal.py --file data/deals/1187.HK.yaml --dry-run    # lint
python scripts/load_deal.py --file data/deals/1187.HK.yaml              # 写库
```

`load_deal_dict` 是幂等的: 重复跑同一 YAML 会 update ipo_master + cornerstone_link.

### 11.3 Panel snapshot — 评估的"参考标杆"
每次 `python run_v7_backtest.py` 跑完, 自动写一行 `panel_snapshots`:

| 列 | 内容 |
|---|---|
| snapshot_id | `PANEL_<asof>_<cfg_hash[:6]>` |
| member_ipo_ids_json | 当时 panel 成员 (status='listed') 全集 |
| aggregates_json | 跨章节 / 跨 GICS 的中位/IQR (单 deal 评估时直接读) |
| market_env_json | 当时 MarketEnvironment 8 字段 |
| config_yaml_snapshot | 完整配置 YAML 嵌入 |
| code_git_sha | 当时 HEAD |

### 11.4 单 deal 评估: `scripts/analyze_deal.py`
```bash
# 单 deal
python scripts/analyze_deal.py --stock-code 1187.HK

# 区间扫描 (low/mid/high 各跑一次, 看 NACS 对定价敏感度)
python scripts/analyze_deal.py --stock-code 1187.HK --price-scan

# 多 deal 横评 (日常工作流)
python scripts/analyze_deal.py --stock-codes "1187.HK,2493.HK,3296.HK" --compare

# 持久化 audit trail
python scripts/analyze_deal.py --stock-code 1187.HK --persist --notes "路演 follow-up"
```

每次 `--persist` 写一行 `nacs_predictions`, 含:
- 完整 `inputs_json` (IPOOffering 当时的快照)
- L1 / L2 / L3 各子项 (`layer1_components_json` 等)
- 同伴比对 `similar_cases_json` (top-5 listed IPO 的实际表现)
- panel 上下文 `panel_snapshot_id` 引用

### 11.5 上市后复盘: `scripts/case_review.py`
```bash
python scripts/case_review.py --stock-code 1187.HK
```

输出:
- 该 stock 在 `nacs_predictions` 中的全部历史预测 (按 asof 升序)
- 跨次预测的 NACS / Q_company / Q_eco / R_lockup 标准差 (稳定性)
- 锁定预测 (最后一次) vs 当前 `ipo_returns` 的实际 (受 `is_*_due` 过滤)
- 当时 inputs vs 上市后 actual 字段差 (intl_oversub 估值偏差等)
- 当时给出的 similar_cases 实际表现 vs 这只本身的实际, 看模型类比是否准
- 业绩未到期的字段返回 `not yet due` 而非 NULL

### 11.6 完整工作流
```
data/deals/<stock>.yaml         (人工录入)
    ↓ scripts/load_deal.py
ipo_master(status='prospectus') + ipo_cornerstone_link
    ↓ scripts/analyze_deal.py --persist
nacs_predictions  +  panel_snapshots
    ↓ ... 几个月后实际上市 + ETL 自动 status: prospectus → pricing → listed ...
ipo_returns (随业绩到期逐步填) + ipo_returns.is_*_due 标志
    ↓ scripts/case_review.py
诊断报告: 预测 vs 实际, 哪里偏了, 为什么
```

### 11.7 HTML IC memo (`--html`)
所有 3 个 CLI 都支持 `--html <path>` 输出自包含 HTML, 可直接邮件分发或投委
会传阅:

```bash
# 单 deal IC memo
python scripts/analyze_deal.py --stock-code 1187.HK \
    --html outputs/memos/1187.html

# 多 deal 横评 (compare)
python scripts/analyze_deal.py --stock-codes "1187.HK,2493.HK,3296.HK" \
    --compare --html outputs/memos/compare.html

# 上市后复盘
python scripts/case_review.py --stock-code 1187.HK \
    --html outputs/memos/1187_review.html
```

特性:
- **单文件**: 整套 CSS 内嵌在 `<style>` 块, 无外部 CSS/JS/CDN 依赖, 双击即开
- **决策徽章**: FULL=绿 / LARGE=蓝 / TRIAL=橙 / RELATIONSHIP=琥珀 / SKIP=红
- **三因子柱**: Q_company / Q_ecosystem / R_lockup 可视化进度条 (R_lockup 用红色)
- **L1/L2/L3 子项**: `<details>` 折叠, 默认收起
- **Similar listed peers**: 收益正负自动着色 (`ret-pos` / `ret-neg` / `ret-pending`)
- **`--price-scan` 跨决策边界警告**: 如 low 给 LARGE 而 high 给 TRIAL, memo 顶部出黄色 warning
- **打印样式**: `@media print` 投委会包打印不变形, 折叠的子项打印时不显示
- **暗黑模式**: `@prefers-color-scheme: dark` 自动切换 (邮件客户端各异, 默认浅色)
- **审计快照**: Inputs snapshot (折叠) 含完整 IPOOffering JSON, 复盘可还原

实现: `src/reports/html_renderer.py` + `src/reports/templates/*.html.j2` (Jinja2),
样式: `src/reports/static/report.css` (~400 行原创, 无第三方框架).

### 11.8 Rationale + Thesis (memo "为什么这么打分")
HTML memo 现在不只是数字, 还有完整的"为什么":

**Decision rationale** (顶部, 默认展开): 把
`NACS_adj = Q_c × Q_e × (1 - R_l) × adjustments` 公式拆解, 显示当时三因子的
具体值, 每个 adjustment 的乘数和触发条件, 以及 NACS 落入哪个 band → 决策.

**Investment thesis** (顶部, 跟决策卡片紧邻):
- `headline`: "建议 LARGE (70%): NACS_adj=0.4969, 9 项强驱动 / 1 项主风险, 类比组实战正面"
- 主驱动列表 (≥75 子项 + cluster bonus 等): 每条带 score 和 reason 详情
- 主风险列表 (≤45 L1/L2 子项 + ≥0.40 L3 子项): 同上
- 类比组实证: similar_cases 的 d30/m6 中位 + winrate + verdict (favorable/neutral/cautious)
- 当下市场情绪: panel.regime_score 解读

**Per-component reasons** (折叠在 L1/L2/L3 详情下, 每子项一行):
形如 `PE_at_offer=22.6 vs peer_median=170.0 → 折让 +86.7%; 命中 [≥30% 折让 → 满分 100]`,
显式给出阈值带 + 计算依据, 投委会能从 "L1.1=84" 一直追到底层公式.

**Adjustment explanations**: 每个 adjustment (A+H/18C/regime gate 等) 都附一句
触发条件 / 业务含义.

实现:
- `src/nacs_rationale.py`: 跟 `_score_l*_*` 函数一一对应的 explain 函数,
  阈值表与 nacs_model 同源
- `src/reports/thesis.py`: 纯规则模板的 driver/risk/base-rate 综合器
  (不调 LLM, 100% 可重复 / 可审计)
- `compute_nacs` 末尾自动调 explain 把 reasons 填进 LayerBreakdown.reasons /
  NACSResult.decision_rationale, 不影响打分逻辑
- 模板 `ic_memo_single.html.j2` / `ic_memo_compare.html.j2` 渲染 rationale 段
