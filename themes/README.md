# themes/ — 港股主题情绪追踪 + 估值溢价测算

服务于"复合业务公司估值"问题. 核心痛点:

> 像华勤这种公司只有 5% AI 收入, 但市场可能给 50% AI 估值溢价 — 怎么量化这种"镀金"现象?

## 文件清单

| 文件 | 类型 | 维护方式 |
|---|---|---|
| `theme_definitions.json` | 配置 | 手工维护 (8-10 个主题, 每个 5-10 只核心可比公司 + 关键词) |
| `ai_revenue_manual.json` | 配置 | 手工维护 (~25 只 AI 概念股的 AI 业务收入占比) |
| `theme_tracker.py` | 每日脚本 | 与 `scripts/fetch_hk_market_data.py` 一起跑 (8:30) |
| `research_premium_coefficient.py` | 一次性研究脚本 | 月度跑一次 / AI 占比表更新后跑 |
| `history.csv` | 输出 (累积) | 自动生成. 每行 = 一天 × 所有主题热度分 |
| `heat_today.json` | 输出 (覆盖) | 每天覆写, HTML 工具直接 fetch |
| `premium_curve.json` | 输出 (拟合) | 跑 `research_premium_coefficient.py` 后产出 |

## 每日流程 (8:30 cron)

```bash
cd 港股基石轮投资模型
python scripts/fetch_hk_market_data.py            # 主流程: 拉 HSI/南向/IPO/themes.json
python themes/theme_tracker.py                    # 主题追踪: 拉板块 + PE + 研报 + Kimi 打分
```

`theme_tracker.py` 输出:
- `themes/heat_today.json` — 当日所有主题的热度分 (0-100) + Kimi 解读
- `themes/history.csv` — 追加一行 (date + 每个主题一列), 用于 30/60/90d 趋势分析

## 一次性研究脚本

```bash
python themes/research_premium_coefficient.py            # 拉数据 + 拟合 + 输出 premium_curve.json
python themes/research_premium_coefficient.py --no-fetch # 用上次缓存重拟 (调参用)
python themes/research_premium_coefficient.py --dry-run  # 不调 iFinD, 只测路径
```

输出 `themes/premium_curve.json`:
```json
{
  "model": "log_linear: y = a * log(1 + b*x) + c",
  "params": {"a": 0.7, "b": 8.0, "c": 0.0},
  "lookup_table": [
    {"ai_pct": 0.05, "premium": 0.24},  // 5% AI → 期望溢价 24%
    {"ai_pct": 0.30, "premium": 0.88},
    ...
  ]
}
```

## 下游集成

themes/ 输出 由 IC memo 通过 `src/reports/themes_data.py` (loader)
+ `src/reports/thesis.py` (`_build_theme_heat_section` + `_build_premium_estimate`)
集成到 `analyze_deal --html` 的输出.

```bash
python scripts/analyze_deal.py --stock-code 1187.HK \
    --html outputs/1187.html
# 输出 HTML 含:
#   - 主题情绪 panel (heat score + 30d sparkline + verdict 颜色编码)
#   - 估值溢价测算 panel (AI 占比 → 期望溢价, 含 R² 提示)
```

阈值 (`src/reports/thesis.py`):
- 热度 ≥80 → `overheated`, memo 显示红色徽章 + warning "锁定期反转风险高"
- 热度 60-79 → `warm` (橙)
- 热度 40-59 → `moderate` (蓝)
- 热度 <40 → `trough` (绿) + warning "主题谷底, 可能是基石入场好时机"

主题归类: `classify_deal_to_theme()` 按 (core_companies → 高置信) +
(keywords → 中/低置信) 匹配 `theme_definitions.json`. 没归类的 deal 不显示
panel.

每次评估的 themes 数据来源 (5 个文件的 mtime / asof / is_stale + classifier
match_signals) 完整写进 `nacs_predictions.themes_provenance_json`, 事后
case_review 可追溯.

历史 `nacs_checklist_tool.html` (本 themes/ 数据的早期消费方) 已归档至
`legacy/`, 见 `legacy/README.md`.

## 关键字段含义

### theme_definitions.json
- `iv_bkid`: 同花顺板块 ID (与 `data/watchlist.json` 共用)
- `core_companies`: 5-10 只核心可比公司, 用于拉 PE_TTM 和研报情绪
- `keywords`: 给 Kimi 提示词用 (主题相关关键词)

### history.csv
列结构: `date, ai_server, llm, humanoid_robot, semi_localization, ...`
每个值 = 当日 0-100 整数热度分

### premium_curve.json
- `params.a`: 振幅系数 (默认 0.7)
- `params.b`: 陡度系数 (默认 8.0, 越大低占比阶段越陡)
- `params.c`: 截距 (默认 0.0)
- 默认曲线含义: 0% AI → 0; 5% AI → +24%; 30% → +84%; 100% → +154%

## 维护节奏

- **每天**: 自动 (cron 跑 theme_tracker.py)
- **季度**: 校正 `ai_revenue_manual.json` (新发年报 / 招股书更新)
- **季度后**: 重跑 `research_premium_coefficient.py` 更新拟合曲线

## 排查

| 现象 | 原因 | 处置 |
|---|---|---|
| HTML 显示"无数据" | `heat_today.json` 没生成 | 跑 `python themes/theme_tracker.py` |
| 趋势图"数据点不足" | `history.csv` 不足 7 天 | 累积即可 (前几天先用单点) |
| Kimi 调用失败 | `KIMI_API_KEY` 未配置 | `.env` 里加 `KIMI_API_KEY=sk-...`, 或用 `--no-kimi` 跑启发式 |
| `premium_curve.json` 是默认值 | 没跑 research 脚本 | 跑一次 `research_premium_coefficient.py` |
| 拟合 R² 低 | `ai_revenue_manual.json` 大量 needs_review=true | 校正后再跑 |
