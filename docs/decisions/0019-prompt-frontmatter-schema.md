# ADR 0019: Prompt frontmatter schema 规范化 + 运行时硬断言

- **Status**: Accepted
- **Date**: 2026-05-17
- **Deciders**: project lead
- **Supersedes**: 无
- **Superseded by**: 无
- **Related**: [ADR 0005](0005-nacs-legacy-asset-migration.md)（NACS 三件套 extras 来源）、
  [ADR 0009](0009-research-agent-framework-borrowing.md)（ScoreCard / WorkflowExtras 模式）、
  [PROJECT_SPEC.md §3.10](../../PROJECT_SPEC.md)（prompts/ 详解）、
  [CLAUDE.md "提示词约束"](../../CLAUDE.md)（修改提示词必须 bump version）

## Context

Phase 5 完成后（tag `v0.5`，2026-05-16），本仓库 `prompts/agents/` 7 张专家
agent 提示词卡进入稳定使用。但随着 P0/P1 提示词改进推进，frontmatter 字段
集出现三类规范问题：

### 一、字段集规范漂移

| 字段 | 来源 | 当前使用 |
|---|---|---|
| `role / version / last_updated / input_schema / output_schema` | spec §3.10 模板 | 7/7 卡都用 |
| `score_card` | ADR 0009（spec 模板没列） | 7/7 卡都用，但 spec §3.10 没规范化 |
| `inherited_inputs` | Phase 5 自创（ADR 0005 §2 + §5 引导） | 仅 3 张 NACS 卡用 |
| `precomputed_inputs` | P1 (2026-05-17) 自创 | 4 张非-NACS 卡用 |

`score_card` / `inherited_inputs` / `precomputed_inputs` 都是事实上的标准
但没文档化，新加 agent 时不知道哪些字段必填、哪些可选。

### 二、`inherited_inputs` 字段语义重载

3 张 NACS 卡的 `inherited_inputs:` 现在既包含：

- **运行时必须存在于 `ctx.extras` 的字段**（如 `regime_score / cluster_bonus_multiplier / theme_heat`） — 缺即业务逻辑断链
- **文档性的依赖引用**（如 `regulatory_regime → config/regulations/*.yaml`、`theme_history_30d → themes/history.csv`、`premium_curve → themes/premium_curve.json`） — 不在 `WorkflowExtras` 里

两种语义混在一个字段里，无法做运行时断言（如果断言 `WorkflowExtras` 必有
`theme_history_30d` 会误报）。

### 三、字段命名不严格 1:1 对应 `WorkflowExtras`

举例：
- `cornerstone.md` 列 `sponsor_track_record`（单数），`WorkflowExtras` 字段叫 `sponsor_track_records`（带 s）
- `sentiment.md` 列 `ai_gilding_signal`，`WorkflowExtras` 字段叫 `ai_gilding_flag`

这种命名漂移使得"硬断言 `inherited_inputs` 必须在 `extras` 里"无法直接落地。

### 四、缺少运行时硬断言

实际运行场景：如果 `policy_agent` 没把 `regime_score` 写到 `extras`，
后续 `sentiment_agent` / `valuation/ensemble.py` 会读到 `None`，
导致 Regime Gate 硬门失效但**没有报错**。Phase 8 回测就会拿到 NACS-degraded
信号但完全感知不到。

### 五、Pydantic ScoreCard 与 .md 手写 JSON 示例可能漂移

`scoring.py` 改了 `BaseScoreCard` 子类字段，.md 文件里的手写 JSON 示例
不会自动同步，LLM 会看到两份相互矛盾的 schema 描述。

### 六、缺少版本变更管控

CLAUDE.md 规定"修改提示词必须 bump version"，但没有 CI 强制：
PR 改了 prompt 但忘记 bump，merge 后无法从 git history 之外的渠道感知
prompt 已变。

---

## Decision

### 1. 正式定义 frontmatter schema（Pydantic 强校验）

新增 `PromptFrontmatter` Pydantic 模型在 `src/hk_ipo_agent/agents/base.py`，
`load_prompt()` 解析后必须通过校验。

**必填字段**（5 个）：

| 字段 | 类型 | 含义 |
|---|---|---|
| `role` | str | Agent role enum 值（如 `fundamental_agent`） |
| `version` | str | semver-lite，如 `"1.2"`；改 prompt 必须 bump |
| `last_updated` | date (ISO) | 修改日期 |
| `input_schema` | str | Pydantic 输入模型名（如 `AgentContext`） |
| `output_schema` | str | Pydantic 输出模型名（如 `AgentOutput`） |

**可选字段**（4 个，按规范使用）：

| 字段 | 类型 | 含义 | 校验 |
|---|---|---|---|
| `score_card` | str | `BaseScoreCard` 子类名（如 `PolicyScoreCard`） | 若设置，必须能在 `scoring.py` 找到对应类 |
| `requires_extras` | list[str] | **运行时硬断言** — 每个 key 必须是 `WorkflowExtras` 的字段名，调用 LLM 前若 `getattr(extras, key) is None` 即 raise `MissingInheritedInput` | 每个 key 必须 1:1 对应 `WorkflowExtras` 字段 |
| `inherited_inputs` | list[str] | **文档性** — 含 config 路径 / 外部文件引用等不在 extras 的依赖。仅给读者看 | 无格式约束 |
| `precomputed_inputs` | list[str] | **文档性** — 描述非 extras 的预计算输入（如财务原语、peer 分位） | 无格式约束 |

**说明**：
- `requires_extras` 是 `inherited_inputs` 字段语义重载的拆分；只有"必须在 `ctx.extras` 实际存在的字段"放这里
- 其余依赖（包括来自 config/themes/extraction 的） 放 `inherited_inputs` 或 `precomputed_inputs`，纯文档用途

### 2. 新增 `MissingInheritedInput` 异常（继承 `HkIpoAgentException`）

放 `common/exceptions.py`：

```python
class MissingInheritedInput(HkIpoAgentException):
    """Agent prompt frontmatter declared `requires_extras: [<key>]` but
    `ctx.extras.<key>` is None / missing at LLM call time.

    See ADR 0019 (frontmatter schema) + ADR 0005 §2 (NACS three-piece signals).
    """

    default_message = "Required `ctx.extras` field is missing"
```

### 3. `BaseAgent._call_llm` / `_call_llm_typed` 前置硬断言

在 `base.py` 增加 `_assert_required_extras(ctx)`，每次 LLM 调用前自动跑：
- 读取 `self._frontmatter()['requires_extras']`（cached）
- 对每个 key：若 `getattr(ctx.extras, key, None) is None` → raise `MissingInheritedInput(key=...)`

效果：缺 `regime_score` / `cluster_bonus_multiplier` / `theme_heat` 立即报错，
不再静默 degrade。

### 4. 抽取 `prompts/system/agent_common.md` 公共片段

把 7 张卡的"Citation 强制段 / 风格段"统一抽到 `prompts/system/agent_common.md`，
agent 卡用 Jinja2 `{% include "system/agent_common.md" %}` 引用。

`load_prompt()` 用 `jinja2.Environment(loader=FileSystemLoader(PROMPTS_ROOT))`
渲染 body（frontmatter 仍按原解析路径处理，不进 Jinja2 上下文）。

### 5. 加测试 `tests/unit/agents/test_prompt_schema.py`

三类断言：

1. **frontmatter schema 校验**：7 张卡都能被 `PromptFrontmatter` 模型校验通过
2. **score_card 字段集一致性**：每张卡 `# Output Schema` 节中 fenced JSON 示例的字段集，等于对应 `BaseScoreCard` 子类字段集（含 `evidence_pages` / `notes`，排除注释性字段）
3. **requires_extras 字段名 1:1 对应 `WorkflowExtras`**：每个 `requires_extras:` 列出的 key，必须是 `WorkflowExtras` 的 dataclass 字段

### 6. CI 加 prompt-version-bump check

`.github/workflows/ci.yml` 加一个 step：
- 用 `git diff origin/main...HEAD --name-only` 找 `prompts/**/*.md` 改动
- 对每个改动文件，断言 diff 包含 `^-version:` 与 `^+version:` 两行（即 version 字段被 bump）
- 失败给出明确错误信息："prompt X.md was modified but version was not bumped"

仅 PR-time 跑，不阻塞本地 commit。

---

## Consequences

**正面**：
- 运行时断链不再静默，cluster_bonus / theme_heat / regime_score 缺失立即报错
- Pydantic ScoreCard 字段变更必触发 .md 同步（schema-doc 不漂移）
- frontmatter 规范化文档化，新人写新 agent 提示词有据可循
- prompt 改动必伴随 version bump，git history 与 prompt 版本号永远一致
- 公共片段抽取后，"citation 必须非空"等规则一处改、七处生效

**负面 / 成本**：
- 当前 7 张卡的 `inherited_inputs:` 需迁移：3 张 NACS 卡的"实际 extras 字段"挪到 `requires_extras:`，文档性依赖留在 `inherited_inputs:`
- base.py 新增 ~50 行代码 + 1 个异常类
- CI 增加 ~15 行 lint step（PR-time）
- 7 张卡的 version 从 1.2 → 1.3（frontmatter 字段重组属于 prompt 修改）
- Phase 5 已有的 7 个 agent .py 实现不需要改（断言由 BaseAgent 透明加载）

**风险与缓解**：
- 风险 1：`requires_extras` 严格 1:1 校验可能误伤未来新增的 dataclass 字段。
  缓解：测试 `test_requires_extras_match_workflow_extras` 显式列举有效 key，
  WorkflowExtras 改字段名时同步更新
- 风险 2：Jinja2 include 引入新解析路径，可能跟 spec §3.10 说的"Jinja2 渲染（注入 schema、上下文）"在 Phase 6 时进一步合并。
  缓解：本 ADR 仅落 include，scoring schema 注入仍走 `scoring.schema_instruction()`，二者解耦
- 风险 3：CI version-bump check 会拒"只改 typo 不改 version"的 PR。
  缓解：明确规则——任何 prompt 改动（包括 typo 修复）都必须 bump patch 版本，
  跟 CLAUDE.md "修改提示词必须 bump version" 一致

---

## Affected files

**新增**：
- `docs/decisions/0019-prompt-frontmatter-schema.md`（本 ADR）
- `prompts/system/agent_common.md`（公共片段）
- `tests/unit/agents/test_prompt_schema.py`（3 类断言）

**修改**：
- `src/hk_ipo_agent/agents/base.py`（`PromptFrontmatter` Pydantic + Jinja2 include + `_assert_required_extras`）
- `src/hk_ipo_agent/common/exceptions.py`（新增 `MissingInheritedInput`）
- `prompts/agents/{policy,cornerstone_signal,sentiment}.md`（迁移 `inherited_inputs` → `requires_extras` + include common + bump v1.3）
- `prompts/agents/{fundamental,industry,valuation,liquidity}.md`（include common + bump v1.3）
- `tests/unit/agents/test_base.py`（version 1.2 → 1.3）
- `.github/workflows/ci.yml`（prompt-version-bump step）
- `CLAUDE.md`（启动检查 + Phase 5 行加 ADR 0019）
- `docs/decisions/README.md`（两张表加 0017 行）

---

## Progress

实施切片（一次性完成，约 90 min）：

- [x] **0017-1 ADR + 索引**：本 ADR 文件 + CLAUDE.md / README.md 更新
- [x] **0017-2 exceptions**：`MissingInheritedInput` 异常类
- [x] **0017-3 公共片段**：`prompts/system/agent_common.md`
- [x] **0017-4 base.py 改造**：`PromptFrontmatter` Pydantic + Jinja2 include + `_assert_required_extras`
- [x] **0017-5 7 张卡迁移**：`inherited_inputs` → `requires_extras` + include common + bump v1.3
- [x] **0017-6 测试**：`test_prompt_schema.py` 3 类断言 + `test_base.py` version 同步
- [x] **0017-7 CI**：`.github/workflows/ci.yml` prompt-version-bump check
- [x] **0017-8 全测**：`pytest tests/unit/agents/` 全通过 + 项目 ruff 通过

每个切片独立验证。失败则停下来报告，不假装继续。
