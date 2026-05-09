# legacy/ — 已归档的工具

## `nacs_checklist_tool.html` (914 行, ~46KB)

**归档时间**: 2026-05-09 (B 计划完成时)
**归档原因**: 三块功能均已被取代

### 原工具的 3 个功能 → 现在的去向

| 区块 | 原功能 | 现在去 |
|---|---|---|
| **I-VI** 决策表单 | 用户手填 14 字段 → JS 即时打分 (v3.2 规则硬编码) | `python scripts/analyze_deal.py --stock-code <code>` (调真实 `nacs_model.compute_nacs()`, 跟 `configs/nacs_v8.yaml` 同步) |
| **VII** 主题情绪追踪 | 选主题 → 显示当前热度分 + 30/60/90d 趋势 | `analyze_deal --html` 输出的 IC memo 自动带 "主题情绪 (live)" panel; 来自 `src/reports/thesis.py::_build_theme_heat_section`, 数据走同样的 `themes/heat_today.json` + `themes/history.csv` |
| **VIII** 估值溢价测算 (AI 镀金检测器) | 输入 AI 占比 → 算期望溢价 | IC memo "估值溢价测算" panel; 来自 `src/reports/thesis.py::_build_premium_estimate`, 数据走同样的 `themes/premium_curve.json` + `themes/ai_revenue_manual.json` |

### 为什么归档而非删除

1. **审计追溯**: 早期投决会议记录中可能引用过这个工具的输出 (e.g. "看 nacs_checklist 显示是 LARGE"); 留作历史参考
2. **JS 决策树是 v3.2 实证规则的活化石**: 跟现在 `configs/nacs_v8.yaml` 的 5-band 阈值表对比, 能看到模型 2 年迭代的具体细节
3. **回归测试需要**: 万一新工具出 bug, 还能拿这个对照算

### 不要做的事

- **不要再用它做投决**: 决策口径已 drift, JS 规则在 nacs_model 多次迭代后再没同步过
- **不要从这里 fork 代码**: HTML/CSS 风格 (IBM Plex / Cormorant Garamond + 金/黑配色) 跟新 IC memo 的 system-font 简洁风不一致, 不要混用
- **不要修复它的 bug**: 任何问题应在 `analyze_deal --html` 处修

### 如果一定要打开

```bash
python -m http.server 8080
# 浏览器访问 http://localhost:8080/legacy/nacs_checklist_tool.html
```

直接 `file://` 打开会因 CORS 限制无法 fetch `themes/*.json` (这是早期没修复的设计缺陷).
