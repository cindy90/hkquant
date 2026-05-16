# ADR 0010: Phase 6 编排 — 辩论早停 + Snapshot 创建发位

- **Status**: Accepted
- **Date**: 2026-05-16
- **Deciders**: project lead
- **Supersedes**: 无
- **Superseded by**: 无

## Context

Phase 6 编排层 (LangGraph) + Critic 辩论 + Synthesizer 决策 + Prediction Registry snapshot 创建有以下 spec 未明确的小决策：

1. **辩论收敛策略**：PROJECT_SPEC.md §3.8 / §8 描述了 `debate (Bull/Bear/Devil 子图)` 但未规定停止条件。需要决定：
   - 固定轮数（如 3 轮）？
   - 共识相似度阈值（Jaccard / cosine）？
   - LLM-as-judge 判定是否收敛？
2. **Devil's Advocate 角色定位**：spec §6 `DebateRound` 有 `bull_argument / bear_argument / devil_challenge / resolution` 四字段，但 devil 与 bear 都是"找问题"，职责差异需要明确。
3. **Snapshot 写入路径**：CLAUDE.md 强约束"任何完整分析必须先创建 snapshot 才能输出决策"，但 Phase 6 阶段 PostgreSQL `prediction_snapshots` 表 + DB trigger 在 Phase 7.5 才完整。Phase 6 需要什么程度的 snapshot 实现？
4. **HITL 中断点位**：spec 说 `synthesize → hitl → report`，但 dev/test 环境每次都中断不现实。需要决策 hitl 是否可配置 bypass。
5. **状态合并 reducer**：spec §8.2 说"并行 agent 输出必须使用 `Annotated[Dict, operator.update]` 或显式 reducer"，但 7 个 agent 用同一字段 `agent_outputs: dict[AgentRole, AgentOutput]` 时具体 reducer 写法？

## Decision

### 1. 辩论收敛 — Jaccard 相似度早停 + 最大 3 轮硬上限

参考 ADR 0009 借鉴港股研究agent `debate.py` 策略：

- 每轮辩论后,提取 Bull / Bear 论点的关键词集合（Jaccard 用 token 集）
- 当 ``jaccard(bull_tokens, bear_tokens) > 0.6`` 且 ≥1 轮已完成 → 收敛,提前结束
- 否则进入下一轮（最多 3 轮）
- ``resolution`` 字段由 Devil's Advocate 在最后一轮 challenge 后由 Bull/Bear 任一方提出（spec 字段允许 None）

**Rationale**: 港股研究agent 实证显示 60% 项目在第 2 轮即收敛,平均 2.3 轮,既保证质量又控制 token。

### 2. Devil's Advocate 角色 — 元层质疑（不站队）

明确职责分工:
- **Bull**: 找 IPO 的所有积极理由
- **Bear**: 找 IPO 的所有风险 / 反对理由
- **Devil's Advocate**: **质疑双方的论据是否站得住脚** — 例如"Bear 引用的客户集中度数据是否对该商业模式真的不利？Bull 引用的 TAM 数据来源是否可信？"

Devil 不输出"对 IPO 的判断"，输出"对 Bull/Bear 论据的元层 challenge"。

### 3. Snapshot — Phase 6 用 in-memory + hash 验证，DB 持久化推到 Phase 7.5

- `prediction_registry/snapshot.py` 实装 `PredictionSnapshot` 构造 + SHA256 hash 计算 + 不可变性 Pydantic 强制（`FrozenModel`，spec §6 已定义）
- `prediction_registry/registry.py` 仅提供 in-memory store (`dict[UUID, PredictionSnapshot]`) + `create_snapshot()` API
- DB trigger / `prediction_snapshots` 表 / PostgresSaver 在 Phase 7.5 完整实施
- Phase 6 编排已强制走 `synthesize → create_snapshot → report` 顺序,Phase 7.5 只需替换底层存储,不改 graph

### 4. HITL — 可配置 bypass（`Settings.orchestrator.enable_hitl: bool = False` 默认）

- 生产环境 (`HK_IPO__ORCHESTRATOR__ENABLE_HITL=true`) 必须在 `synthesize` 后中断,等待人工确认
- dev/test/CI 默认 bypass（`enable_hitl=False`）
- 在 `hitl.py` 实装 `should_interrupt(state) -> bool`,由 conditional edge 决定走 `report` 还是 `wait_for_human`
- 配置改动必须 ADR — 本 ADR 0010 即为此 ADR

### 5. State reducer — `operator.or_` (dict merge)

- `agent_outputs: Annotated[dict[AgentRole, AgentOutput], operator.or_]`
- LangGraph 自动用 `|` 操作合并 7 个 fanout agent 的输出（每个 agent 返回 `{"agent_outputs": {self.role: output}}`）
- `extras` 也用 `operator.or_` (字典层面 merge); 字段冲突最后一个 wins (没有 NACS 信号同时由两个 agent 写，不会冲突)
- 单一字段如 `valuation_output`, `debate_output`, `decision` 用默认 reducer（覆盖）

## Consequences

### Positive
- 辩论早停: 平均节省 30% LLM token + 30% 时长（实证 from 港股研究agent）
- Devil 元层质疑减少 Bull/Bear 双方"鸡同鸭讲"的可能,提升辩论质量
- Snapshot 接口 Phase 6 落定,Phase 7.5 仅替换底层不改 graph
- HITL bypass 让 CI 跑通,生产仍可强制开
- `operator.or_` 是 Python 3.9+ 字典原生合并语义,LangGraph 兼容性好

### Negative
- Jaccard token 集相似度对中文需要先分词 → Phase 6 用简化的 char-level Jaccard（不分词）,Phase 8 calibration 可升级
  - **Mitigation**: 阈值 0.6 经验定;Phase 8 用回测样本调优
- In-memory snapshot 在多 worker 部署时不共享 → Phase 7.5 之前生产部署只能单进程
  - **Mitigation**: ADR 中已声明 Phase 7.5 替换;Phase 6 not production-ready 不矛盾
- 默认 HITL bypass 意味着 dev 环境不会"自动暴露 hitl 钩子坏掉"
  - **Mitigation**: `tests/unit/orchestrator/test_hitl.py` 强制 `enable_hitl=True` 跑一次中断路径

### Neutral
- 辩论 LLM 模型: Bull/Bear 用 Sonnet,Devil 用 Sonnet,Synthesizer 用 Opus（spec §1 已规定 Opus 4.7 for Synthesizer）
- 主图节点顺序严格按 spec §3.8 + §8.1: `ingest → extract → 7 agents fanout → valuation → debate → cross_check → synthesize → create_snapshot → hitl(maybe) → report`

## Progress

- [x] **现在**: 本 ADR 0010 写就
- [x] **Phase 6 (2026-05-16)**: `critic/debate_graph.py` Jaccard 早停 + 3 轮上限（char-level CJK tokenizer）
- [x] **Phase 6 (2026-05-16)**: `critic/{bull,bear,devils_advocate}.py` 三角色；Bear 强制注入 Regime Gate 提示
- [x] **Phase 6 (2026-05-16)**: `critic/cross_checker.py` 确定性历史样本统计
- [x] **Phase 6 (2026-05-16)**: `synthesizer/*` 5 模块；硬规则（regime/no_models/gilding+narrative_risk）不可被 LLM 覆盖
- [x] **Phase 6 (2026-05-16)**: `prediction_registry/{snapshot,registry}.py` in-memory store + SHA-256 完整性
- [x] **Phase 6 (2026-05-16)**: `orchestrator/states.py` `operator.or_` + 自定义 `_merge_extras` reducer
- [x] **Phase 6 (2026-05-16)**: `orchestrator/{nodes,edges,graph,hitl,checkpoint}.py` 主图 13 nodes 编译成功
- [x] **Phase 6 (2026-05-16)**: `prompts/debate/*` + `prompts/system/{synthesizer,orchestrator}.md` v1.0
- [x] **Phase 6 (2026-05-16)**: `tests/unit/{critic,synthesizer,prediction_registry,orchestrator}/` 69 新单测 + 1 DONE-condition full pipeline
- [ ] **Phase 7.5**: 替换 in-memory snapshot 为 PostgreSQL + DB trigger
