# data/derived/_archive — v6 → v7 迁移前的实验产物

## 目的

保留模型版本演进过程中**已被替代**的派生文件, 不直接删除以保留口径迁移的审计追溯.
任何探索/回测脚本**不应**读取本目录.

## 文件清单

| 文件 | 时点 | 替代品 | 为什么归档 |
|---|---|---|---|
| `ipo_d30_returns.csv` | 2026-05-07 | DB 表 `ipo_returns` | 键名是 `thscode` 而非 `stock_code`, 与新 schema 不一致 |
| `nacs_v7.csv` | 2026-05-07 | `data/derived/snapshots/<date>/nacs_v7_scores_*.csv` | v7 早期输出, 5 月 9 日 dedupe 前包含 `03296.HK` 重复; 列结构与新版略有差 |
| `nacs_yearly_aff.csv` | 2026-05-07 | (无直接替代; 此实验未推进) | 269 行按年份×affiliation 实验, 与 DB 后续口径冲突, 未再使用 |

## 与 governance migration v1/v2 的关系

这些文件是 **v1/v2 治理之前** 的实验产物:
- v1 (2026-05-09 11:03): dedupe / currency / due flags / gross_proceeds
- v2 (2026-05-09 12:27): status / panel_snapshots / nacs_predictions

`nacs_v7.csv` 含的 `03296.HK` 重复就是 v1 dedupe 之前的现象. 任何用这些文件做的
分析结论, 应在 v1 之后用 `data/derived/snapshots/<date>/` 重做.

## 处置策略

- 保留: 这些文件作为口径变迁的"化石", 帮助定位"为什么 NACS 在 v6→v7 之后变了"
- 不读取: 任何下游脚本不应 `read_csv("data/derived/_archive/...")`. 如发现, 应改为读
  当前权威源 (`data/nacs_real.db` 或 `data/derived/snapshots/<latest>/`)
- 后续删除: 累计 6 个月无追溯查询时, 可整目录归档到外部对象存储后从 git 历史里删
