# Roadmap — `thesis.py` 接管主题情绪 + AI 镀金溢价

## 背景

`nacs_checklist_tool.html` (项目根) 当前是**唯一**接入 `themes/heat_today.json` /
`themes/history.csv` / `themes/premium_curve.json` 的 UI:

- 区块 VII 主题情绪追踪: 选主题 → 显示当前热度分 + 30/60/90d 趋势 + 过热警告
- 区块 VIII 估值溢价测算: 输入"AI 业务收入占比" → 算期望溢价 (镀金检测器)

我们已经在 `src/reports/thesis.py` 里搭好了 IC memo thesis 综合器
(drivers/risks/base_rate). 这份 roadmap 是把上面两块功能从 HTML 工具搬到
`thesis.py` + IC memo, 让 `analyze_deal --html` 在分析单 deal 时**自动**带上
其所属主题的热度 + 该公司期望溢价的上下文.

完成后:
- 决策权威 (analyze_deal) 与情绪/溢价 panel 在同一份 IC memo
- `nacs_checklist_tool.html` VII/VIII 区可以归档 (Option C 落地)
- 模型每次迭代不再有"两套规则"漂移风险

## 数据源现状

```
themes/heat_today.json       (cron 8:30 重写)
  {as_of, themes: {<theme_id>: {label, ret_5d/20d/60d, pe_ttm_avg,
                                heat_score: 0-100, reason, warning}}}

themes/history.csv           (cron 8:30 追加 1 行)
  date, ai_application, ai_driving, ai_server, humanoid_robot, ...
                                                (per-theme heat_score 0-100)

themes/premium_curve.json    (季度跑 research_premium_coefficient.py 后产出)
  {fitted_at, model: "log_linear: y=a*log(1+b*x)+c",
   params: {a, b, c}, r_squared, lookup_table: [{ai_pct, premium}, ...]}

themes/ai_revenue_manual.json (人工维护)
  {samples: {<stock_code>: {ai_revenue_pct, source, needs_review, ...}}}

themes/theme_definitions.json (人工维护; theme_id ↔ core_companies + iv_bkid)
```

注意: 这 4 份数据**完全独立于 panel_snapshots / nacs_predictions**, 是另一套数据流;
迁移只是接消费端, 不影响 `compute_nacs()`.

---

## 6 步落地计划

### Step 1 — `src/reports/themes_data.py` (~2h)

新文件, 纯数据加载 + 校验:

```python
def load_heat_today(themes_dir: Path = THEMES_DIR) -> Optional[Dict]:
    """Return {'as_of': date, 'themes': {theme_id: heat_record}} or None
    if themes/heat_today.json missing or stale (>3 days old)."""

def load_premium_curve(themes_dir: Path = THEMES_DIR) -> Optional[Dict]:
    """Return premium_curve.json content (params + lookup_table) or None."""

def load_history(themes_dir: Path = THEMES_DIR) -> Optional[pd.DataFrame]:
    """Return DataFrame indexed by date with theme columns."""

def load_ai_revenue_manual(themes_dir: Path = THEMES_DIR) -> Dict[str, float]:
    """Return {stock_code: ai_revenue_pct}; missing stocks return {}."""
```

测试 (~10 个): 文件不存在 → None, 过期 → warning, 格式错 → 抛错.

### Step 2 — Theme classifier: deal → theme_id (~1h)

新功能 in `themes_data.py`:

```python
def classify_deal_to_theme(stock_code: str, gics_l2: Optional[str],
                            ipo_concepts: List[str],
                            theme_definitions: Dict) -> Optional[str]:
    """对一个 deal 推断其所属主题 (theme_id), 没匹配返回 None.

    优先级:
        1. gics_l2 / ipo_concepts 直接匹配 theme_definitions[theme_id].keywords
        2. ipo_master.stock_code 在 theme_definitions[theme_id].core_companies
        3. 没匹配 → None (memo 里就不显示主题情绪 panel)
    """
```

测试 (~5 个): 半导体 IPO → semi_localization, AI server 概念 → ai_server, 完全
不沾 AI/科技 的消费 IPO → None.

### Step 3 — 扩展 `thesis.py::synthesize_thesis` 支持主题 panel (~2h)

```python
def synthesize_thesis(result, panel_snap=None, similar_cases=None,
                      themes_data=None,        # ← 新增: load_heat_today 等返回值
                      stock_code=None,          # ← 用于查 AI 占比
                      ai_revenue_pct=None,      # ← 来自 ai_revenue_manual 或人工 override
                      ) -> Dict[str, Any]:
    ...
    out["theme_heat"] = _build_theme_heat_section(stock_code, gics_l2,
                                                    ipo_concepts, themes_data)
    out["premium_estimate"] = _build_premium_estimate(
        ai_revenue_pct, themes_data["premium_curve"]
    )
    ...
```

输出新字段:

```python
"theme_heat": {
    "theme_id": "semi_localization",
    "label": "半导体国产化",
    "heat_score": 75,                 # 0-100
    "ret_60d": 0.117,
    "pe_ttm_avg": 28.5,
    "warning": "热度>80, 锁定期反转风险",  # 可空
    "trend_30d": [70, 72, 75, ...],    # 来自 history.csv 最近 30 天
    "verdict": "moderate" / "overheated" / "trough" / "warm",
    "reason": "kimi-generated 解读"
}

"premium_estimate": {
    "ai_revenue_pct": 0.05,            # 输入 (5%)
    "expected_premium": 0.24,           # 模型期望 (lookup_table 查值)
    "curve_params": {"a": 0.7, ...},
    "interpretation": "5% AI 收入 → 期望溢价 +24% (来自 36 样本回归 R²=0.62)",
    "r_squared": 0.62
}
```

测试 (~8 个): heat_score 80+ → verdict='overheated', 30d 趋势升 → trend up,
ai_revenue_pct=None → premium=None.

### Step 4 — IC memo 模板加 panel (~1.5h)

`src/reports/templates/ic_memo_single.html.j2` thesis 段后追加:

```html
{% if thesis.theme_heat %}
<article class="card theme-heat">
  <h3>主题情绪 (live, asof {{ themes_asof }})</h3>
  <div class="heat-display">
    <span class="heat-label">{{ thesis.theme_heat.label }}</span>
    <span class="heat-score-{{ thesis.theme_heat.verdict }}">
      {{ thesis.theme_heat.heat_score }}/100
    </span>
    <small>60d {{ thesis.theme_heat.ret_60d | pct }}, PE {{ thesis.theme_heat.pe_ttm_avg | num(1) }}</small>
  </div>
  {% if thesis.theme_heat.warning %}
  <div class="warnings">{{ thesis.theme_heat.warning }}</div>
  {% endif %}
  <!-- mini sparkline 30d trend, 用 SVG -->
  <svg class="sparkline" ...>...</svg>
  <p class="reason">{{ thesis.theme_heat.reason }}</p>
</article>
{% endif %}

{% if thesis.premium_estimate %}
<article class="card premium-estimate">
  <h3>估值溢价测算 (AI 镀金检测器)</h3>
  <table>
    <tr><td>AI 收入占比</td><td>{{ thesis.premium_estimate.ai_revenue_pct | pct }}</td></tr>
    <tr><td>期望溢价 (model)</td><td>{{ thesis.premium_estimate.expected_premium | pct }}</td></tr>
    <tr><td>模型 R²</td><td>{{ thesis.premium_estimate.r_squared | num(2) }}</td></tr>
  </table>
  <p>{{ thesis.premium_estimate.interpretation }}</p>
</article>
{% endif %}
```

CSS additions: `.heat-score-overheated` (red), `-warm` (amber), `-moderate` (blue),
`-trough` (green); `.sparkline` 内联 SVG.

### Step 5 — `analyze_deal.py` 接 themes_data + ai_revenue_pct (~1h)

```python
# scripts/analyze_deal.py
from reports.themes_data import (
    load_heat_today, load_premium_curve, load_history,
    load_ai_revenue_manual, classify_deal_to_theme,
)

# 在 main() 里 (--html 路径前):
themes_data = {
    "heat_today": load_heat_today(),
    "premium_curve": load_premium_curve(),
    "history": load_history(),
    "ai_revenue_manual": load_ai_revenue_manual(),
}
# 渲染时透传:
html = render_single_deal(
    recs, snap, asof=..., similar_cases=...,
    themes_data=themes_data,
    ai_revenue_pct_override=args.ai_revenue_pct,  # 新 CLI flag
)
```

新 CLI flag:
```bash
python scripts/analyze_deal.py --stock-code 1187.HK --html ... \
    --ai-revenue-pct 0.05    # override ai_revenue_manual 的值
```

`load_deal.py` 可以在 deal YAML 也加这个字段:
```yaml
themes:
  ai_revenue_pct: 0.05      # 招股书披露的 AI 业务收入占比
  override_theme_id: semi_localization   # 强制用某主题, 否则 classify
```

### Step 6 — 归档 `nacs_checklist_tool.html` (Option C, ~30min)

完成 Step 1-5 + 验收后:

1. `git mv nacs_checklist_tool.html legacy/nacs_checklist_tool.html`
2. 加 `legacy/README.md` 说明 "已被 analyze_deal --html + thesis 主题段替代"
3. README.md 移除 quick estimator 描述, 改为指向 `analyze_deal.py`
4. 删除 themes/README.md 的 "HTML 集成" 一节
5. 保留 `themes/` 数据生产流水线不变 (cron 任务还在跑, 给 thesis.py 消费)

---

## 总工时 + 验收

| Step | 内容 | 工时 |
|---|---|---|
| 1 | themes_data 加载层 + 测试 | 2h |
| 2 | Theme classifier (deal → theme_id) | 1h |
| 3 | thesis.py 加 theme_heat + premium_estimate | 2h |
| 4 | 模板 + CSS sparkline + warning 颜色 | 1.5h |
| 5 | analyze_deal CLI + deal YAML themes 字段 | 1h |
| 6 | 归档 HTML + README 切换 | 0.5h |
| | **合计** | **~8h (1 工作日)** |

测试目标: 覆盖率 79% → 81%+, 至少 25 个新测试 (含 themes_data, classifier,
thesis 扩展, 渲染 snapshot).

验收: 跑 `analyze_deal --stock-code <一个 AI 主题 IPO> --html` 输出的 memo 应
包含主题情绪 panel + 溢价测算 panel, 数值与 nacs_checklist_tool.html VII/VIII
区一致 (差异 < 0.01).

---

## 不在本 roadmap 范围

- `theme_tracker.py` / `research_premium_coefficient.py` 本身的算法迭代
  (这是上游数据生产, 跟下游消费独立)
- 主题 attribution (即"这只 IPO 涨了, 多少归因于主题热度 vs 公司基本面"),
  这是另一个研究问题
- 实时主题情绪 webhook (cron 跑一次/日已够)

---

## 关键设计选择 + 理由

**为什么放 thesis.py 而不是新建 themes_thesis.py**:
thesis.py 已经是 IC memo 的"综合段"入口, drivers/risks/base_rate 在那, 主题情绪
+ 溢价测算是同类型"上下文信息", 自然放一起. 拆开会让模板要 fetch 两个综合源.

**为什么不让 thesis 自动 fetch themes 数据**:
保持 `thesis.py` 是纯函数 (传入 themes_data dict). I/O 在 analyze_deal.py 的 main
里做. 这样测试时不需要 mock 文件系统.

**为什么 ai_revenue_pct 既支持 manual 文件又支持 deal YAML override**:
ai_revenue_manual.json 是历史样本 (已上市公司过去 2 年), 用于 fit premium_curve.
新 deal 的 AI 占比要从招股书读, 应在 deal YAML 里录入, 不是塞进 manual 文件污染拟合数据.
