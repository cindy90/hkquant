# data/derived/scores — NACS 评分快照

## 文件清单

| 文件 | 行数 | 说明 |
|---|---|---|
| `nacs_v7_scores_20260509.csv` | 384 | NACS v7 模型评分快照（含 Q_company / Q_ecosystem / R_lockup / decision） |

## Schema

```
ipo_id, stock_code, name, listing_date, listing_chapter,
NACS, Q_company, Q_ecosystem, R_lockup,
decision, position_pct,
regime_score, cluster_count,
r5d, r30d, r60d, r180d,
year
```

## 字段口径

| 字段 | 含义 |
|---|---|
| `NACS` | 综合评分（0-1），`= w1·Q_company + w2·Q_ecosystem + w3·R_lockup` |
| `Q_company` | 公司质量分（0-1） |
| `Q_ecosystem` | 生态/同业景气分（0-1） |
| `R_lockup` | 锁定期风险/吸引力（0-1） |
| `decision` | 投资动作枚举（PASS/STAGE/INVEST/AGGRESSIVE） |
| `position_pct` | 建议仓位占比 |
| `regime_score` | 当期市场 Regime 打分 |
| `r5d/r30d/r60d/r180d` | 已实现收益（用于事后归因，不是评分输入） |

## 重新生成

```bash
python run_v7_backtest.py
```

输出会同时落到 `outputs/nacs_v7_scores.csv` 和 `outputs/backtest_ic_*.json`。归档时按日期后缀搬迁。
