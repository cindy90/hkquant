# data/derived/peer_ic — 同业 Peer 信号 IC 探索结果

## 文件清单（2026-05-09 快照）

| 文件 | 行数 | 说明 |
|---|---|---|
| `ic_results_20260509.csv` | 756 | 全量 IC 矩阵 = 9 视图 × 4 聚合 × 7 窗口 × 3 池 |
| `ic_top50_20260509.csv` | 50 | 按 \|t-stat\| 排序的 Top 50 |
| `ic_robustness_20260509.csv` | 8 | 候选信号稳健性二筛：净 ΔIC、分年稳定性、五分位 L-S |
| `ic_top_signals_detail_20260509.csv` | 1123 | 8 个候选信号的逐 IPO 明细（X 信号值 / Y 收益 / n_peers） |

## 探索口径

- **样本**: 384 只港股 IPO（2022-2026）
- **防 look-ahead**: peer.listing_date < target.pricing_date − WINDOW_DAYS
- **门槛**: peer ≥3, 样本 ≥20

### 维度组合

| 维度 | 取值 |
|---|---|
| view | `chapter`, `hs_l1`, `hs_l3`, `ths_global_l1`, `ths_global_l4`, `sw_l1`, `sw_l3`, `concept_any`, `market` |
| agg | `mean`, `median`, `top3_recent_mean`, `n_peers` |
| window | `d1_close`(2d), `d30`(30d), `m3`(90d), `m6`(180d), `m12`(365d), `unlock_d30`(210d), `unlock_d90`(270d) |
| pool | `all`, `main_profitable`, `main_all` |

## Schema

### ic_results / ic_top50
```
view, agg, window, pool, n_obs, ic, t_stat, ls_spread, ls_t_stat
```
- `ic`: Spearman 相关系数
- `t_stat`: ic 的 t 统计量
- `ls_spread`: 五分位多空收益差（Q1-Q5 或 Q5-Q1，取决于 ic 符号）
- `ls_t_stat`: ls_spread 的 t 统计量

### ic_robustness
22 列：在 ic_results 9 列基础上加：
```
market_base_ic        # 市场基线 IC（同窗口 market 视图）
delta_ic              # 净增量 = ic - market_base_ic
Q1_mean, Q5_mean      # 信号 X 五分位的两端 Y 均值
ic_2022, ic_2023, ic_2024, ic_2025  # 分年 IC
n_2022, n_2023, n_2024, n_2025      # 分年样本数
```

### ic_top_signals_detail
```
signal, ipo_id, stock_code, name, chapter, listing_year, listing_date, pricing_date,
X, Y, n_peers
```
- `X`: 信号值（同业聚合后的指标）
- `Y`: 实际目标收益（按 window 字段对应）
- 用于在 BI 中绘制散点图、检查异常点

## 已识别强信号（Top 3）

| 排名 | signal | n | net ΔIC | t | L-S | 分年稳定性 |
|---|---|---|---|---|---|---|
| S2 | hs_l1 + top3_recent_mean + unlock_d90 | 125 | +0.21 | +2.3 | +27% | 3/3 同向 |
| S1 | ths_global_l1 + top3_recent_mean + d30 | 226 | -0.30 | -4.6 | -36%（反转） | 4/5 同向 |
| S7 | sw_l1 + top3_recent_mean + d30 | 130 | +0.22 | +2.9 | +26% | 4/4 但 sw 池偏 A+H |

详见上层分析报告。

## 重新生成

```bash
python scripts/explore_peer_industry_concept_ic.py    # → outputs/peer_ic_results.csv 等
python scripts/explore_peer_ic_robustness.py          # → outputs/peer_ic_robustness.csv 等
```
