# data/derived/backtest — 回测产物归档

## 目录结构

```
backtest/
├── latest/                      # 最新一次回测的 IC 摘要
│   ├── ic_static.json           # 静态模式（事后口径）
│   └── ic_realtime.json         # 实时模式（无未来信息）
└── iterations/                  # 历次模型迭代快照
    ├── p1_10/                   # P1 修复第 10 版
    ├── p1_11/
    ├── p1_lockup_v2/            # 引入 lockup 字段后第 2 版
    ├── p2_1/                    # P2 修复第 1 版
    └── p2_2/
```

每个 `iterations/<tag>/` 子目录含：
- `backtest_ic_realtime.json`: 该次迭代的实时模式 IC 摘要
- `nacs_v7_scores.csv`: 该次迭代的全样本评分

## ic_*.json schema

```jsonc
{
  "mode": "static" | "realtime",
  "n_total": 384,
  "main_board": {
    "5d":  { "ic": 0.04, "n": 269, "ls_spread": -0.01, "ls_t_stat": -0.20 },
    "30d": { ... },
    "60d": { ... },
    "180d": { ... }
  },
  "regime_pass": { ... },   // 仅 regime_score 通过门槛的子集
  ...
}
```

## 注意事项

- **static 模式 vs realtime 模式**：static 用全样本拟合参数（看后视镜），realtime 用滚动训练窗口防 look-ahead。生产环境只信 realtime
- main_board 子集排除 18a/18c/secondary，是核心评估口径
- regime_pass 子集对应 regime_score 通过门槛的样本，用于检验"择时滤镜"是否有效

## 重新生成

```bash
python run_v7_backtest.py
```
