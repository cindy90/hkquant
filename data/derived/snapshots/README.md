# data/derived/snapshots — 带日期的派生数据快照

## 目的
所有探索脚本 / 回测的"带日期"输出都堆在这里, 一日一目录:
```
snapshots/2026-05-09/
  ├─ chapter_report_20260509.csv
  ├─ chapter_mismatch_20260509.csv
  ├─ industry_report_20260509.csv
  ├─ industry_mismatch_20260509.csv
  ├─ ic_results_20260509.csv
  ├─ ic_robustness_20260509.csv
  ├─ ic_top50_20260509.csv
  ├─ ic_top_signals_detail_20260509.csv
  └─ nacs_v7_scores_20260509.csv
snapshots/2026-05-10/  ← 下一次跑加新目录
```

## 配套: `data/derived/latest/`
`latest/` 里全是 symlink, 指向**最新**的快照文件 (无日期后缀的稳定别名),
所有探索脚本应该读 `latest/<filename>.csv`, 而不是直接读带日期的版本.

切换 latest 指向 = 一次性更新所有 symlink:
```bash
cd data/derived/latest
ln -sf ../snapshots/2026-05-10/chapter_report_20260510.csv chapter_report.csv
# ... 其它文件类似
```

## 写入约定
- 探索脚本输出请直接落到 `snapshots/<YYYY-MM-DD>/<name>_<YYYYMMDD>.csv`
- **不要**把带日期的快照写到 `latest/`, `verification/`, `peer_ic/`, `scores/`
- `verification/`, `peer_ic/`, `scores/` 仅保留 README, 不再放数据文件
