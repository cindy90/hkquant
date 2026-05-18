# HK IPO Cornerstone Agent — 增量规范 v1.2.2

> **本文件性质**：这是对 `PROJECT_SPEC.md` (v1.2.1) 的**增量补丁**，不是完整规范。代码库已按 v1.2.1 搭建完成，本文件描述需要**新增和修改**的部分。Claude Code 必须先完整理解现有代码库，再按本文件做增量改造。

> **本增量来源**：对开源项目 `virattt/ai-hedge-fund`（58.8k stars，多 agent 投资决策系统）和 `virattt/dexter`（金融研究 agent）的实战经验吸收。

> **两个增量**：
> - **增量 A — 投资哲学 Lens 层**：借鉴 ai-hedge-fund 的投资人格 agent，在 critic 层新增 5 个投资哲学视角（含 1 个基石专属视角），把泛泛的 Bull/Bear 辩论升级为结构化的哲学分歧。
> - **增量 B — Agent 可观测性与安全**：借鉴 Dexter 的 scratchpad 追踪和循环检测，新增 agent 级工具调用追踪 + 循环检测 + 硬步数上限。

> **优先级**：增量 A 改变决策质量（高价值）；增量 B 改变系统健壮性（高必要性）。建议先做 B（风险低、改动小、立刻提升稳定性），再做 A。

---

## 第一部分：实施总览

### 改动范围一览

| 改动类型 | 增量 A（Lens 层） | 增量 B（可观测性） |
|---|---|---|
| 新增目录 | `critic/investor_lens/` | `common/tracing/` |
| 新增文件 | 8 个 | 4 个 |
| 修改现有文件 | 5 个 | 3 个 |
| 新增数据库表 | 0（用新增列） | 2 |
| 数据库列变更 | `prediction_snapshots` +1 列 | 0 |
| 新增 Pydantic 模型 | 6 个 | 4 个 |
| 新增 prompts | 5 个 | 0 |
| 新增配置 | `config/investor_lens.yaml` | `config/agents.yaml` 扩展 |

### 对现有代码的兼容性原则

1. **数据库只做加法**：新增表、新增 nullable 列。禁止删表、禁止改现有列类型。
2. **现有快照保持有效**：增量 A 给 `prediction_snapshots` 加 nullable 列 `lens_panel_output`，老快照该列为 NULL，hash 校验必须兼容（见 A.4）。
3. **现有 orchestrator 图增加节点，不删节点**：新增 `investor_lens_panel` 节点插入 debate 之后。
4. **现有 AgentOutput / DebateOutput / FinalDecision 只做字段扩展**：新增 Optional 字段，不改已有字段语义。
5. **现有测试不应破坏**：增量改动后，现有单元/集成/e2e 测试必须仍然全过（除非测试本身需要更新断言）。

---

## 第二部分：增量 B — Agent 可观测性与安全

> 先做 B：改动小、风险低、立刻提升健壮性。借鉴 Dexter 的 scratchpad 模式和安全机制。

### B.1 设计理念

当前 v1.2.1 的 `prediction_snapshots` 记录的是**粗粒度**结果（最终 agent 输出）。但单个 agent 运行过程中调用了哪些工具、每个工具返回了什么、agent 是否陷入循环、用了多少步——这些**细粒度执行轨迹**没有被记录。

Dexter 的做法值得借鉴：每次工具调用都记录 `toolName + args + 原始结果 + LLM 摘要`，并有循环检测和硬步数上限防止失控执行。

本增量解决三个问题：
1. **可观测性**：每个 agent 的工具调用全程可追溯，便于调试和归因
2. **循环防护**：检测 agent 反复调用相同工具，及时终止
3. **成本防护**：硬步数上限防止单次 agent 运行失控烧钱

### B.2 目录结构变更

新增 `src/hk_ipo_agent/common/tracing/`：

```
src/hk_ipo_agent/common/
├── ... (现有文件)
└── tracing/                          # ★ 增量 B 新增
    ├── __init__.py
    ├── scratchpad.py                 # 工具调用追踪记录器
    ├── loop_detector.py              # 循环检测
    ├── step_limiter.py               # 硬步数上限
    └── trace_writer.py               # 追踪持久化（写 DB）
```

### B.3 新增 Pydantic Schemas

加到 `common/schemas.py`：

```python
# ===== 增量 B 新增：Agent 执行追踪 =====
class ToolCallTrace(BaseModel):
    """单次工具调用记录。借鉴 Dexter scratchpad。"""
    sequence: int                       # agent 运行内的调用序号
    tool_name: str
    tool_args: Dict[str, Any]
    tool_args_hash: str                 # args 的 SHA256，用于循环检测
    raw_result: Optional[Dict[str, Any]] = None  # 工具原始返回
    raw_result_truncated: bool = False  # 原始结果是否被截断（过大时）
    llm_summary: Optional[str] = None   # LLM 对结果的摘要（供后续步骤用，避免重读大payload）
    error: Optional[str] = None
    duration_ms: int
    cost_usd: Optional[Decimal] = None  # 如果该工具内部调了 LLM
    timestamp: datetime

class AgentExecutionTrace(BaseModel):
    """单个 agent 一次完整运行的执行轨迹。"""
    id: UUID
    snapshot_id: Optional[UUID] = None  # 关联的快照（分析完成后回填）
    ipo_id: UUID
    agent_role: AgentRole
    tool_calls: List[ToolCallTrace]
    total_steps: int
    total_cost_usd: Decimal
    total_duration_ms: int
    terminated_reason: Optional[Literal[
        "completed",           # 正常完成
        "loop_detected",       # 检测到循环
        "step_limit_hit",      # 达到硬步数上限
        "cost_limit_hit",      # 达到成本上限
        "error",               # 异常
    ]] = "completed"
    thinking_steps: List[str] = Field(default_factory=list)  # agent 推理步骤
    created_at: datetime

class LoopDetectionResult(BaseModel):
    is_loop: bool
    repeated_tool: Optional[str] = None
    repeated_args_hash: Optional[str] = None
    repeat_count: int = 0
```

**扩展现有 `AgentOutput`**（新增 Optional 字段，不破坏现有）：

```python
class AgentOutput(BaseModel):
    # ... 所有现有字段保持不变 ...
    # 增量 B 新增：
    execution_trace_id: Optional[UUID] = None  # 关联 AgentExecutionTrace
    terminated_reason: Optional[str] = None    # 冗余存储，便于快速筛查异常
```

### B.4 数据库变更

新增 2 张表（additive migration）：

```sql
-- ========== 增量 v1.2.2 增量B：Agent 执行追踪 ==========

-- Agent 执行轨迹（append-only，immutable）
CREATE TABLE agent_execution_traces (
    id UUID PRIMARY KEY,
    snapshot_id UUID REFERENCES prediction_snapshots,  -- 分析完成后回填
    ipo_id UUID REFERENCES ipo_events,
    agent_role VARCHAR(50) NOT NULL,
    total_steps INT NOT NULL,
    total_cost_usd NUMERIC,
    total_duration_ms INT,
    terminated_reason VARCHAR(30) DEFAULT 'completed',
    thinking_steps JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_agent_traces_snapshot ON agent_execution_traces (snapshot_id);
CREATE INDEX idx_agent_traces_ipo ON agent_execution_traces (ipo_id, agent_role);
CREATE INDEX idx_agent_traces_terminated ON agent_execution_traces (terminated_reason)
    WHERE terminated_reason != 'completed';  -- 快速筛查异常运行

-- 单次工具调用记录
CREATE TABLE tool_call_traces (
    id UUID PRIMARY KEY,
    execution_trace_id UUID REFERENCES agent_execution_traces ON DELETE CASCADE,
    sequence INT NOT NULL,
    tool_name VARCHAR(100) NOT NULL,
    tool_args JSONB,
    tool_args_hash VARCHAR(64),
    raw_result JSONB,
    raw_result_truncated BOOLEAN DEFAULT FALSE,
    llm_summary TEXT,
    error TEXT,
    duration_ms INT,
    cost_usd NUMERIC,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (execution_trace_id, sequence)
);
CREATE INDEX idx_tool_traces_execution ON tool_call_traces (execution_trace_id, sequence);

-- immutability 保护（复用 v1.1 已有的 prevent_snapshot_modification 函数）
CREATE TRIGGER agent_trace_no_update
    BEFORE UPDATE ON agent_execution_traces
    FOR EACH ROW EXECUTE FUNCTION prevent_snapshot_modification();
CREATE TRIGGER tool_trace_no_update
    BEFORE UPDATE ON tool_call_traces
    FOR EACH ROW EXECUTE FUNCTION prevent_snapshot_modification();
```

> 注意：`agent_execution_traces.snapshot_id` 在 agent 运行时为 NULL（此时还没创建快照），分析流程到 `create_snapshot` 节点时回填。回填是 UPDATE 操作——因此 `snapshot_id` 字段需要例外：immutability trigger 改为只在 `snapshot_id IS NOT NULL` 时拒绝 UPDATE，或用一次性回填后锁定的逻辑。**实现时用：trigger 允许 `snapshot_id` 从 NULL → 非 NULL 的单次更新，其余字段一律拒绝。**

### B.5 对现有文件的修改

#### B.5.1 `agents/base.py`（核心修改）

`BaseAgent` 必须在每次工具调用时经过追踪和安全检查。现有 `run()` 方法和 `_call_llm()` 保持，但工具调用必须包一层：

```python
class BaseAgent(ABC):
    # ... 现有属性 ...
    # 增量 B 新增：
    max_steps: int = 25                # 硬步数上限（从 config/agents.yaml 读）
    loop_detection_threshold: int = 3  # 同工具同参数重复 N 次判定循环

    async def _call_tool(self, tool_name: str, tool_args: dict, scratchpad: Scratchpad):
        """所有工具调用必须经过此方法，禁止 agent 直接调工具。"""
        # 1. 步数检查
        if scratchpad.step_count >= self.max_steps:
            raise StepLimitExceeded(self.role, self.max_steps)
        # 2. 循环检测
        loop_result = scratchpad.check_loop(tool_name, tool_args)
        if loop_result.is_loop:
            raise LoopDetected(self.role, loop_result)
        # 3. 执行工具 + 记录
        start = time.monotonic()
        try:
            raw_result = await self._execute_tool(tool_name, tool_args)
            error = None
        except Exception as e:
            raw_result, error = None, str(e)
        duration_ms = int((time.monotonic() - start) * 1000)
        # 4. 生成 LLM 摘要（供后续步骤用，避免重读大 payload）
        llm_summary = await self._summarize_tool_result(tool_name, raw_result) if raw_result else None
        # 5. 写入 scratchpad
        scratchpad.record(ToolCallTrace(...))
        if error:
            raise ToolExecutionError(tool_name, error)
        return raw_result
```

**关键约束**：
- agent 内所有工具调用必须走 `_call_tool`，不允许直接调 `tools/` 下的工具
- `LoopDetected` 和 `StepLimitExceeded` 不是致命错误：捕获后 agent 优雅终止，输出当前已有的部分结论，并在 `AgentOutput.terminated_reason` 标记原因
- 终止的 agent 输出 `overall_score` 仍可计算，但 `uncertainty_flags` 必须加一条说明"因 X 提前终止，结论可能不完整"

#### B.5.2 `common/llm_client.py`（小修改）

现有 retry / cost tracking 保持。新增：把每次 LLM 调用的 cost 关联到当前 agent 的 scratchpad（用 contextvar 传递当前 trace 上下文），以便 `total_cost_usd` 准确归集。

#### B.5.3 `orchestrator/nodes.py`（小修改）

各 agent 节点函数在调用 agent 后，把返回的 `AgentExecutionTrace` 写入 DB（通过 `trace_writer.py`）。`create_snapshot` 节点回填 `snapshot_id`。

### B.6 新增 tracing 模块说明

**`scratchpad.py`** — `Scratchpad` 类：
- 持有单次 agent 运行的所有 `ToolCallTrace`
- `record(trace)` 追加记录
- `check_loop(tool, args)` 返回 `LoopDetectionResult`
- `step_count` 属性
- 运行结束 `to_execution_trace()` 产出 `AgentExecutionTrace`
- 大 payload 处理：`raw_result` 超过阈值（建议 32KB）时截断并标 `raw_result_truncated=True`

**`loop_detector.py`** — 循环检测逻辑：
- 维护 `(tool_name, args_hash)` 计数
- 同元组出现 ≥ `loop_detection_threshold` 次 → `is_loop=True`
- 也检测"无进展循环"：连续 N 步都无新信息（可选，二期）

**`step_limiter.py`** — 步数上限：纯计数 + 阈值判断

**`trace_writer.py`** — 持久化：
- `write_trace(execution_trace)` 写 `agent_execution_traces` + `tool_call_traces`
- `backfill_snapshot_id(trace_ids, snapshot_id)` 回填

### B.7 配置变更

`config/agents.yaml` 每个 agent 配置新增字段：

```yaml
agents:
  fundamental:
    model: claude-sonnet-4
    max_tokens: 4096
    temperature: 0.3
    # 增量 B 新增：
    max_steps: 25                  # 硬步数上限
    loop_detection_threshold: 3    # 循环检测阈值
    max_cost_usd: 1.50             # 单次运行成本上限
  # ... 其余 agent 同样补充
```

### B.8 测试要求

新增单元测试：
- `tests/unit/common/test_scratchpad.py` — 记录、循环检测
- `tests/unit/common/test_loop_detector.py` — 各种循环场景
- `tests/unit/agents/test_step_limit.py` — 步数上限触发 → agent 优雅终止
- 对抗测试：构造一个会无限循环调同一工具的 mock agent，验证 `loop_detected` 在 3 步内触发并优雅终止
- 对抗测试：`tool_call_traces` / `agent_execution_traces` 的 UPDATE（除 snapshot_id 回填外）被 DB 拒绝

### B.9 DONE 条件

- 任意一次 agent 运行都产生完整 `AgentExecutionTrace` 并持久化
- 循环 agent 在阈值步数内被检测并优雅终止
- 步数 / 成本上限生效
- `snapshot_id` 回填正常
- 现有所有测试仍全过
- UI 侧（v1.3）可选：在 IPO 详情的 Audit tab 或 Analysis tab 增加"agent 执行轨迹"查看（本增量不强制 UI，但后端数据已就绪）

---

## 第三部分：增量 A — 投资哲学 Lens 层

> ai-hedge-fund 的核心可借鉴点：所有 agent 收到相同数据，但基于不同投资哲学得出完全不同结论，由 Portfolio Manager 权衡。本增量把这个思路引入 critic 层。

### A.1 设计理念

**当前 v1.2.1 的 critic 层**是 Bull / Bear / Devil's Advocate / Cross-checker——这是**功能性**角色（看多 / 看空 / 挑刺 / 历史比对）。

**问题**：功能性辩论容易流于"各打五十大板"，缺乏一致的判断框架。一个真正的投委会里，分歧往往源于**投资哲学**的根本不同，而非简单的乐观/悲观。

**本增量新增"投资哲学 Lens 层"**：5 个基于不同投资哲学的视角 agent，每个用一致的、可解释的框架评判同一个标的。**核心原则：Lens 之间的分歧本身是信号，不是噪音。**

**为什么对 18C 基石投资特别合适**：18C 拟上市公司常常未盈利、未充分商业化，恰恰是不同投资哲学会激烈分歧的标的。价值派会说"无盈利，pass"；成长派会说"颠覆性技术，participate"。这种结构化分歧能逼出比泛泛 Bull/Bear 更具体、更严格的判断。

**与现有 Bull/Bear/Devil 的关系**：不替代，而是并存。Bull/Bear/Devil 做"针对具体论点的攻防"；Lens 层做"基于哲学框架的独立评判"。两者都喂给 Synthesizer。

### A.2 五个 Lens 定义

| Lens | 哲学原型 | 评判框架 | 对 18C 的典型倾向 |
|---|---|---|---|
| **Value Lens（价值视角）** | Graham / Buffett / Munger | 已验证盈利、安全边际、可持续护城河、合理价格 | 对未盈利 18C 默认怀疑，要求下行保护 |
| **Growth Lens（成长视角）** | Cathie Wood | 大 TAM、颠覆性技术、指数级增长、品类领导力 | 18C 的天然栖息地，愿为未来付溢价 |
| **Valuation Purist Lens（估值纯粹派）** | Damodaran | 故事-数字桥、概率加权、内在价值推导 | 不站队，搭建估值桥，输出"低于 X 价格则参与" |
| **Contrarian Lens（逆向视角）** | Michael Burry | 市场忽视了什么、过度炒作、招股书埋藏的结构问题 | 审视炒作，查"老股东套现离场"信号 |
| **Cornerstone Lens（基石专属视角）★** | 无对应原型，本系统独有 | 锁定期风险、退出路径、配售经济性、机会成本 | 评判"是否好的基石交易"而非"是否好公司" |

**Cornerstone Lens 是 ai-hedge-fund 没有、本系统必须独有的**。它的核心洞察：**一家好公司可能是一笔糟糕的基石投资**——比如解禁后流动性极差无法退出、或基石实质上在补贴 IPO 发行。它专门从以下角度评判：
- 6 个月锁定期内的下行暴露（不能中途退出）
- 解禁日的潜在抛压与流动性（解禁后能否实际平仓）
- 基石配售的经济性（折价是否真实，还是只是"站台"）
- 资金机会成本（6 个月锁定 vs 其他配置）

### A.3 目录结构变更

新增 `src/hk_ipo_agent/critic/investor_lens/`：

```
src/hk_ipo_agent/critic/
├── __init__.py
├── bull.py                           # 现有
├── bear.py                           # 现有
├── devils_advocate.py                # 现有
├── cross_checker.py                  # 现有
├── debate_graph.py                   # 现有（需修改，见 A.6）
└── investor_lens/                    # ★ 增量 A 新增
    ├── __init__.py
    ├── base.py                       # InvestorLens ABC
    ├── value_lens.py
    ├── growth_lens.py
    ├── valuation_purist_lens.py
    ├── contrarian_lens.py
    ├── cornerstone_lens.py           # ★ 本系统独有
    ├── panel.py                      # Lens 面板编排（并行跑 5 个 lens + 计算分歧）
    └── divergence.py                 # 分歧度量计算
```

### A.4 数据库变更

**不新增表**，给 `prediction_snapshots` 加 1 个 nullable 列：

```sql
-- ========== 增量 v1.2.2 增量A：投资 Lens 面板 ==========
ALTER TABLE prediction_snapshots ADD COLUMN lens_panel_output JSONB;
-- 老快照该列为 NULL，正常。新快照写入完整 LensPanelResult。
```

**重要——快照 hash 兼容性**：

v1.1 的 `prediction_snapshots.input_data_hash` 覆盖 `(input_data_snapshot + agent_outputs + valuation + debate + decision)`。增量 A 后，新快照多了 `lens_panel_output`。

`snapshot.py` 的 hash 计算函数必须修改为：
```python
def compute_snapshot_hash(snapshot_data: dict) -> str:
    # 增量 A：lens_panel_output 仅在存在时纳入 hash
    components = [
        snapshot_data["input_data_snapshot"],
        snapshot_data["agent_outputs"],
        snapshot_data["valuation_output"],
        snapshot_data["debate_output"],
        snapshot_data["decision"],
    ]
    if snapshot_data.get("lens_panel_output") is not None:
        components.append(snapshot_data["lens_panel_output"])
    return sha256(canonical_json(components))
```

这样：老快照（无 lens 数据）hash 不变、校验仍通过；新快照（有 lens 数据）hash 包含 lens。**同时给 `prediction_snapshots` 加一个 `snapshot_schema_version VARCHAR(10) DEFAULT 'v1'` 列，新快照标 `'v2'`**，便于未来归因和审计区分。

```sql
ALTER TABLE prediction_snapshots ADD COLUMN snapshot_schema_version VARCHAR(10) DEFAULT 'v1';
```

### A.5 新增 Pydantic Schemas

加到 `common/schemas.py`：

```python
# ===== 增量 A 新增：投资哲学 Lens 层 =====
class InvestorLensType(str, Enum):
    VALUE = "value"
    GROWTH = "growth"
    VALUATION_PURIST = "valuation_purist"
    CONTRARIAN = "contrarian"
    CORNERSTONE = "cornerstone"

class LensVerdict(str, Enum):
    STRONG_PARTICIPATE = "strong_participate"
    PARTICIPATE = "participate"
    CONDITIONAL = "conditional"          # 有条件参与（通常带价格条件）
    PASS = "pass"
    STRONG_PASS = "strong_pass"

class InvestorLensVerdict(BaseModel):
    """单个 Lens 对标的的评判。"""
    lens_type: InvestorLensType
    verdict: LensVerdict
    conviction: float                    # 0-1，该 lens 对自己判断的确信度
    # 该哲学框架下的核心论点
    thesis: str                          # 一句话核心判断
    key_arguments: List[str]             # 支撑论点
    deal_breakers: List[str]             # 该哲学视角下的致命问题（可能为空）
    # 价格相关（conditional verdict 必填）
    price_condition: Optional[Decimal] = None  # "低于此价格才参与"
    fair_value_estimate: Optional[ValuationDistribution] = None
    citations: List[Citation]
    cost_usd: Decimal
    runtime_seconds: float

class LensDivergence(BaseModel):
    """5 个 Lens 之间的分歧度量。"""
    divergence_score: float              # 0-1，0=完全一致，1=极端分歧
    verdict_spread: int                  # verdict 跨越的档位数（0-4）
    consensus_verdict: Optional[LensVerdict] = None  # 若收敛则为共识结论
    is_philosophy_dependent: bool        # True = 决策高度依赖哲学立场
    most_bullish_lens: InvestorLensType
    most_bearish_lens: InvestorLensType
    key_disagreement: str                # 分歧的核心所在（自然语言）

class LensPanelResult(BaseModel):
    """投资 Lens 面板的完整输出。"""
    lens_verdicts: List[InvestorLensVerdict]  # 5 个 lens
    divergence: LensDivergence
    # 给 Synthesizer 的结构化建议
    panel_summary: str
    cornerstone_specific_flags: List[str]  # Cornerstone Lens 专门提出的、其他 lens 不会覆盖的点
    total_cost_usd: Decimal
    total_runtime_seconds: float
```

**扩展现有 schemas**（新增 Optional 字段，不破坏现有）：

```python
class PredictionSnapshot(BaseModel):
    # ... 所有现有字段保持不变 ...
    # 增量 A 新增：
    lens_panel_output: Optional[LensPanelResult] = None
    snapshot_schema_version: str = "v1"  # 新快照设为 "v2"

class FinalDecision(BaseModel):
    # ... 所有现有字段保持不变 ...
    # 增量 A 新增：
    lens_divergence_score: Optional[float] = None  # 引用 LensPanelResult.divergence
    philosophy_dependent: Optional[bool] = None    # 决策是否高度依赖哲学立场
    lens_verdicts_summary: Optional[Dict[str, str]] = None  # {lens_type: verdict} 速查
```

### A.6 对现有文件的修改

#### A.6.1 `orchestrator/states.py`（小修改）

`AnalysisState` 新增字段：
```python
class AnalysisState(...):
    # ... 现有字段 ...
    lens_panel_result: Optional[LensPanelResult] = None  # 增量 A 新增
```

#### A.6.2 `orchestrator/graph.py`（核心修改）

现有图：
```
... → valuation → debate → cross_check → synthesize → ...
```

增量后：
```
... → valuation → debate → investor_lens_panel → cross_check → synthesize → ...
```

新增节点 `investor_lens_panel`，插在 `debate` 之后、`cross_check` 之前。理由：Lens 可以同时参考 7 个专家 agent 的输出、估值结果、以及 Bull/Bear 辩论的论点，做最全面的哲学评判。

```python
# graph.py 修改
g.add_node("investor_lens_panel", investor_lens_panel_node)
# 删除原边: g.add_edge("debate", "cross_check")
g.add_edge("debate", "investor_lens_panel")
g.add_edge("investor_lens_panel", "cross_check")
```

#### A.6.3 `orchestrator/nodes.py`（新增节点函数）

新增 `investor_lens_panel_node`：调用 `critic/investor_lens/panel.py`，5 个 lens 并行运行，产出 `LensPanelResult` 写入 state。

#### A.6.4 `synthesizer/synthesizer.py`（核心修改）

Synthesizer 现在多一个输入：`LensPanelResult`。决策逻辑必须升级：

1. **分歧高时降置信度**：`divergence_score` 高（建议 > 0.6）→ `FinalDecision.confidence` 相应下调，`philosophy_dependent=True`
2. **不做简单平均**：禁止把 5 个 lens 的 verdict 算术平均成结论。分歧大时，要在备忘录里**明确呈现分歧**，而不是抹平
3. **Cornerstone Lens 有否决性权重**：如果 Cornerstone Lens 给出 `deal_breakers`（如"解禁后无流动性"），即便其他 lens 都看好，Synthesizer 也必须显著下调决策或转为 `PARTIAL` —— 因为基石投资的本质约束（锁定期、退出）是不可绕过的
4. **共识时提升置信度**：5 个 lens 收敛 → 置信度上调
5. `FinalDecision` 填充新增的 `lens_divergence_score`、`philosophy_dependent`、`lens_verdicts_summary`

#### A.6.5 `synthesizer/scoring.py`（小修改）

风险评分卡新增一个维度："哲学分歧度"——把 `LensDivergence` 纳入评分卡展示。

#### A.6.6 `reporting/templates/investment_memo.md.j2`（修改模板）

投决备忘录新增一个章节："投资哲学视角"——逐个展示 5 个 lens 的 verdict + thesis + deal breakers，并明确标注分歧所在。这是机构级 IC paper 的标准做法（呈现异议）。

#### A.6.7 `prediction_registry/attribution.py`（修改）

归因引擎的"辩论质量层"扩展为也分析 Lens 层：事后看，哪个 lens 的判断最准？某类公司上，是不是 Growth Lens 系统性过乐观 / Value Lens 系统性过保守？这为 learning_loop 提供新的校准维度。

#### A.6.8 `learning_loop/` （小修改）

`drift_detector.py` 新增一类 drift 信号：`lens_calibration_drift`——某个 lens 在某类公司上系统性失准。`adjustment_proposer.py` 可提议调整某个 lens 的 prompt 或在 Synthesizer 中的权重。

### A.7 新增 prompts

新增 `prompts/investor_lens/`：

```
prompts/
├── ... (现有)
└── investor_lens/                    # ★ 增量 A 新增
    ├── value_lens.md
    ├── growth_lens.md
    ├── valuation_purist_lens.md
    ├── contrarian_lens.md
    └── cornerstone_lens.md
```

每个 prompt 的写作要求：
- frontmatter 同现有 prompts 规范（role / version / input_schema / output_schema）
- 必须明确该投资哲学的**评判框架**和**红线**
- 必须强制 LLM 输出 `InvestorLensVerdict` JSON
- **关键**：每个 lens 的 prompt 必须让 LLM **坚定地从该哲学立场出发**，不要试图"平衡"——平衡是 Synthesizer 的工作，不是单个 lens 的工作。Value Lens 就应该像个严格的价值投资者，Growth Lens 就应该像个坚定的成长投资者。分歧是设计目标。
- Cornerstone Lens 的 prompt 必须聚焦基石投资的结构性约束（锁定期、退出、配售经济性），不要重复其他 lens 已覆盖的基本面分析

### A.8 配置变更

新增 `config/investor_lens.yaml`：

```yaml
lenses:
  value:
    model: claude-sonnet-4
    temperature: 0.3
    max_steps: 15
  growth:
    model: claude-sonnet-4
    temperature: 0.4
    max_steps: 15
  valuation_purist:
    model: claude-opus-4-7      # 估值纯粹派需要更强推理
    temperature: 0.2
    max_steps: 20
  contrarian:
    model: claude-sonnet-4
    temperature: 0.5            # 逆向视角需要更发散
    max_steps: 15
  cornerstone:
    model: claude-sonnet-4
    temperature: 0.3
    max_steps: 15

# Synthesizer 如何权衡 lens（起点值，后续由 learning_loop 校准）
synthesizer_weights:
  # Cornerstone Lens 的 deal_breaker 有否决性，不在此权重内单独处理
  divergence_confidence_penalty: 0.3   # divergence_score=1 时置信度最多下调 30%
  consensus_confidence_bonus: 0.15
  philosophy_dependent_threshold: 0.6  # divergence_score 超过此值标记 philosophy_dependent
```

### A.9 测试要求

- `tests/unit/critic/investor_lens/` — 每个 lens 独立测试（mock LLM）
- `tests/unit/critic/test_divergence.py` — 分歧度量计算
- `tests/unit/synthesizer/test_lens_integration.py` — Synthesizer 对高分歧 / 低分歧 / Cornerstone deal-breaker 三种情况的处理
- **黄金回归测试**：用一家已上市的 18C 公司（如黑芝麻智能，上市后跌了），验证 Lens 层是否会产生有意义的分歧——理想情况：Value/Contrarian Lens 当时应给出 PASS 或 deal-breaker，事后看是对的
- e2e：完整跑一次分析，验证 `lens_panel_output` 正确写入快照、备忘录含投资哲学章节

### A.10 DONE 条件

- 5 个 lens 全部实现，每个能独立输出 `InvestorLensVerdict`
- `investor_lens_panel` 节点接入 orchestrator 图，并行运行 5 个 lens
- `LensPanelResult` 正确写入 `prediction_snapshots.lens_panel_output`
- 老快照 hash 校验仍通过（schema_version='v1'），新快照 schema_version='v2'
- Synthesizer 正确处理高分歧场景（降置信度 + philosophy_dependent）和 Cornerstone deal-breaker（否决性下调）
- 投决备忘录含"投资哲学视角"章节
- 现有所有测试仍全过

---

## 第四部分：对主 SPEC 的章节修订映射

如果你要把本增量正式合并回 `PROJECT_SPEC.md` 升级为 v1.2.2，以下章节需要更新：

| 主 SPEC 章节 | 需要的修改 |
|---|---|
| 顶部版本 banner | 加 v1.2.2 说明行 |
| §2 目录结构 | `critic/` 下加 `investor_lens/` 子树；`common/` 下加 `tracing/` 子树；`prompts/` 下加 `investor_lens/` |
| §3.x 文件职责 | 新增 §3.13 描述 investor_lens；新增 §3.14 描述 tracing |
| §4 Phase 6 | "编排 + Critic + Synthesizer" 的 deliverables 加入 investor_lens 5 个 lens + panel |
| §4 Phase 5 | Agent 层 deliverables 加入 base.py 的 tracing 集成 |
| §5 数据库 Schema | 加 `agent_execution_traces`、`tool_call_traces` 2 表 + `prediction_snapshots` 2 个新列 |
| §6 Pydantic Schemas | 加 10 个新模型（A 的 6 个 + B 的 4 个）；扩展 `AgentOutput`、`PredictionSnapshot`、`FinalDecision` |
| §7 Agent 设计规范 | `BaseAgent` 增加 `_call_tool` / `max_steps` / 循环检测说明 |
| §8 LangGraph 编排 | 主图加 `investor_lens_panel` 节点 |
| §11 CLAUDE.md | 加"Lens 层约束"和"可观测性约束"（见第五部分） |
| §12 风险矩阵 | 加 3 条风险（见第五部分） |
| §13 DoD | 加增量 A / B 的验收标准 |

---

## 第五部分：CLAUDE.md 增量约束

在 `CLAUDE.md` 中新增以下两节：

```markdown
## 投资 Lens 层约束（v1.2.2 增量 A）

- 每个 Lens 必须坚定地从其投资哲学立场出发，禁止在单个 Lens 内部"自我平衡"。平衡是 Synthesizer 的职责
- Lens 之间的分歧是设计目标，不是 bug。禁止为了"让结论好看"而调 prompt 使 Lens 趋同
- Synthesizer 禁止对 5 个 Lens 的 verdict 做简单算术平均。高分歧必须在备忘录中明确呈现
- Cornerstone Lens 的 deal_breaker 有否决性权重：即使其他 Lens 都看好，出现基石结构性 deal-breaker（锁定期/退出/流动性）也必须显著下调决策
- Lens 层不替代 Bull/Bear/Devil，两者并存
- 新快照的 snapshot_schema_version 必须标 'v2'；hash 计算必须兼容老快照（lens_panel_output 为 NULL 时不纳入 hash）

## Agent 可观测性约束（v1.2.2 增量 B）

- agent 内所有工具调用必须经过 BaseAgent._call_tool，禁止直接调用 tools/ 下的工具
- 每次 agent 运行必须产生完整 AgentExecutionTrace 并持久化
- 循环检测和步数上限触发时，agent 必须优雅终止（输出部分结论 + uncertainty_flag），不是抛致命异常
- agent_execution_traces / tool_call_traces 是 append-only，除 snapshot_id 一次性回填外禁止 UPDATE
- 工具原始结果过大（>32KB）必须截断并标记，同时保留 LLM 摘要供后续步骤使用
- max_steps / loop_detection_threshold / max_cost_usd 必须从 config/agents.yaml 读取，禁止硬编码
```

新增 §12 风险矩阵 3 条：

| 风险 | 防范 |
|---|---|
| **Lens 趋同失去价值（增量 A）** | prompt 强制坚定立场；定期检查 divergence 分布，长期全部收敛说明 prompt 出了问题 |
| **Synthesizer 抹平分歧（增量 A）** | 禁止算术平均；备忘录强制含分歧呈现章节；高分歧强制 philosophy_dependent 标记 |
| **agent 执行轨迹表膨胀（增量 B）** | tool_call_traces 按时间分区；超过 N 个月的 trace 归档到 cold storage（保留 execution_trace 汇总，明细归档） |

---

## 第六部分：实施顺序与给 Claude Code 的指令

### 推荐实施顺序

1. **先做增量 B**（2-3 天）：风险低、改动小、立刻提升健壮性。先有可观测性，后续做增量 A 时调试也更容易。
2. **再做增量 A**（3-4 天）：在 B 的基础上，新增 Lens 层。

### 增量 B 实施步骤

1. 新增 `common/tracing/` 4 个文件
2. DB migration：加 `agent_execution_traces`、`tool_call_traces` 2 表 + triggers
3. 扩展 `common/schemas.py`（4 个新模型 + 扩展 AgentOutput）
4. 修改 `agents/base.py`（`_call_tool` + 安全检查）
5. 修改 `common/llm_client.py`（cost 关联）
6. 修改 `orchestrator/nodes.py`（写 trace + 回填 snapshot_id）
7. 扩展 `config/agents.yaml`
8. 写测试 → 跑全量测试确认现有测试不破

### 增量 A 实施步骤

1. 新增 `critic/investor_lens/` 8 个文件
2. DB migration：`prediction_snapshots` 加 2 列
3. 修改 `snapshot.py` 的 hash 函数（兼容老快照）
4. 扩展 `common/schemas.py`（6 个新模型 + 扩展 3 个现有模型）
5. 新增 `prompts/investor_lens/` 5 个 prompt
6. 新增 `config/investor_lens.yaml`
7. 修改 `orchestrator/states.py`、`graph.py`、`nodes.py`
8. 修改 `synthesizer/synthesizer.py`、`scoring.py`
9. 修改 `reporting/templates/investment_memo.md.j2`
10. 修改 `prediction_registry/attribution.py`、`learning_loop/drift_detector.py`
11. 写测试（含黄金回归）→ 跑全量测试

### 给 Claude Code 的指令

1. **首先**：完整阅读现有代码库，确认理解 v1.2.1 的实际实现（不要假设代码和 SPEC 完全一致，以实际代码为准）。
2. **先增量 B，后增量 A**，每个增量完成后停下来等人工确认。
3. **每一步 DB migration 必须是 additive**：只加表、加列、加 trigger。任何 `DROP` / `ALTER COLUMN TYPE` 都必须停下来问。
4. **改动现有文件前先确认现有测试全过**，改动后再次跑全量测试，确保没破坏。
5. **现有快照必须保持可校验**：增量 A 的 hash 兼容性是硬要求，实现后必须用一个老快照验证 hash 仍通过。
6. **遇到现有代码与本增量 SPEC 冲突的情况**（比如现有 BaseAgent 的工具调用方式和本文假设不同），停下来说明冲突，给出适配方案，等确认。
7. 增量完成后，更新 `PROJECT_SPEC.md` 顶部 banner 和版本 footer 为 v1.2.2，并按第四部分的映射表更新对应章节。

---

*Increment Version: 1.2.2*  
*Base: PROJECT_SPEC.md v1.2.1*  
*Last Updated: 2026-05-17*  
*来源: 借鉴 virattt/ai-hedge-fund（投资哲学 Lens 层）和 virattt/dexter（Agent 可观测性与安全）*  
*改动性质: 对已搭建代码库的增量补丁，非完整重写。增量 A = 投资哲学 Lens 层；增量 B = Agent 执行追踪 + 循环检测 + 步数上限。*
