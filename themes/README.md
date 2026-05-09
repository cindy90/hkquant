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

## HTML 集成

`nacs_checklist_tool.html` 在 verdict 区下方追加了两个 panel:

- **VII. 主题情绪追踪** — 选主题 → 显示当前热度分 + 30/60/90d 趋势图 + 警告
  - 热度 > 80: 主题过热, 锁定期反转风险高
  - 热度 < 40: 主题谷底, 可能是基石入场好时机

- **VIII. 估值溢价测算 (AI 镀金检测器)** — 输入 AI 收入占比 → 算期望溢价
  - 公式: `溢价 = 主题热度 × AI 收入占比 × 历史溢价系数 (premium_curve)`
  - 实际市场溢价超过模型预测 → 镀金过头, 减持/规避

HTML 直接 fetch 三个本地 JSON/CSV. **必须用本地 HTTP server 打开**(否则 file:// 跨源限制 fetch 会失败):

```bash
python -m http.server 8080
# 访问 http://localhost:8080/nacs_checklist_tool.html
```

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
