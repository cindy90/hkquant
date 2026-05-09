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
