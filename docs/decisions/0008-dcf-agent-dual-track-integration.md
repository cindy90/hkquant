# ADR 0008: DCF agent 联动 — 算法借鉴 + 双轨保留

- **Status**: Accepted
- **Date**: 2026-05-16
- **Deciders**: project lead
- **Supersedes**: 无
- **Superseded by**: 无

## Context

用户本地已有一个独立的 DCF agent（`D:\自定义工具\投资建议书agent\DCF agent`）：

- **形态**：Claude Code Skill（~5300 行 Python + 877 行 SKILL.md + 9 reference markdown 文件）
- **触发**：人对话 "建个三表模型" → skill 接管 9 session 引导流程
- **输入**：tushare / yfinance / NotebookLM / Excel 上传 + 一份 `project_config.yaml`
- **输出**：**Excel 文件** — 完整 IS/BS/CF 公式联动 + DCF + Comps + Valuation_Summary + 9 项 QC 检查
- **覆盖**：三表模型（Sessions A-E）+ DCF（F）+ Sensitivity（G）+ Comps（H）+ Valuation Summary（J）
- **示例案例**：翼菲科技 6871.HK 已完整跑通

而 PROJECT_SPEC.md §3.7 + ADR 0005 §2 要求的 Phase 4 估值层是另一形态：

- **形态**：Python async 库，输出 Pydantic `ValuationDistribution`
- **触发**：LangGraph node 调 `await ValuationModel.value(extraction, market_data)`
- **输入**：`ProspectusExtraction`（Phase 3 产物）+ iFind 市场数据（Phase 2 客户端）
- **输出**：`ValuationEnsembleOutput`（多模型分布 + 权重 + 集成 + price_range）
- **必须模型**：comparable / dcf / pre_ipo_anchor / ah_premium / milestones / industry/* / monte_carlo (10000 路径) / ensemble (含 Regime Gate 硬门)

两者**形态、消费者、数据流均不同**：
- DCF agent → 人看的 Excel 投行模板，单次约 2-3 小时人工对话
- Phase 4 → 7 agent + Synthesizer 消费的结构化分布，<30 秒自动跑

但 DCF / Comps 的**数学公式**（WACC、UFCF、Gordon TV、Exit Multiple TV、EV→Equity 桥接、PS/PE/EV-Sales 倍数分位数）是通用金融知识，且 DCF agent 已在真实案例验证过实现细节（行号布局、公式字符串、QC 阈值）。

## Decision

**采用方案 A — 算法借鉴 + 双轨保留**。具体路径：

### 1. Phase 4 按 spec 自建，算法参考 DCF agent
- `src/hk_ipo_agent/valuation/` 9 个模块按 spec §3.7 完全自建为 Python async 库
- **WACC + UFCF + Terminal Value + EV→Equity** 公式 → 参考 `DCF agent/_skill_extracted/references/session-f.md` 第 120-200 行
- **PS/PE/EV-Sales 分位数 + peer 过滤规则** → 参考 `session-h.md` Block 1-3
- **Sensitivity 表** → MC 是其超集，spec 要求 10000 路径 MC；DCF agent 的敏感性 + 三情景作为 MC 输入分布的灵感来源
- **不复制代码**，只借鉴公式与阈值（这些是公开金融知识）

### 2. DCF agent 作为独立 skill 完整保留
- 不修改、不重构 `D:\自定义工具\投资建议书agent\DCF agent\` 任何文件
- 继续服务原有场景：人工 IPO/Research/IC memo Excel 输出
- 9 session 对话流形态对 Excel 投行模板有不可替代价值，spec Phase 4 的 Pydantic 输出代替不了

### 3. Phase 7 报告层加 "invoke_dcf_skill" adapter
- 投决备忘录（Phase 7 reporting）可选择性产出 Excel 附件
- 实现方式：`reporting/exporters/dcf_excel.py`，写一份 `project_config.yaml` 后通过 subprocess 调 DCF agent skill 或文档化让 reviewer 手动触发
- **Phase 7 时再实施**；Phase 4 不依赖此 adapter

### 4. iFind 客户端知识合并（已在本次完成）
- DCF agent 的 `shared/ifind_client.py`（430 行）+ `ifind_indicator_catalog.csv`（52 条验证过的指标）含具体生产可用的 endpoint 调用约定
- 已将 catalog 拷贝到 `data/knowledge_base/ifind_indicator_catalog.csv`
- 已将 7 个具体方法（`get_financials` / `get_ipo_history` / `get_ipo_basics` / `get_hk_history_prices` / `get_valuation_snapshot` / `get_ah_premium_history` / `get_macro_index_history` / `query_edb`）+ 4 个 indicator 常量串 + `HK_MACRO_QUOTE` 索引映射 合并到 `src/hk_ipo_agent/data/sources/ifind_client.py`
- Phase 2.1 iFind 凭证就绪后这些方法可立即跑

## Consequences

### Positive
- **Phase 4 算法实现风险显著降低**：直接借用 DCF agent 已验证的公式 / 行号 / 阈值，不需要从头研究
- **DCF agent 用户场景零影响**：原工具完整保留，原用户继续受益
- **iFind 客户端立即升级**：Phase 2 骨架方法变成 production-ready endpoint，少一轮 Phase 2.1 返工
- **未来 Phase 7 投决备忘录可附 Excel**：reviewer 验签时能看到完整可审计公式链路
- **职责清晰**：spec Phase 4 = 自动决策路径；DCF agent = 人工深度建模路径；互补不互替

### Negative
- **算法借鉴需手动 cross-check**：DCF agent 是 Excel 公式字符串，Phase 4 是 Python 数值；公式转译时若漏一个 `(1+wacc)^t` 之类的细节会埋 bug。
  - **Mitigation**：`tests/unit/valuation/` 写黄金回归测试，对同一组假设比对 Phase 4 数值与 DCF agent 翼菲案例 Excel 单元格输出
- **两套估值实现存在版本漂移风险**：如果将来 DCF agent 升级了 WACC 公式但 Phase 4 没跟，可能输出不一致
  - **Mitigation**：每个借鉴的公式在 Phase 4 代码注释中标明 `# Source: DCF agent session-f.md L165`；Phase 8 calibration 也会暴露明显漂移
- **Phase 7 adapter 不平凡**：DCF agent 是交互 skill 不是 library，subprocess 触发它需要 mock 用户对话或重新打包为 CLI；可能需要 ADR

### Neutral
- 现行 DCF agent **没有** Regime Gate / Pre-IPO Anchor / AH Premium / Milestones / Monte Carlo — 这些 spec 必须项纯由 Phase 4 自建，与 DCF agent 无冲突也无借鉴
- **`ah_premium.py` Phase 4 采用经验分布采样替代 spec §3.7 的 6 因子回归**：AH 双重上市新股历史样本极少（<30 只），多因子回归在小样本下极易过拟合。Phase 4 先用 `FromArray(ah_premium_history_pct)` 经验采样（等效于非参数 bootstrap），fallback 为 `Triangular(0.15, 0.30, 0.40)` 行业基线。Phase 8 calibration 阶段待样本积累 ≥50 + iFind AH 溢价指数 ready 后，升级为 spec 要求的多因子回归（Beta差 / 流通市值差 / 流动性差 / 股息率 / 行业 / AH溢价指数）
- **`comparable.py` Phase 4 暂无显式流动性折价调整**：spec §3.7 要求"跨市场可比带流动性折价"；Phase 4 接受任意市场的 peer_multiples（跨市场能力由 data layer 组装），但未对低流动性标的做额外折价。Phase 8 校准时将根据 bid-ask spread / 成交额 / 自由流通比率计算折价系数并注入 MC 假设

## Progress

- [x] **现在**：iFind 客户端知识合并（catalog + 7 个 endpoint + 4 个常量 + macro 索引）
- [x] **现在**：本 ADR 0008 写就，记录方案
- [x] **Phase 4 (2026-05-16)**：valuation/ 10 模块按 spec 自建，DCF（`dcf.py` 末尾）/Comps（`comparable.py` 末尾 + `_MULTIPLE_LOW/HIGH`）公式注释标明 DCF agent 来源（session-f.md L120-200 / session-h.md Block 1-3）
- [x] **Phase 4 (2026-05-16)**：tests/unit/valuation/ 58 单测 + 3 DONE-condition smoke；翼菲案例完整 cell-level 黄金回归留待 Phase 9（需要先把翼菲招股书走完 Phase 3 提取出 `ProspectusExtraction` 再对比 Excel）
- [ ] **Phase 7**：`reporting/exporters/dcf_excel.py` adapter（subprocess 调 DCF agent 或 reviewer 手动触发，待 Phase 7 决定）
- [ ] **可选 Phase 9**：将 DCF agent 9 session 输出作为黄金 e2e 测试的人工对照基线
