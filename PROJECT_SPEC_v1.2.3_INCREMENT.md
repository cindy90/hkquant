# HK IPO Cornerstone Agent — 增量规范 v1.2.3

> **本文件性质**：对 `PROJECT_SPEC.md` (v1.2.1) 的**增量补丁**，与 v1.2.2 增量并列。代码库已按 v1.2.1 搭建完成。Claude Code 必须先理解现有代码库再做增量改造。

> **本增量主题**：**全过程可解释性 + 逐步推理产物 + 讨论协作层**。把系统从"输出一份最终报告"升级为"输出完整的、可逐步查看、可逐步讨论的推理链"。

> **依赖关系**：本增量建议在 **v1.2.2 增量 B（Agent 可观测性）之后**实施——因为每个 agent 的"思考过程"产物依赖 v1.2.2 的 `AgentExecutionTrace.thinking_steps` 和 `tool_call_traces`。若 v1.2.2-B 未做，本增量仍可工作，但 agent 推理细节会较粗。

> **解决的核心问题**：用户需要看到"每一步 agent / 模型是怎么分析和思考的、每一步结论是什么、最终结论如何一步步得出"，并能针对任意一步参与讨论、反馈给系统迭代。当前 v1.2.1 只输出一份最终简化报告，中间过程仅存于数据库 JSONB，不可读、不可讨论。

---

## 第一部分：设计理念

### 1.1 三个核心原则

1. **每一步都产出人类可读产物**：分析流程的每个节点（招股书抽取、7 个专家 agent、每个估值模型、每个辩论参与者、每个投资 Lens、Synthesizer、最终决策）都必须渲染一份独立的 Markdown 文档（称为 **Reasoning Artifact**）。

2. **产物是快照的"渲染视图"，不是新的真相来源**：`prediction_snapshots` 仍是唯一权威、不可变的数据源。Reasoning Artifact 是从快照确定性渲染出来的——给定同一个快照 + 同一个渲染模板版本，永远渲染出同样的 MD。这保持了与现有 immutability 设计的一致性。

3. **讨论独立于快照**：用户对任意产物的批注、评论、讨论是一个**独立的协作层**，绝不修改不可变快照。讨论可以反馈到 learning_loop 驱动迭代。

### 1.2 与现有模块的关系

| 现有模块 | 本增量如何扩展 |
|---|---|
| `prediction_snapshots`（JSONB 全量中间产出） | 不动。作为渲染的数据源 |
| `reporting/`（只渲染 investment_memo） | 扩展：新增 artifact 渲染子模块 |
| `agent_execution_traces`（v1.2.2 工具调用追踪） | 作为 agent 推理产物的细节来源 |
| `learning_loop/`（漂移检测 + 调整提议） | 扩展：用户在产物上的批注成为调整提议的新证据来源 |
| UI v1.3（IPO 详情页渲染 agent 输出） | 扩展：新增 artifact 浏览器 + 行级批注 + 推导链视图 |

---

## 第二部分：Reasoning Artifact 渲染层

### 2.1 目录结构变更

新增 `src/hk_ipo_agent/reporting/artifacts/`：

```
src/hk_ipo_agent/reporting/
├── ... (现有 report_builder.py / charts.py / exporters/ / templates/)
└── artifacts/                          # ★ 增量 v1.2.3 新增
    ├── __init__.py
    ├── artifact_renderer.py             # 核心渲染器：snapshot → MD 产物集
    ├── artifact_writer.py               # 写盘 + 写 DB
    ├── derivation_chain.py              # 推导链文档生成
    ├── manifest.py                      # 运行清单（导航索引）生成
    └── md_templates/                    # 各步骤的 Jinja2 MD 模板
        ├── _macros.j2                   # 公用宏（引用块、不确定性块等）
        ├── run_manifest.md.j2
        ├── prospectus_extraction.md.j2
        ├── specialist_agent.md.j2       # 7 个专家 agent 共用
        ├── valuation_model.md.j2        # 单个估值模型
        ├── valuation_ensemble.md.j2
        ├── debate_participant.md.j2     # bull/bear/devil/cross-checker 共用
        ├── investor_lens.md.j2          # 5 个 lens 共用（依赖 v1.2.2 增量 A）
        ├── lens_panel_divergence.md.j2
        ├── synthesis.md.j2
        ├── final_decision.md.j2
        └── derivation_chain.md.j2
```

### 2.2 产物文件组织

每次完整分析（一个 snapshot）产出一棵 MD 产物树，存放在：

```
data/analysis_runs/{ipo_id}/{snapshot_id}/
├── 00_manifest.md                       # 运行清单 + 导航索引（入口）
├── 01_prospectus_extraction.md          # 招股书抽取了什么、置信度
├── 02_agents/
│   ├── fundamental.md
│   ├── industry.md
│   ├── valuation.md
│   ├── policy.md
│   ├── liquidity.md
│   ├── cornerstone_signal.md
│   └── sentiment.md
├── 03_valuation/
│   ├── _ensemble.md                     # 多模型如何加权
│   ├── comparable.md
│   ├── dcf.md
│   ├── pre_ipo_anchor.md
│   ├── ah_premium.md                    # 若适用
│   └── monte_carlo.md
├── 04_debate/
│   ├── bull.md
│   ├── bear.md
│   ├── devils_advocate.md
│   └── cross_checker.md
├── 05_investor_lens/                    # 依赖 v1.2.2 增量 A，若未做则跳过本目录
│   ├── _panel_divergence.md
│   ├── value_lens.md
│   ├── growth_lens.md
│   ├── valuation_purist_lens.md
│   ├── contrarian_lens.md
│   └── cornerstone_lens.md
├── 06_synthesis.md                      # Synthesizer 如何权衡所有输入
├── 07_final_decision.md                 # 最终决策（投决备忘录）
└── 99_derivation_chain.md               # ★ 推导链：结论如何一步步得出
```

> 同一份产物树**同时**写入 DB（供 UI 渲染，见第六部分 `analysis_artifacts` 表）和写盘（供用户直接 git 化、离线查看、外部分享）。

### 2.3 每个 Reasoning Artifact 的标准 MD 结构

所有产物 MD 必须遵循统一结构（在 `_macros.j2` 定义公用块）：

```markdown
---
artifact_type: specialist_agent
step_name: fundamental_agent
ipo_id: <uuid>
snapshot_id: <uuid>
snapshot_schema_version: v2
render_template_version: 1.0
generated_at: 2026-05-17T14:30:00+08:00
model_used: claude-sonnet-4
cost_usd: 0.34
runtime_seconds: 42
---

# 基本面 Agent — {公司名}

## 1. 这一步做了什么
（一句话说明本步骤的职责）

## 2. 输入
- 收到的数据：招股书抽取结果、iFind 财务异常因子...
- 上游依赖：（如果本步骤依赖前面某步，列出并链接）

## 3. 思考过程
（来自 AgentExecutionTrace.thinking_steps —— agent 的推理链，
 按步骤呈现，不是只给结论）
1. 首先检查收入质量...
2. 发现 Top5 客户占比 68%，关联交易占 23%...
3. 因此判断业务实质存在客户集中度风险...

## 4. 工具调用记录
（来自 tool_call_traces —— 每次调了什么工具、查了什么、返回什么）
| # | 工具 | 查询 | 结果摘要 |
|---|---|---|---|
| 1 | ifind_tool | 财务异常因子 | 应收账款周转异常... |
| 2 | prospectus_tool | "客户集中度" | 招股书 p.142... |
（每行可展开看 raw_result，UI 侧链接到 tool_call_traces）

## 5. 结论
- 评分：business_quality 62 / financial_health 71 / governance 80
- 关键发现（每条带原文引用）：
  - **发现 1**：客户集中度偏高 — 证据：... [招股书 p.142]
  - ...
- 不确定性标记：
  - 财报仅覆盖 3 个会计期，趋势判断样本不足

## 6. 本步骤如何影响最终结论
（这一步的输出被下游哪些步骤使用 —— 形成可追溯链路）
→ 输入给：估值 Agent、辩论层 Bear、Value Lens、Synthesizer

## 7. 讨论
（此区块由 UI 动态注入 artifact_comments；MD 静态版显示评论数 + 链接）
```

**关键要求**：
- "思考过程"区块必须呈现 agent 的**推理链**（来自 v1.2.2 的 `thinking_steps`），不是只贴结论。这是用户需求的核心——"知道每一步是怎么思考的"。
- 每个结论性陈述必须带**原文引用**（沿用现有 `Citation` 机制）。
- 每个产物必须有"本步骤如何影响最终结论"区块，形成显式的步骤间依赖链路。

### 2.4 渲染时机与一致性

- **渲染时机**：在 orchestrator 图的 `create_snapshot` 节点之后、`report` 节点之内触发 `artifact_renderer.render_all(snapshot)`。即——快照固化后立即渲染整套产物。
- **确定性**：渲染是快照的纯函数。产物 frontmatter 记录 `snapshot_id` + `render_template_version`。
- **可重渲染**：如果渲染模板升级（`render_template_version` 变化），可对历史快照重新渲染。重渲染产生新版本产物，旧版本保留（产物本身可版本化，但底层快照永不变）。
- **失败处理**：某个产物渲染失败不能阻塞整个分析；记录失败、发 warning 警报、其余产物正常产出。

---

## 第三部分：推导链文档（Derivation Chain）

这是用户需求"最终结论如何一步步得出"的直接答案。`99_derivation_chain.md` 不是又一份报告，而是一张**结论溯源地图**。

### 3.1 推导链的结构

```markdown
# 推导链 — {公司名} 基石投资决策

## 最终结论
**决策：部分参与（PARTIAL）**
**价格条件：发行价 ≤ HKD 14.0 才参与**
**置信度：54%（中等偏低）**
**关键标记：philosophy_dependent = True（决策高度依赖投资哲学立场）**

## 结论是如何得出的（自顶向下展开）

最终决策 ← Synthesizer 权衡了以下输入：

├── 【投资 Lens 面板】分歧度 0.72（高）→ 触发 philosophy_dependent
│   ├── Value Lens：PASS — "无已验证盈利，安全边际不足" → [05/value_lens.md]
│   ├── Growth Lens：STRONG_PARTICIPATE — "颠覆性技术，TAM 巨大" → [05/growth_lens.md]
│   ├── Valuation Purist：CONDITIONAL — "内在价值 HKD 12-15" → [05/valuation_purist_lens.md]
│   ├── Contrarian Lens：PASS — "Pre-IPO 投资人套现迹象" → [05/contrarian_lens.md]
│   └── Cornerstone Lens：CONDITIONAL ★ — deal-breaker：解禁后流动性存疑
│       → 这是决策从 PARTICIPATE 下调到 PARTIAL 的关键摆动因素 → [05/cornerstone_lens.md]
│
├── 【辩论层】未解决分歧：客户集中度风险
│   ├── Bull：客户绑定深，转换成本高 → [04/bull.md]
│   └── Bear：Top1 客户占 41%，单点失败风险 → [04/bear.md]
│
├── 【估值集成】ensemble p50 = HKD 13.2，p10-p90 = HKD 9.1-18.4 → [03/_ensemble.md]
│   ├── 可比公司法：HKD 14.8（权重 40%） → [03/comparable.md]
│   ├── DCF：HKD 11.0（权重 30%） → [03/dcf.md]
│   └── Pre-IPO 锚定：HKD 13.5（权重 30%） → [03/pre_ipo_anchor.md]
│
└── 【7 个专家 Agent】综合评分见各 agent 产物 → [02/]

## 关键摆动因素（Key Swing Factors）
按对最终决策的影响力排序：
1. Cornerstone Lens 的流动性 deal-breaker（决策性，把结论从"参与"压到"部分参与"）
2. Lens 面板高分歧（把置信度从 ~75% 压到 54%）
3. 估值 p50（13.2）低于发行价区间上限 → 设定 HKD 14 价格条件

## 如果某一步结论不同，会怎样（反事实速览）
- 若 Cornerstone Lens 未提流动性 deal-breaker → 决策大概率为 PARTICIPATE
- 若 Lens 面板收敛（低分歧）→ 置信度大概率 > 70%
```

### 3.2 推导链的实现要点

- `derivation_chain.py` 从快照读取所有步骤输出，构建一棵**依赖 DAG**，然后渲染为带链接的 MD。
- 每个节点链接到对应的详细产物 MD。
- "关键摆动因素"由 Synthesizer 在决策时显式输出（见第五部分对 Synthesizer 的修改）——不是事后猜测，而是 Synthesizer 自己说明"哪些输入对结论影响最大"。
- "反事实速览"可选；若 v1.2.2 增量 A 的 Lens 层已实现，可基于 Lens 分歧给出简单反事实。完整反事实分析仍在 `learning_loop/counterfactual.py`。

---

## 第四部分：讨论与批注层

用户需求："参与讨论"、"通过反馈让模型迭代"。本层实现。

### 4.1 设计原则

- 批注/评论是**独立协作层**，绝不修改不可变快照或产物本身。
- 批注可锚定到三种粒度：整个产物 / 产物某个章节（按 heading anchor）/ 某条具体 finding（按 finding_id）。
- 批注支持线程（回复）。
- 批注有状态：`open` / `resolved` / `actioned`（已转化为系统改进）。
- 标记为"系统推理有误"的批注可升级为 learning_loop 的调整提议证据。

### 4.2 批注如何反馈到模型迭代

这是关键链路（呼应用户"通过反馈让模型迭代"的需求）：

```
用户在 02_agents/fundamental.md 的某条 finding 上批注：
"这个客户集中度判断忽略了招股书 p.155 披露的新签约大单"
        ↓
批注被标记为 category = "reasoning_flaw"
        ↓
learning_loop/comment_ingestor.py（新增）定期扫描此类批注
        ↓
聚合同类批注 → 若某 agent 在某类公司上反复被指出同类问题
        ↓
adjustment_proposer 生成提议：修改该 agent 的 prompt
        ↓
进入既有的 propose → 人工 review → apply 流程
```

**重要**：用户批注不直接改 prompt/config。它只是**证据**，仍走既有的"人工批准制"调整流程。这保持了 v1.1 learning_loop 的安全约束。

### 4.3 批注的"需系统回应"模式

某些批注用户希望系统回应（比如"请重新评估这一步，考虑 X 因素"）。这类批注：
- 标记 `requires_system_response = True`
- 触发一次**局部重分析**：不是重跑整个 pipeline，而是带上用户的额外上下文，重跑该单一步骤（agent / lens / 估值模型）
- 局部重分析产生一个**新快照**（不可变原则——不改老快照），并标注 `parent_snapshot_id` + `triggered_by_comment_id`
- 新快照渲染新产物树，用户可对比新旧

> 这是一个受控的"对话式迭代"：用户不满意某一步 → 批注 → 系统带新上下文重跑该步 → 产出新版本。全程留痕。

---

## 第五部分：数据库变更

全部 additive。

```sql
-- ========== 增量 v1.2.3：推理产物 + 讨论层 ==========

-- 渲染出的推理产物（供 UI 渲染 + 溯源；磁盘也存一份）
CREATE TABLE analysis_artifacts (
    id UUID PRIMARY KEY,
    snapshot_id UUID REFERENCES prediction_snapshots,
    ipo_id UUID REFERENCES ipo_events,
    artifact_type VARCHAR(50) NOT NULL,   -- 'specialist_agent'/'valuation_model'/'debate_participant'/'investor_lens'/'synthesis'/'final_decision'/'derivation_chain'/'manifest'/'prospectus_extraction'
    step_name VARCHAR(100) NOT NULL,      -- e.g. 'fundamental_agent'/'dcf'/'bull'
    file_path VARCHAR(500),               -- 磁盘相对路径
    content_md TEXT NOT NULL,             -- 渲染出的 MD 全文
    content_hash VARCHAR(64),
    render_template_version VARCHAR(20),
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (snapshot_id, step_name, render_template_version)
);
CREATE INDEX idx_artifacts_snapshot ON analysis_artifacts (snapshot_id);
CREATE INDEX idx_artifacts_ipo ON analysis_artifacts (ipo_id, artifact_type);

-- 产物 immutable（重渲染产生新行，不改旧行；复用 v1.1 trigger 函数）
CREATE TRIGGER artifact_no_update
    BEFORE UPDATE ON analysis_artifacts
    FOR EACH ROW EXECUTE FUNCTION prevent_snapshot_modification();

-- 用户对产物的批注 / 讨论
CREATE TABLE artifact_comments (
    id UUID PRIMARY KEY,
    artifact_id UUID REFERENCES analysis_artifacts,
    parent_comment_id UUID REFERENCES artifact_comments,  -- 线程回复
    anchor_type VARCHAR(20) NOT NULL,     -- 'artifact'/'section'/'finding'
    anchor_ref VARCHAR(200),              -- section heading anchor 或 finding_id
    author_user_id UUID REFERENCES user_accounts,
    body_md TEXT NOT NULL,
    category VARCHAR(40),                 -- 'question'/'reasoning_flaw'/'data_error'/'agree'/'suggestion'/'other'
    status VARCHAR(20) DEFAULT 'open',    -- 'open'/'resolved'/'actioned'
    requires_system_response BOOLEAN DEFAULT FALSE,
    linked_proposal_id UUID,              -- 若升级为 learning_loop 提议
    linked_child_snapshot_id UUID REFERENCES prediction_snapshots,  -- 若触发局部重分析
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    resolved_by UUID REFERENCES user_accounts
);
CREATE INDEX idx_comments_artifact ON artifact_comments (artifact_id, created_at);
CREATE INDEX idx_comments_status ON artifact_comments (status) WHERE status = 'open';
CREATE INDEX idx_comments_flagged ON artifact_comments (category)
    WHERE category IN ('reasoning_flaw', 'data_error');
```

**对 `prediction_snapshots` 的扩展**（additive，标识局部重分析衍生的快照）：
```sql
ALTER TABLE prediction_snapshots ADD COLUMN parent_snapshot_id UUID REFERENCES prediction_snapshots;
ALTER TABLE prediction_snapshots ADD COLUMN triggered_by_comment_id UUID;
-- 常规分析这两列为 NULL；批注触发的局部重分析才填
```

---

## 第六部分：新增 Pydantic Schemas

加到 `common/schemas.py`：

```python
# ===== 增量 v1.2.3：推理产物 + 讨论层 =====
class ArtifactType(str, Enum):
    MANIFEST = "manifest"
    PROSPECTUS_EXTRACTION = "prospectus_extraction"
    SPECIALIST_AGENT = "specialist_agent"
    VALUATION_MODEL = "valuation_model"
    VALUATION_ENSEMBLE = "valuation_ensemble"
    DEBATE_PARTICIPANT = "debate_participant"
    INVESTOR_LENS = "investor_lens"
    LENS_PANEL_DIVERGENCE = "lens_panel_divergence"
    SYNTHESIS = "synthesis"
    FINAL_DECISION = "final_decision"
    DERIVATION_CHAIN = "derivation_chain"

class AnalysisArtifact(BaseModel):
    id: UUID
    snapshot_id: UUID
    ipo_id: UUID
    artifact_type: ArtifactType
    step_name: str
    file_path: Optional[str] = None
    content_md: str
    content_hash: str
    render_template_version: str
    generated_at: datetime

class CommentAnchorType(str, Enum):
    ARTIFACT = "artifact"
    SECTION = "section"
    FINDING = "finding"

class CommentCategory(str, Enum):
    QUESTION = "question"
    REASONING_FLAW = "reasoning_flaw"
    DATA_ERROR = "data_error"
    AGREE = "agree"
    SUGGESTION = "suggestion"
    OTHER = "other"

class ArtifactComment(BaseModel):
    id: UUID
    artifact_id: UUID
    parent_comment_id: Optional[UUID] = None
    anchor_type: CommentAnchorType
    anchor_ref: Optional[str] = None
    author_user_id: UUID
    body_md: str
    category: CommentCategory
    status: Literal["open", "resolved", "actioned"] = "open"
    requires_system_response: bool = False
    linked_proposal_id: Optional[UUID] = None
    linked_child_snapshot_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime
    resolved_at: Optional[datetime] = None
    resolved_by: Optional[UUID] = None

class DerivationNode(BaseModel):
    """推导链中的一个节点。"""
    step_name: str
    artifact_id: Optional[UUID] = None
    summary: str                          # 该步的一句话结论
    children: List["DerivationNode"] = Field(default_factory=list)

class SwingFactor(BaseModel):
    """关键摆动因素 —— 由 Synthesizer 显式输出。"""
    factor: str
    impact: str                           # 对最终决策的影响描述
    influenced_step: str                  # 来自哪一步
```

**扩展现有 `FinalDecision`**（新增 Optional 字段）：
```python
class FinalDecision(BaseModel):
    # ... 现有字段 + v1.2.2 增量 A 字段 ...
    # 增量 v1.2.3 新增：
    swing_factors: List[SwingFactor] = Field(default_factory=list)  # 关键摆动因素
    derivation_summary: Optional[str] = None  # 结论推导的自然语言概述
```

---

## 第七部分：对现有文件的修改

### 7.1 `orchestrator/nodes.py`（修改 report 节点）

`report` 节点除了现有的 investment_memo，还要调用 `artifact_renderer.render_all(snapshot)` 渲染整套产物树，并通过 `artifact_writer` 写盘 + 写 DB。

### 7.2 `synthesizer/synthesizer.py`（核心修改）

Synthesizer 在产出 `FinalDecision` 时，必须**显式输出 `swing_factors`**——即"哪些输入对本次决策影响最大"。这不是事后归因，是 Synthesizer 自己在决策时就说明清楚。这是推导链"关键摆动因素"区块的数据来源。

同时输出 `derivation_summary`：用自然语言概述"结论是怎么一步步得出的"。

### 7.3 `reporting/report_builder.py`（小修改）

最终的 investment_memo 顶部增加一行链接，指向 `99_derivation_chain.md`，让读最终报告的人能一键进入推导链。

### 7.4 `learning_loop/`（新增 + 修改）

新增 `learning_loop/comment_ingestor.py`：
- 定期扫描 `artifact_comments` 中 `category IN ('reasoning_flaw', 'data_error')` 且 `status = 'open'` 的批注
- 聚合分析：某 agent / lens 是否被反复指出同类问题
- 把聚合结果作为新证据喂给 `adjustment_proposer.py`

修改 `adjustment_proposer.py`：调整提议的证据来源新增一类——"用户批注聚合"。

### 7.5 `prediction_registry/`（小修改）

局部重分析（批注触发的单步重跑）需要一个新入口。在 `registry.py` 或新增 `partial_reanalysis.py`：
- 接收 `(parent_snapshot_id, comment_id, step_to_rerun, extra_context)`
- 只重跑指定步骤，其余步骤复用父快照的结果
- 产出新快照，填 `parent_snapshot_id` + `triggered_by_comment_id`

---

## 第八部分：UI 集成（PROJECT_SPEC_UI.md v1.3 的增量）

UI 侧新增能力。对应 `PROJECT_SPEC_UI.md` 需要的增量：

### 8.1 新增页面 / 组件

```
src/app/(workbench)/ipo/[ipoId]/
└── artifacts/                           # ★ 新增：推理产物浏览器
    ├── page.tsx                         # 产物树导航 + 推导链入口
    └── [stepName]/page.tsx              # 单个产物的渲染 + 讨论

components/domain/
├── artifact-tree-nav.tsx                # 左侧产物树导航
├── artifact-viewer.tsx                  # MD 渲染 + 行级批注锚点
├── derivation-chain-view.tsx            # 推导链可视化（可折叠树）
├── comment-thread.tsx                   # 批注线程
├── comment-composer.tsx                 # 发表批注（选中文字触发）
└── swing-factors-panel.tsx              # 关键摆动因素面板
```

### 8.2 核心交互

- **产物浏览器**：IPO 详情新增 "Artifacts" tab。左侧是产物树（02_agents、03_valuation...），点击进入单个产物的 MD 渲染视图。
- **行级批注**：在产物 MD 任意段落 / finding 上选中文字 → 弹出 "评论" → 发表批注。批注以侧边 pin 形式显示（类似 Google Docs）。
- **推导链视图**：可折叠的树形图，从最终决策往下展开，每个节点链接到对应产物。这是用户"看清结论如何一步步得出"的主界面。
- **讨论 → 系统回应**：批注勾选 "需要系统重新评估这一步" → 触发局部重分析 → UI 显示新旧产物对比。
- **批注状态管理**：reviewer 可把批注标记为 resolved；标记为 reasoning_flaw 的批注进入 learning 队列（在 `/learning` 区可见）。

### 8.3 与现有 UI 的关系

- 现有的 IPO 详情 "Analysis" tab（v1.3 §4.2.1）保留——它是结构化的决策视图。
- 新增的 "Artifacts" tab 是**全过程逐步视图**——两者互补：Analysis tab 给"结论 + 关键图表"，Artifacts tab 给"每一步怎么想的 + 可讨论"。
- 现有的 "Chat" tab（与 agent 对话）保留——chat 是即时问答，artifact 批注是针对固化产物的结构化讨论，两者不同。

---

## 第九部分：CLAUDE.md 增量约束

在 `CLAUDE.md` 新增一节：

```markdown
## 推理产物与讨论层约束（v1.2.3 增量）

- 每次完整分析必须为每一个步骤渲染一份独立的 Reasoning Artifact MD，不允许只输出最终报告
- Reasoning Artifact 是快照的确定性渲染视图，不是新真相来源。给定同一快照 + 同一模板版本，必须渲染出同样的 MD
- 产物的"思考过程"区块必须呈现 agent 的推理链（来自 thinking_steps），不允许只贴结论
- 每个结论性陈述必须带原文引用（Citation）
- analysis_artifacts 表 append-only。重渲染产生新行（新 render_template_version），不改旧行
- 用户批注（artifact_comments）绝不修改不可变快照或产物
- 用户批注不直接改 prompt / config。批注只是证据，仍走既有的 learning_loop 人工批准制调整流程
- 批注触发的局部重分析必须产生新快照（填 parent_snapshot_id + triggered_by_comment_id），不改老快照
- Synthesizer 必须显式输出 swing_factors —— 决策时就说清哪些输入影响最大，不允许事后猜测
- 某个产物渲染失败不能阻塞整个分析流程；记录失败 + 发 warning，其余产物正常产出
```

---

## 第十部分：实施步骤与 DONE 条件

### 10.1 前置依赖

- **强烈建议先完成 v1.2.2 增量 B（Agent 可观测性）**：产物的"思考过程"和"工具调用记录"区块依赖 `thinking_steps` 和 `tool_call_traces`。
- 若 v1.2.2 增量 A（Lens 层）已做，本增量自动包含 Lens 产物；若未做，跳过 `05_investor_lens/` 目录相关模板即可。

### 10.2 实施步骤（建议 3-4 天）

1. DB migration：`analysis_artifacts`、`artifact_comments` 2 表 + `prediction_snapshots` 2 列 + triggers
2. 扩展 `common/schemas.py`（新模型 + 扩展 FinalDecision）
3. 新增 `reporting/artifacts/` 全部文件 + MD 模板
4. 修改 `synthesizer/synthesizer.py`（输出 swing_factors + derivation_summary）
5. 修改 `orchestrator/nodes.py`（report 节点调用 artifact 渲染）
6. 新增 `learning_loop/comment_ingestor.py` + 修改 `adjustment_proposer.py`
7. 新增局部重分析入口（`prediction_registry/partial_reanalysis.py`）
8. UI 侧：新增 Artifacts tab + 批注组件 + 推导链视图
9. 后端 API：新增 artifact 和 comment 相关 endpoint（见下）
10. 写测试 → 跑全量测试确认现有测试不破

### 10.3 新增 API endpoint（补充到主 SPEC §16.2）

```
GET    /api/v1/snapshots/{id}/artifacts              # 产物树
GET    /api/v1/artifacts/{artifact_id}               # 单个产物 MD
GET    /api/v1/snapshots/{id}/derivation-chain       # 推导链
GET    /api/v1/artifacts/{artifact_id}/comments      # 批注列表
POST   /api/v1/artifacts/{artifact_id}/comments      # 发表批注
PATCH  /api/v1/comments/{comment_id}                 # 更新批注状态
POST   /api/v1/comments/{comment_id}/trigger-rerun   # 触发局部重分析
```

### 10.4 DONE 条件

- 每次完整分析产出完整产物树（一个 IPO 至少 ~20 个 MD 文件），写盘 + 写 DB
- 每个产物 MD 包含：输入、思考过程、工具调用、结论、对最终结论的影响、引用
- `99_derivation_chain.md` 正确呈现"结论如何一步步得出" + 关键摆动因素
- 产物可从快照确定性重渲染（同快照同模板 → 同 MD，content_hash 一致）
- 用户能在 UI 任意产物上发表行级批注，批注线程正常
- 标记 reasoning_flaw 的批注能被 comment_ingestor 扫描并进入 learning_loop
- 批注触发局部重分析能产出带 parent_snapshot_id 的新快照
- 老快照（无产物）不受影响；现有所有测试仍全过

---

## 第十一部分：对主 SPEC 的章节修订映射

若合并回主 SPEC：

| 主 SPEC 章节 | 修改 |
|---|---|
| 顶部 banner | 加 v1.2.3 说明行 |
| §2 目录结构 | `reporting/` 下加 `artifacts/` 子树 |
| §3.x | 新增子章节描述 artifact 渲染层 |
| §4 Phase 7 | report 节点 deliverables 加产物渲染 |
| §5 数据库 | 加 `analysis_artifacts`、`artifact_comments` 2 表 + `prediction_snapshots` 2 列 |
| §6 Pydantic | 加 ~8 个新模型 + 扩展 FinalDecision |
| §11 CLAUDE.md | 加"推理产物与讨论层约束" |
| §16 UI 集成 | API 清单加 artifact / comment endpoint |
| `PROJECT_SPEC_UI.md` | 加 Artifacts tab + 批注组件 + 推导链视图 |

---

*Increment Version: 1.2.3*  
*Base: PROJECT_SPEC.md v1.2.1（建议在 v1.2.2 增量 B 之后实施）*  
*Last Updated: 2026-05-17*  
*主题: 全过程可解释性 —— 每一步推理产物 MD + 推导链 + 讨论协作层。*  
*核心: 系统不再只输出一份最终报告，而是输出完整的、逐步可查看、逐步可讨论的推理链；用户对任意步骤的批注可反馈进 learning_loop 驱动迭代。*
