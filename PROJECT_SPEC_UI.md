# HK IPO Cornerstone Agent — UI 工作台规范 v1.3

> **本文件用途**：作为 Claude Code 构建 UI 工作台的严格指令文档。本文件是 `PROJECT_SPEC.md` (v1.2 后端规范) 的姊妹文档，专注前端。两者冲突时，本文件优先级高于主 spec 的 UI 相关章节。

> **定位**：UI 不是 nice-to-have，是机构级运营的最低要求。当前 v1.2 设计中所有 review、警报响应、调整批准都依赖 CLI 脚本——这在生产环境无法持续。本规范将系统从"脚本+人工拼接"升级为"专业操作工作台"。

> **关键设计原则**：
> 1. **机构级专业感**：Bloomberg / Refinitiv Eikon / BlackRock Aladdin 风格的信息密度和操作效率，而非消费级 SaaS 风格
> 2. **决策辅助而非取代**：所有关键操作都是 reviewer 主动确认，UI 提供信息密度和决策效率，不代用户做决定
> 3. **后端为权威**：UI 仅是 thin client，所有业务逻辑在后端；UI 失败不能丢数据
> 4. **审计友好**：每个写操作都自动记录操作者、时间戳、IP、操作前后状态
> 5. **可观测优先**：每个数据点都可溯源到原始数据/快照/招股书页码

---

## 0. UI 系统使命

构建一个**专业投资工作台**，让投资分析师（Reviewer）和 IC 成员能够：

1. **每日登录 5 分钟内**掌握所有 active 预测的最新状态、待办事项、关键警报
2. **单 IPO 深度分析**：查看完整 7 agent 输出、估值分布、辩论记录、招股书引用，并可与 agent 对话深挖
3. **结构化 review**：在 checkpoint 触发时高效完成事后审查，签字归档
4. **批准 learning loop 提议**：可视化看到 proposed adjustment 的 diff、证据、预期影响，一键 accept/reject
5. **组合层视野**：跨多个 active 基石的暴露、解禁日历、相关性
6. **实时响应警报**：critical alert 4h 内响应，移动端友好
7. **完整审计回溯**：任何决策都可向上追溯到原始数据，向下追踪到事后结果

---

## 1. 技术栈（必须严格使用）

| 类别 | 选型 | 版本 | 备注 |
|---|---|---|---|
| 框架 | Next.js | 15+ (App Router) | RSC 优先；客户端组件用 'use client' 显式标注 |
| 语言 | TypeScript | 5+ | strict mode；禁止 `any` |
| 包管理 | pnpm | latest | monorepo 友好；workspace 支持 |
| UI 组件 | shadcn/ui | latest | 直接 copy 而非依赖；可定制 |
| 样式 | Tailwind CSS | 4 | with @apply 限制使用 |
| 金融图表 | **Tremor** | 3+ | 主力，专为金融 dashboard 设计 |
| 通用图表 | Recharts | 2+ | Tremor 不够时补充 |
| 价格图表 | TradingView Lightweight Charts | latest | K 线、价格走势 |
| 表单 | react-hook-form + zod | latest | 类型安全表单 |
| 数据获取 | TanStack Query | 5+ | 缓存、重试、optimistic update |
| 状态管理 | Zustand | latest | 全局轻量状态；TanStack Query 负责服务端状态 |
| GraphQL（可选） | Apollo Client / urql | latest | 复杂查询用，REST 不够时启用 |
| 实时通信 | Server-Sent Events (主) + WebSocket (备) | - | SSE 简单，WebSocket 用于需要双向的场景 |
| 认证 | Auth.js (NextAuth v5) | latest | 集成 SSO（Okta/Azure AD） |
| 表格 | TanStack Table | 8+ | 复杂表格（虚拟滚动、排序、筛选） |
| 富文本 | Tiptap | latest | review notes 编辑 |
| 日期 | date-fns | latest | 禁用 moment.js |
| 数学/金融计算 | mathjs + 自建 utils | - | 客户端仅做展示计算，业务计算在后端 |
| 国际化 | next-intl | latest | 中英双语切换 |
| 测试 | Vitest + Playwright | latest | 单元 + e2e |
| 代码质量 | Biome（替代 ESLint+Prettier） | latest | 性能更好，配置更简单 |
| 类型生成 | openapi-typescript | latest | 从后端 OpenAPI 自动生成 API 类型 |
| 监控 | Sentry | latest | 错误追踪 |
| 分析 | PostHog | latest | UX 分析（机构内部使用，需评估合规） |

**禁止**：MUI、Ant Design、Chakra UI（风格不符合机构级专业感）；styled-components / emotion（用 Tailwind）；Redux / MobX（用 Zustand + TanStack Query）；Moment.js / jQuery / Bootstrap。

---

## 2. 完整目录结构

```
hk-ipo-cornerstone-ui/                       # 独立项目（不在主 backend repo 内）
├── README.md
├── CLAUDE.md                                # UI 专属 Claude Code 规则
├── PROJECT_SPEC_UI.md                       # 本文件
├── package.json
├── pnpm-lock.yaml
├── tsconfig.json
├── next.config.ts
├── tailwind.config.ts
├── biome.json
├── .env.example
├── .gitignore
├── playwright.config.ts
├── vitest.config.ts
├── docker-compose.yml                       # 仅 dev 用
├── Dockerfile                               # 生产部署
│
├── docs/
│   ├── DESIGN_SYSTEM.md                     # 颜色、字体、间距
│   ├── COMPONENT_LIBRARY.md                 # 组件清单
│   ├── INTERACTION_PATTERNS.md              # 交互模式
│   ├── KEYBOARD_SHORTCUTS.md                # 快捷键
│   ├── ACCESSIBILITY.md                     # a11y 要求
│   └── decisions/                           # UI ADR
│
├── public/
│   ├── fonts/                               # 自托管字体（含中文）
│   └── images/
│
├── src/
│   ├── app/                                 # Next.js App Router
│   │   ├── layout.tsx                       # 根布局（providers、auth、theme）
│   │   ├── page.tsx                         # 首页 → 重定向到 /dashboard
│   │   ├── globals.css
│   │   ├── error.tsx                        # 错误边界
│   │   ├── not-found.tsx
│   │   │
│   │   ├── (auth)/                          # 认证组（无主侧栏）
│   │   │   ├── layout.tsx
│   │   │   ├── login/page.tsx
│   │   │   └── sso-callback/page.tsx
│   │   │
│   │   ├── (workbench)/                     # 主工作台组（带侧栏）
│   │   │   ├── layout.tsx                   # 工作台布局
│   │   │   │
│   │   │   ├── dashboard/
│   │   │   │   └── page.tsx                 # ★ 主控台
│   │   │   │
│   │   │   ├── ipo/
│   │   │   │   ├── page.tsx                 # IPO 列表
│   │   │   │   └── [ipoId]/
│   │   │   │       ├── layout.tsx           # IPO 详情布局（顶部 tab）
│   │   │   │       ├── page.tsx             # 默认 → analysis
│   │   │   │       ├── analysis/
│   │   │   │       │   └── page.tsx         # 完整分析报告
│   │   │   │       ├── lifecycle/
│   │   │   │       │   └── page.tsx         # 状态机时间线
│   │   │   │       ├── outcomes/
│   │   │   │       │   └── page.tsx         # checkpoint 表现
│   │   │   │       ├── chat/
│   │   │   │       │   └── page.tsx         # 与 agent 对话
│   │   │   │       ├── prospectus/
│   │   │   │       │   └── page.tsx         # 招股书原文 + 抽取对照
│   │   │   │       └── audit/
│   │   │   │           └── page.tsx         # 操作日志
│   │   │   │
│   │   │   ├── reviews/
│   │   │   │   ├── page.tsx                 # 待 review 列表
│   │   │   │   └── [reviewId]/
│   │   │   │       └── page.tsx             # ★ Review 工作台
│   │   │   │
│   │   │   ├── portfolio/                   # 组合视图（v1.3 占位，v2.0 实现）
│   │   │   │   └── page.tsx
│   │   │   │
│   │   │   ├── alerts/
│   │   │   │   ├── page.tsx                 # 警报中心
│   │   │   │   └── [alertId]/
│   │   │   │       └── page.tsx
│   │   │   │
│   │   │   ├── learning/
│   │   │   │   ├── proposals/
│   │   │   │   │   ├── page.tsx             # 待批调整列表
│   │   │   │   │   └── [proposalId]/
│   │   │   │   │       └── page.tsx         # ★ 调整批准
│   │   │   │   ├── drift/
│   │   │   │   │   └── page.tsx             # Drift 可视化
│   │   │   │   ├── attribution/
│   │   │   │   │   └── page.tsx             # 跨样本归因分析
│   │   │   │   └── history/
│   │   │   │       └── page.tsx             # 历史调整效果
│   │   │   │
│   │   │   ├── backtest/
│   │   │   │   ├── page.tsx                 # 回测列表
│   │   │   │   └── [runId]/
│   │   │   │       └── page.tsx             # 回测结果
│   │   │   │
│   │   │   ├── system/                      # 系统健康
│   │   │   │   ├── page.tsx                 # 调度器状态、数据源、成本
│   │   │   │   ├── schedulers/page.tsx
│   │   │   │   ├── data-sources/page.tsx
│   │   │   │   └── costs/page.tsx
│   │   │   │
│   │   │   ├── settings/
│   │   │   │   ├── page.tsx
│   │   │   │   ├── config/page.tsx          # YAML 配置编辑器
│   │   │   │   ├── prompts/                 # 提示词管理
│   │   │   │   │   ├── page.tsx
│   │   │   │   │   └── [promptId]/page.tsx
│   │   │   │   ├── users/page.tsx
│   │   │   │   └── notifications/page.tsx
│   │   │   │
│   │   │   └── audit/
│   │   │       └── page.tsx                 # 全局审计日志
│   │   │
│   │   ├── (mobile)/                        # 移动专属页面
│   │   │   ├── alert/[alertId]/page.tsx     # 移动端 alert 响应
│   │   │   └── approve/[proposalId]/page.tsx
│   │   │
│   │   └── api/                             # Next.js API routes（仅 BFF 用）
│   │       ├── auth/[...nextauth]/route.ts
│   │       ├── stream/                      # SSE endpoints
│   │       │   └── events/route.ts
│   │       └── proxy/                       # 后端 API 代理（隐藏 API key）
│   │           └── [...path]/route.ts
│   │
│   ├── components/
│   │   ├── ui/                              # shadcn/ui 基础组件（直接 copy）
│   │   │   ├── button.tsx
│   │   │   ├── card.tsx
│   │   │   ├── dialog.tsx
│   │   │   ├── ...
│   │   │
│   │   ├── layout/                          # 布局组件
│   │   │   ├── app-shell.tsx                # 主布局壳
│   │   │   ├── sidebar.tsx                  # 侧栏导航
│   │   │   ├── top-bar.tsx                  # 顶栏（搜索、通知、用户）
│   │   │   ├── command-palette.tsx          # Cmd+K 全局搜索
│   │   │   └── breadcrumbs.tsx
│   │   │
│   │   ├── charts/                          # ★ 金融图表组件
│   │   │   ├── valuation-distribution.tsx   # 估值分布（蒙特卡洛）
│   │   │   ├── price-chart.tsx              # 股价走势 + 基准
│   │   │   ├── factor-radar.tsx             # 因子雷达图
│   │   │   ├── agent-score-radar.tsx        # 7 agent 评分雷达
│   │   │   ├── lifecycle-timeline.tsx       # 状态机时间线
│   │   │   ├── outcome-heatmap.tsx          # checkpoint 热图
│   │   │   ├── attribution-waterfall.tsx    # 归因瀑布图
│   │   │   ├── drift-time-series.tsx        # drift 时序图
│   │   │   ├── debate-timeline.tsx          # 辩论时间线
│   │   │   ├── risk-matrix.tsx              # 5x5 风险矩阵
│   │   │   ├── exposure-treemap.tsx         # 组合暴露 treemap
│   │   │   └── correlation-heatmap.tsx
│   │   │
│   │   ├── domain/                          # ★ 业务专属组件
│   │   │   ├── snapshot-card.tsx
│   │   │   ├── decision-badge.tsx           # 参与/部分/不参与徽章
│   │   │   ├── confidence-meter.tsx
│   │   │   ├── agent-output-card.tsx        # 单个 agent 输出展开
│   │   │   ├── finding-with-citation.tsx    # finding + 原文引用
│   │   │   ├── prospectus-citation.tsx      # 招股书引用浮层
│   │   │   ├── review-form.tsx              # review 表单
│   │   │   ├── adjustment-diff.tsx          # 调整 diff 视图
│   │   │   ├── what-if-panel.tsx            # what-if 分析面板
│   │   │   ├── agent-chat.tsx               # 与 agent 对话组件
│   │   │   ├── alert-card.tsx
│   │   │   ├── lifecycle-state-badge.tsx
│   │   │   ├── ipo-status-pill.tsx
│   │   │   ├── checkpoint-progress.tsx      # 11 个 checkpoint 进度条
│   │   │   ├── cornerstone-investor-card.tsx
│   │   │   ├── sponsor-track-record.tsx
│   │   │   └── earnings-comparison-table.tsx
│   │   │
│   │   ├── forms/
│   │   │   ├── yaml-editor.tsx              # Monaco-based YAML 编辑器
│   │   │   ├── prompt-editor.tsx            # 提示词编辑（带 frontmatter）
│   │   │   └── markdown-editor.tsx
│   │   │
│   │   └── shared/
│   │       ├── data-table.tsx               # 通用复杂表格
│   │       ├── loading-states.tsx           # skeleton/spinner
│   │       ├── empty-states.tsx
│   │       ├── error-boundary.tsx
│   │       ├── pagination.tsx
│   │       ├── filter-bar.tsx
│   │       └── export-button.tsx
│   │
│   ├── lib/
│   │   ├── api/
│   │   │   ├── client.ts                    # API client 配置
│   │   │   ├── generated/                   # openapi-typescript 自动生成
│   │   │   │   └── schema.ts
│   │   │   ├── endpoints/                   # API hook 封装
│   │   │   │   ├── snapshots.ts
│   │   │   │   ├── ipos.ts
│   │   │   │   ├── reviews.ts
│   │   │   │   ├── proposals.ts
│   │   │   │   ├── alerts.ts
│   │   │   │   ├── drift.ts
│   │   │   │   ├── chat.ts
│   │   │   │   └── system.ts
│   │   │   └── websocket.ts
│   │   ├── auth/
│   │   │   ├── config.ts                    # Auth.js 配置
│   │   │   ├── permissions.ts               # RBAC 检查
│   │   │   └── middleware.ts
│   │   ├── stores/                          # Zustand stores
│   │   │   ├── ui-store.ts                  # UI 状态（侧栏开关等）
│   │   │   ├── workspace-store.ts           # 工作区状态（pinned IPOs）
│   │   │   └── notification-store.ts
│   │   ├── hooks/
│   │   │   ├── use-realtime.ts              # SSE/WebSocket hook
│   │   │   ├── use-keyboard.ts              # 快捷键
│   │   │   ├── use-permissions.ts
│   │   │   └── use-audit-log.ts
│   │   ├── utils/
│   │   │   ├── format.ts                    # 数字、日期、货币格式化
│   │   │   ├── currency.ts                  # HKD/USD/CNY 转换
│   │   │   ├── citations.ts                 # 引用处理
│   │   │   └── diff.ts                      # diff 工具
│   │   ├── constants/
│   │   │   ├── checkpoints.ts               # T+N 常量
│   │   │   ├── agent-roles.ts
│   │   │   └── routes.ts
│   │   └── types/                           # 全局类型
│   │       ├── domain.ts                    # 业务类型（mirror 后端 Pydantic）
│   │       └── ui.ts
│   │
│   ├── i18n/                                # 国际化
│   │   ├── config.ts
│   │   ├── messages/
│   │   │   ├── zh-CN.json
│   │   │   └── en.json
│   │   └── middleware.ts
│   │
│   └── styles/
│       ├── globals.css
│       └── themes/
│           ├── light.css
│           └── dark.css                     # 默认主题
│
├── tests/
│   ├── unit/                                # Vitest 单元测试
│   │   ├── components/
│   │   ├── lib/
│   │   └── utils/
│   ├── e2e/                                 # Playwright e2e
│   │   ├── auth.spec.ts
│   │   ├── dashboard.spec.ts
│   │   ├── ipo-analysis.spec.ts
│   │   ├── review-workflow.spec.ts
│   │   ├── proposal-approval.spec.ts
│   │   └── critical-alert-mobile.spec.ts
│   ├── fixtures/                            # 测试数据
│   │   ├── snapshots.json
│   │   ├── outcomes.json
│   │   └── proposals.json
│   └── visual/                              # 视觉回归测试
│       └── snapshots/
│
└── scripts/
    ├── generate-api-types.ts                # 从后端 OpenAPI 生成 TS 类型
    ├── seed-mock-data.ts                    # mock 数据生成
    └── check-a11y.ts                        # a11y 自动检查
```

---

## 3. 设计系统（必须严格遵守）

### 3.1 色彩

**遵循机构级金融工具风格**：暗色为默认主题，浅色为可选。

```css
/* 暗色主题（默认） */
--bg-primary: #0a0d12;       /* 近黑 */
--bg-secondary: #14181f;     /* 卡片背景 */
--bg-tertiary: #1c2128;      /* 嵌套卡片 */
--bg-elevated: #242b35;      /* 弹窗 */

--text-primary: #e6edf3;
--text-secondary: #8b949e;
--text-muted: #6e7681;

--border-default: #30363d;
--border-emphasized: #444c56;

/* 语义色 — 金融场景专属 */
--gain: #3fb950;             /* 涨/正向 */
--loss: #f85149;             /* 跌/负向 */
--neutral: #58a6ff;          /* 中性/信息 */

--severity-critical: #f85149;
--severity-warning: #d29922;
--severity-info: #58a6ff;
--severity-success: #3fb950;

/* 决策色 */
--decision-participate: #3fb950;
--decision-partial: #d29922;
--decision-skip: #8b949e;
--decision-wait: #58a6ff;

/* Agent 角色色（用于雷达图、徽章） */
--agent-fundamental: #58a6ff;
--agent-industry: #a371f7;
--agent-valuation: #3fb950;
--agent-policy: #d29922;
--agent-liquidity: #f778ba;
--agent-cornerstone: #ff9e64;
--agent-sentiment: #56d4dd;
```

### 3.2 字体

```css
--font-sans: "Inter", -apple-system, "PingFang SC", "Hiragino Sans GB", system-ui;
--font-mono: "JetBrains Mono", "Consolas", monospace;
--font-numeric: "JetBrains Mono"; /* 数字必须等宽 */
```

**强制规则**：
- 所有数字（股价、估值、百分比）必须用 `font-numeric` + `tabular-nums`
- 中文标题用 `font-weight: 500`，避免过粗
- 表格内容字号 ≤ 14px，确保信息密度

### 3.3 间距

```css
--space-1: 4px;
--space-2: 8px;
--space-3: 12px;
--space-4: 16px;
--space-6: 24px;
--space-8: 32px;
--space-12: 48px;
```

**机构级密度**：默认使用 Tailwind 的 `text-sm`、`p-3`、`gap-2`。禁止滥用 `p-8`、`text-2xl` 等消费级 SaaS 常见的"过度呼吸感"。

### 3.4 形状

- 圆角：`rounded` (4px) 默认；`rounded-lg` (8px) 用于卡片；禁用 `rounded-full` 除非真正的圆形元素
- 阴影：暗色主题下用 border 区分层级，少用 shadow

### 3.5 反馈一致性

| 状态 | 视觉 | 持续 |
|---|---|---|
| Loading | Skeleton（不用 spinner，除非 < 500ms） | - |
| Empty | 居中图标 + 短文案 + CTA | - |
| Error | 红色 banner + 重试按钮 + 详情可展开 | - |
| Success | Toast（右上角，3s 自动消失） | 3s |
| Critical alert | 红色 banner（持续显示直到 ack） + 系统通知 | 持续 |
| Saving | 禁用按钮 + 内联 spinner | - |
| Saved | 绿色 checkmark（1s 后消失） | 1s |

---

## 4. 核心页面详细规范

### 4.1 主控台 `/dashboard`

**目标**：机构 PM 早上 9:00 登录，**3 分钟内**掌握全局。

**布局**（CSS Grid 12 列）：

```
┌─────────────────────────────────────────────────────────────────┐
│ Top Bar: 全局搜索 + 实时时钟 + 通知铃 + 用户菜单                │
├─────────────────────────────────────────────────────────────────┤
│  侧栏  │  ┌── 今日待办（4 卡片，跨 12 列）──────────────────┐  │
│ 导航   │  │ Critical│ Pending │ Pending │ Overdue          │  │
│       │  │ Alerts  │ Reviews │ Approv. │ Checkpoints      │  │
│       │  │   3     │    7    │    2    │      1           │  │
│       │  └────────────────────────────────────────────────┘  │
│       │                                                       │
│       │  ┌── 活跃 IPO 状态 Swimlane（跨 12 列）──────────┐   │
│       │  │ [可视化所有 active snapshot 在状态机的位置]   │   │
│       │  └──────────────────────────────────────────────┘   │
│       │                                                       │
│       │  ┌── 组合暴露 ──┐ ┌── 本周事件 ──┐ ┌── 系统健康 ─┐ │
│       │  │ (4 列 treemap)│ │ (4 列 timeline)│ │ (4 列 status)│ │
│       │  └──────────────┘ └───────────────┘ └─────────────┘ │
│       │                                                       │
│       │  ┌── 最近 10 个完成的 checkpoint（跨 12 列）─────┐   │
│       │  │ [表格：IPO / Checkpoint / Return / Decision]  │   │
│       │  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

**组件细节**：

1. **4 张待办卡片**（统一组件 `<TodoCard>`）：
   - 大号数字 + 标签 + 趋势箭头（vs 昨天）
   - 点击跳转到对应详情列表
   - critical 类卡片若数字 > 0，红色发光边框
   - 数字 0 时显示绿色 checkmark
   - 实时刷新（SSE 订阅）

2. **活跃 IPO Swimlane**（`<LifecycleSwimlane>`）：
   - 横向时间线，按状态分泳道（PRE_LISTING / PRICING / LISTED / TERMINAL）
   - LISTED 泳道再细分为 11 个 checkpoint 列
   - 每个 IPO 是一个可点击的胶囊，hover 显示快速预览
   - 颜色编码状态健康度
   - 支持过滤：行业、ListingType、保荐人

3. **组合暴露 Treemap**：
   - 按行业/ListingType 切换
   - 块大小 = 资金敞口
   - 块颜色 = 实时表现（绿涨红跌）
   - 点击跳转 `/portfolio?industry=X`

4. **本周事件 Timeline**：
   - 即将到来的：财报发布、解禁、定价、上市
   - 每个事件可设置提醒
   - 高优先级事件高亮（如重要解禁日）

5. **系统健康**：
   - 调度器状态（high_freq / daily / event_driven 三个绿点）
   - 数据源连通（iFind / HKEX / Anthropic）
   - 近 24h LLM 成本 + 月度预算消耗进度
   - 任一不健康 → 触发警报但不阻塞 dashboard 加载

**交互**：
- `Cmd+K` 打开全局命令面板
- `g d` 任意页面快速跳回 dashboard（vim 风格快捷键）
- 数字键 `1-4` 跳转到 4 个待办类目

**数据**：
- API: `GET /api/v1/dashboard/summary`
- 实时更新: SSE `/api/stream/events` 订阅 `dashboard.*` 事件

### 4.2 单 IPO 详情 `/ipo/[ipoId]`

**目标**：reviewer 单 IPO 深度分析的主入口，**最常用的页面**。

**布局**（顶部 tab 切换不同视图）：

```
┌─────────────────────────────────────────────────────────────────┐
│ [Sticky Header]                                                  │
│ 公司名 + 股票代码 + ListingType 徽章                              │
│ Current Decision: [Participate] · Confidence: 78%                │
│ Price Range: HKD 12.5 - 15.0 - 17.5 · Last Updated: 2h ago      │
│ [Re-analyze] [Pin] [Export PDF] [Share]                          │
├─────────────────────────────────────────────────────────────────┤
│ [Tab Bar] Analysis | Lifecycle | Outcomes | Chat | Prospectus | Audit │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│ [Tab Content]                                                    │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

#### 4.2.1 Analysis Tab `/ipo/[id]/analysis`

**布局**（2 列：主区 8 列 + 侧边 4 列）：

```
Main (8 cols):
┌── Decision Summary Card ──────────────────────────┐
│ 大号决策 + Confidence Meter                       │
│ Key Reasons For / Against（左右两列）             │
│ Trigger Rules（监控触发器）                       │
└──────────────────────────────────────────────────┘

┌── 7 Agent Score Radar ──────────────────────────┐
│ 雷达图 + 每个 agent 的展开按钮                    │
└──────────────────────────────────────────────────┘

┌── Valuation Distribution ──────────────────────┐
│ 蒙特卡洛分布图 + 多模型对比（重叠分布）          │
│ 标注：发行价区间、当前可比公司中位数             │
│ [What-If 分析]按钮                              │
└──────────────────────────────────────────────────┘

┌── Debate Timeline ──────────────────────────────┐
│ Bull/Bear/Devil 三色时间线，每轮可展开           │
│ unresolved_issues 高亮                          │
└──────────────────────────────────────────────────┘

┌── Risk Matrix 5x5 ──────────────────────────────┐
│ 风险因素按概率×影响分布                          │
└──────────────────────────────────────────────────┘

Side (4 cols):
┌── Quick Facts ─────────┐
│ 行业、保荐人、Pre-IPO  │
│ 估值、关键股东、监管制度│
└─────────────────────────┘

┌── Comparable Companies ┐
│ 可比公司列表（A/H/US） │
│ 估值倍数中位数         │
└─────────────────────────┘

┌── Predicted Cornerstones┐
│ 系统预测的基石画像类型 │
│ + 支持/反对信号        │
└─────────────────────────┘

┌── Sponsor Track Record ┐
│ 保荐人历史表现统计     │
└─────────────────────────┘

┌── System Cost ─────────┐
│ 本次分析 LLM 成本      │
│ 总运行时间             │
└─────────────────────────┘
```

**关键交互**：

1. **每个 Finding 都可展开看完整 evidence + 原文引用**
   - 点击引用编号 → 浮层显示招股书原文（用 PDF.js 或文本片段）
   - 引用编号 hover → 显示页码 + 章节

2. **What-If 分析**（核心差异化功能）：
   - 点击触发右侧 drawer
   - 用户可修改关键假设：稳态毛利率、收入 CAGR、终值倍数、WACC、可比公司池
   - 实时调用后端 `/api/v1/whatif` → 重新计算估值分布
   - 显示新分布 vs 原分布的对比

3. **Agent 详情展开**：
   - 点击雷达图任意 agent → 右侧抽屉打开
   - 显示完整 AgentOutput：所有 findings、scores 拆分、数据源、不确定性 flags
   - 底部 "Ask this agent" 按钮跳转到 chat tab 并预填上下文

#### 4.2.2 Lifecycle Tab `/ipo/[id]/lifecycle`

**目标**：可视化 IPO 从招股书披露到现在的完整状态机历史。

**布局**：
- 主区：水平时间线（横向 SVG），状态用泳道颜色编码
- 时间线节点：每次状态转换 + 关键事件（公告、财报、价格异动）
- 节点颜色：critical/warning/info
- 点击节点 → 展开详情
- 时间轴可缩放（年/月/日）

**侧栏**：
- 当前状态卡片
- 下一个预期 checkpoint（倒计时）
- 历史警报列表（按时间倒序）

#### 4.2.3 Outcomes Tab `/ipo/[id]/outcomes`

**仅 LISTED 之后才有数据**。

**布局**：

```
┌── 股价走势 ─────────────────────────────────────┐
│ TradingView Lightweight Charts                  │
│ 主图：股价                                       │
│ 副图：vs 恒指/恒科指/行业基准的超额收益          │
│ 标注：发行价、首日收盘、关键事件                 │
└──────────────────────────────────────────────────┘

┌── Checkpoint 表现热图 ──────────────────────────┐
│ 11 列（T+1...T+360）× 4 行（绝对/超额三种）     │
│ 颜色：绿涨红跌                                   │
│ 每格点击 → 展开归因摘要                          │
└──────────────────────────────────────────────────┘

┌── 关键事件 Timeline ────────────────────────────┐
│ 财报发布、盈警、配售、解禁等                     │
│ 与股价走势关联（点击事件在上图标注）            │
└──────────────────────────────────────────────────┘

┌── 财报比对表（仅在财报发布后显示）─────────────┐
│ 收入/利润/毛利率：预测 vs 实际 + Deviation %    │
│ 整体评估：beat/in_line/miss/significant_miss    │
└──────────────────────────────────────────────────┘

┌── 归因摘要 ─────────────────────────────────────┐
│ 哪个 agent 偏差最大                              │
│ 哪个估值模型偏差最大                            │
│ Bear 的预言应验率                                │
│ [完整归因分析] → 跳转 review                    │
└──────────────────────────────────────────────────┘
```

#### 4.2.4 Chat Tab `/ipo/[id]/chat`

**这是体验最关键的功能**。参考 ChatGPT / Cursor / Claude 的对话界面。

**布局**：
- 主区：对话流（用户消息右侧，agent 消息左侧）
- agent 消息必须显示：所用数据源徽章、引用列表、运行时间、成本
- 引用可点击溯源
- 顶部：context summary（"You're chatting with the analysis agents for {company} as of {snapshot_date}"）
- 底部：输入框 + 建议问题（contextual quick prompts）
- 右侧抽屉：可固定常用工具（What-If、Run new analysis with modified assumption、Compare to similar company）

**建议问题示例**（基于当前上下文动态生成）：
- "为什么 Bull Agent 给的估值这么高？"
- "把这家与晶泰对比"
- "如果剔除可比公司中的 X 公司，估值会变多少？"
- "招股书第 142 页说的这个数据，与年报口径是否一致？"
- "Bear Agent 提的客户集中度风险，与历史类似公司的实际后果是什么？"

**关键交互**：
- 对话可"转为 review_note"：选中部分对话内容 → 右键 → "Add to Review Notes"
- 长任务（如重跑分析）后台执行 + 进度通知
- 历史对话保留并可搜索

**重要约束**：
- chat 不修改快照内容
- 任何"修改决策"的请求 → 引导用户走正式流程（创建新分析或写 review）

### 4.3 Review 工作台 `/reviews/[reviewId]`

**目标**：在 checkpoint 触发时完成结构化事后审查。

**布局**（3 列）：

```
Left (4 cols)             Center (5 cols)            Right (3 cols)
┌────────────────┐  ┌─────────────────────┐  ┌──────────────────┐
│ 原 Decision    │  │ Auto-Generated      │  │ Proposed         │
│ 摘要           │  │ Review Draft        │  │ Adjustments      │
│                │  │                     │  │                  │
│ • Decision     │  │ ## What we got      │  │ [Adj 1] ☐       │
│ • Confidence   │  │    right            │  │  Target: weights │
│ • Price Range  │  │ ...                 │  │  +0.1 to PS      │
│                │  │                     │  │  [Accept][Reject]│
│ 当前 Outcome   │  │ ## What we got      │  │                  │
│ • Return: -23% │  │    wrong            │  │ [Adj 2] ☐       │
│ • Decision     │  │ ...                 │  │  Target: prompt  │
│   Correct: No  │  │                     │  │  Edit fundamental│
│                │  │ ## Primary          │  │  [Accept][Reject]│
│ 关键事件列表    │  │    Attribution      │  │                  │
│ ...            │  │ - Fundamental Agent │  └──────────────────┘
└────────────────┘  │   missed: customer  │
                    │   concentration X   │
                    │                     │
                    │ ## Counterfactual   │
                    │ If listened to Bear │
                    │ ...                 │
                    │                     │
                    │ [Editable Markdown] │
                    │                     │
                    └─────────────────────┘

Bottom:
┌─────────────────────────────────────────────────────────────────┐
│ Reviewer: [auto-filled current user]                            │
│ Notes (Markdown): [tiptap editor]                               │
│ ☐ I confirm this attribution is accurate                        │
│ [Save Draft] [Submit Review]                                    │
└─────────────────────────────────────────────────────────────────┘
```

**关键交互**：

1. **左侧"对比"模式**：原决策卡片可与"当前事实"并排显示，差异高亮
2. **中央草稿可编辑**：系统自动生成但 reviewer 可修改任何文字
3. **每条 adjustment 都可：**
   - Accept（默认）
   - Reject（必须填理由）
   - Edit（修改 proposed_value，自动转 modified 状态）
4. **"Simulate"按钮**：在最近 5 个样本上模拟此调整效果（即时显示对历史 outcome 的影响）
5. **提交前强制 confirm checkbox**：避免误操作
6. **离开页面有未保存修改 → 警告 modal**

**自动化**：
- 进入页面自动加载 review_draft（后端 attribution.py 生成）
- 每 30s 自动保存草稿（draft 状态）
- 提交后写入 prediction_reviews，触发对应 adjustment_applier（如有 accepted）

### 4.4 Proposal 批准 `/learning/proposals/[id]`

**目标**：批准 learning_loop 提议的 config / prompt 调整。

**布局**：

```
┌── Proposal Header ─────────────────────────────────────────────┐
│ Target: config/valuation_weights.yaml                          │
│ Type: weight_change                                            │
│ Source: drift detected in CH18C valuation bias                 │
│ Proposed by: learning_loop (2026-05-15)                        │
└─────────────────────────────────────────────────────────────────┘

┌── Rationale ───────────────────────────────────────────────────┐
│ [完整 rationale 文字 + 链接到证据样本]                          │
└─────────────────────────────────────────────────────────────────┘

┌── Diff View ───────────────────────────────────────────────────┐
│ [GitHub-style diff: 红色删除 / 绿色新增]                        │
│                                                                 │
│ - CH18C_PRE_COMMERCIAL:                                        │
│ -   comparable_weight: 0.35                                    │
│ + CH18C_PRE_COMMERCIAL:                                        │
│ +   comparable_weight: 0.25                                    │
└─────────────────────────────────────────────────────────────────┘

┌── Evidence (5 snapshots) ──────────────────────────────────────┐
│ [Table: snapshot_id | ipo | predicted | actual | error]        │
│ 每行可点击展开                                                 │
└─────────────────────────────────────────────────────────────────┘

┌── Expected Impact ─────────────────────────────────────────────┐
│ [Text + 量化预测]                                              │
│ [Run Simulation on Last 5 Samples] 按钮                        │
└─────────────────────────────────────────────────────────────────┘

┌── Historical Similar Adjustments ──────────────────────────────┐
│ [此类调整的历史成功率]                                          │
└─────────────────────────────────────────────────────────────────┘

┌── Action ──────────────────────────────────────────────────────┐
│ Reviewer: [current user]                                       │
│ Comment: [textarea]                                            │
│ ☐ I have reviewed the diff carefully                           │
│ ☐ I have run the simulation                                    │
│ [Reject] [Edit & Accept] [Accept As-Is]                        │
└─────────────────────────────────────────────────────────────────┘
```

**强制约束**：
- 两个 confirm checkbox 必须勾选才能 Accept
- Accept 后立即触发 adjustment_applier
- Applier 失败时回滚 + 显示错误，UI 不能假装成功

### 4.5 Drift 可视化 `/learning/drift`

**布局**：

```
┌── Filters ─────────────────────────────────────────────────────┐
│ Time Range | ListingType | Industry | RegulatoryRegime         │
└─────────────────────────────────────────────────────────────────┘

┌── KPI 时序图（4 张并列）────────────────────────────────────────┐
│ 决策准确率 | 估值偏差 | Agent 校准 | Bear 漏报率                │
│ 每张图含：滑动窗口曲线 + 触发阈值线 + 历史 baseline             │
└─────────────────────────────────────────────────────────────────┘

┌── Active Drift Signals ────────────────────────────────────────┐
│ [Table: signal_type | severity | metric | threshold | samples] │
│ 每条可下钻到样本                                                │
└─────────────────────────────────────────────────────────────────┘

┌── 分维度切片热图 ─────────────────────────────────────────────┐
│ Y 轴：ListingType / Industry                                   │
│ X 轴：时间                                                      │
│ 颜色：偏差程度                                                  │
└─────────────────────────────────────────────────────────────────┘
```

**实时刷新**：drift 每天计算一次，UI 显示最后计算时间。

### 4.6 警报中心 `/alerts`

**布局**：
- 顶部 tab：Unacknowledged | All | History
- 默认显示 unacknowledged，按 level（critical → warning → info）+ 时间倒序
- 每条警报卡片：level icon + category + message + actionable_info + 关联 IPO/snapshot 链接
- 操作按钮：Acknowledge | Snooze (1h/4h/24h) | Escalate

**关键设计**：
- Critical alert 必须有 actionable_info（系统侧约束保证）
- Acknowledge 必须签名（自动用 current user，不能匿名）
- 已 ack 的警报保留 30 天后归档

### 4.7 IPO 列表 `/ipo`

**布局**：
- 顶部：filter bar（ListingType、Industry、状态、保荐人、决策结果）
- 主区：复杂表格（TanStack Table）
  - 列：公司名、代码、ListingType、Industry、状态、决策、最新表现、最新 checkpoint
  - 可排序、虚拟滚动、列宽可调
  - 行点击进入详情
  - 多选支持批量操作（如批量导出）
- 右侧：sticky pinned IPOs（用户固定关注的）

### 4.8 移动端 critical alert 页 `/m/alert/[id]`

**仅用于 critical alert 的快速响应**。

**单页布局**：
- 大号 level icon
- Alert message 全文
- 关联 IPO 摘要（名称、状态、当前关键数据）
- 三个大按钮：
  - **Acknowledge & View Later**（标记已知晓）
  - **Take Action Now**（跳转 desktop URL，桌面打开详情）
  - **Escalate**（升级给另一位 reviewer）
- 底部：与 Slack 集成的快速消息（"Already handled in Slack thread"）

**约束**：
- 不在移动端做完整操作（review 提交、proposal 批准），只做"确认收到"
- 任何实质性决策必须回到桌面端

---

## 5. 关键组件规范

### 5.1 `<FindingWithCitation>`

**用途**：显示一条 Finding，引用可点击溯源到招股书原文。

**接口**：
```tsx
interface Props {
  finding: Finding;
  onCitationClick?: (citation: Citation) => void;
  variant?: 'inline' | 'card' | 'expanded';
}
```

**行为**：
- 引用编号显示为下标数字（如 `[1]`、`[2]`），样式为可点击的徽章
- 点击 → 浮层显示原文片段 + 页码 + 章节
- 浮层有"在招股书中打开"按钮 → 跳转 `/ipo/[id]/prospectus?page=X&highlight=Y`
- 支持键盘导航（Tab 在引用间跳转）

### 5.2 `<ValuationDistribution>`

**用途**：估值分布可视化（蒙特卡洛 + 多模型对比）。

**接口**：
```tsx
interface Props {
  distribution: ValuationDistribution;  // ensemble 主分布
  singleModels?: SingleModelValuation[]; // 各模型分布（可选叠加）
  benchmarks?: {                          // 参考线
    issuePrice?: Decimal;
    issueRangeLow?: Decimal;
    issueRangeHigh?: Decimal;
    comparableMedian?: Decimal;
  };
  showPercentiles?: boolean;              // 显示 p10/p50/p90 标线
  height?: number;
}
```

**渲染**：
- 主分布：填充密度图（KDE）
- 单模型分布：叠加的细线
- 基准线：垂直虚线 + 标签
- 百分位标线：垂直实线 + 数值
- 交互：hover 显示 (price, density) tooltip

### 5.3 `<AgentScoreRadar>`

**用途**：7 个 agent 的评分雷达图。

**接口**：
```tsx
interface Props {
  agents: Array<{
    role: AgentRole;
    overallScore: number;
    subScores: Record<string, number>;
  }>;
  onAgentClick?: (role: AgentRole) => void;
  showSubScores?: boolean;  // 是否展开子维度
}
```

**渲染**：
- 7 顶点雷达图
- 每个 agent 用其专属颜色
- 顶点点击 → 触发 onAgentClick（通常打开右侧抽屉）
- 内部填充半透明
- 显示均值线作参考

### 5.4 `<LifecycleSwimlane>`

**用途**：dashboard 中显示所有 active IPO 在状态机的位置。

**接口**：
```tsx
interface Props {
  ipos: Array<{
    id: string;
    companyName: string;
    currentState: IPOLifecycleStateType;
    stateMetadata: any;
    listingDate?: Date;  // LISTED 状态才有
    daysSinceListing?: number;
    nextCheckpoint?: number;
    health: 'healthy' | 'warning' | 'critical';
  }>;
  groupBy?: 'state' | 'industry' | 'listing_type';
  onIPOClick?: (id: string) => void;
}
```

**渲染**：
- 4 行泳道：PRE_LISTING / PRICING / LISTED / TERMINAL
- LISTED 行特殊：横向细分 11 个 checkpoint 列
- 每个 IPO 是胶囊（含公司名 + 状态时长）
- 颜色：health 决定（绿/黄/红）
- 胶囊可拖动到不同位置以重排（仅 UI 状态，不影响数据）

### 5.5 `<AdjustmentDiff>`

**用途**：调整提议的 diff 视图。

**接口**：
```tsx
interface Props {
  targetPath: string;
  adjustmentType: AdjustmentType;
  currentValue: any;
  proposedValue: any;
  syntax?: 'yaml' | 'markdown' | 'json';
}
```

**渲染**：
- GitHub style：红色 - / 绿色 +
- 语法高亮（Monaco diff editor）
- 不可编辑（只读对比）
- 复制按钮支持复制 patch

### 5.6 `<AgentChat>`

**用途**：与 agent 对话组件，支持引用、工具调用、流式响应。

**接口**：
```tsx
interface Props {
  sessionId: string;
  ipoContext: IPOContext;  // 当前 IPO 上下文
  initialMessages?: Message[];
  onMessageSent?: (msg: Message) => void;
}
```

**行为**：
- 消息流式渲染（SSE）
- 用户消息：右侧灰色气泡
- Agent 消息：左侧，含元数据 footer（成本、用时、数据源）
- 引用：消息内嵌可点击 `[1]`、`[2]`
- 工具调用：折叠卡片显示（"Calling: get_ifind_data"）
- 输入框：底部，支持多行、Cmd+Enter 发送、Shift+Enter 换行
- 建议问题：输入框上方 chip 按钮（contextual quick prompts）
- 选中文字：弹出操作菜单（添加到 review notes、复制、引用回复）

---

## 6. API 集成

### 6.1 API Client 配置

**所有 API 调用走 TanStack Query**，禁止直接 fetch / axios。

```typescript
// src/lib/api/client.ts
import { QueryClient } from '@tanstack/react-query';

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,       // 30s 默认 stale
      gcTime: 5 * 60_000,      // 5min 缓存
      retry: (failureCount, error) => {
        if (error.status === 401 || error.status === 403) return false;
        return failureCount < 3;
      },
      refetchOnWindowFocus: true,
    },
    mutations: {
      retry: false,
      onError: (error) => {
        // 全局错误处理 → toast
      },
    },
  },
});
```

### 6.2 类型生成

**强制要求**：后端 FastAPI 暴露 OpenAPI schema，UI 用 `openapi-typescript` 自动生成 TS 类型。

```bash
# 生成命令
pnpm run generate-api-types
# 内部执行：
# openapi-typescript http://localhost:8000/openapi.json -o src/lib/api/generated/schema.ts
```

CI 检查：生成后 git diff 必须为空（保证类型同步）。

### 6.3 端点 hook 封装

每个业务领域一个 hook 文件：

```typescript
// src/lib/api/endpoints/snapshots.ts
import { useQuery, useMutation } from '@tanstack/react-query';
import type { components } from '../generated/schema';

type Snapshot = components['schemas']['PredictionSnapshot'];

export function useSnapshot(snapshotId: string) {
  return useQuery({
    queryKey: ['snapshot', snapshotId],
    queryFn: () => api.get(`/snapshots/${snapshotId}`),
    enabled: !!snapshotId,
  });
}

export function useCreateSnapshot() {
  return useMutation({
    mutationFn: (data) => api.post('/snapshots', data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['snapshots'] });
    },
  });
}
// ... 其他
```

### 6.4 实时数据架构

**两条通道**：

1. **SSE (主)** — Server-Sent Events，单向推送
   - 端点：`GET /api/stream/events`
   - 事件类型：`alert.created`、`snapshot.updated`、`outcome.recorded`、`scheduler.completed`、`proposal.proposed`
   - 用 `EventSource` API 订阅
   - 自动重连

2. **WebSocket (备)** — 用于需要双向的场景
   - 端点：`WS /api/ws/chat/[sessionId]`
   - 仅 chat 功能使用

```typescript
// src/lib/hooks/use-realtime.ts
export function useRealtimeEvents() {
  useEffect(() => {
    const source = new EventSource('/api/stream/events');
    
    source.addEventListener('alert.created', (e) => {
      const alert = JSON.parse(e.data);
      // 触发 toast + 更新 query cache
      queryClient.invalidateQueries({ queryKey: ['alerts'] });
      if (alert.level === 'critical') {
        showBrowserNotification(alert);
      }
    });
    
    return () => source.close();
  }, []);
}
```

### 6.5 关键端点清单

**必须由后端提供**（v1.2 主 spec 需补充）：

```
认证
GET    /api/v1/auth/me                       # 当前用户
POST   /api/v1/auth/logout

Dashboard
GET    /api/v1/dashboard/summary             # 今日待办数据

IPO
GET    /api/v1/ipos                          # 列表 + 筛选
GET    /api/v1/ipos/{id}                     # 详情
GET    /api/v1/ipos/{id}/snapshots           # 所有快照
GET    /api/v1/ipos/{id}/lifecycle           # 状态机历史
GET    /api/v1/ipos/{id}/outcomes            # 所有 checkpoint outcome
GET    /api/v1/ipos/{id}/events              # 关键事件
POST   /api/v1/ipos/{id}/reanalyze           # 触发重新分析

Snapshot
GET    /api/v1/snapshots/{id}                # 完整快照
GET    /api/v1/snapshots/{id}/agent-outputs  # 单独取 agent outputs

Prospectus
GET    /api/v1/prospectus/{id}               # PDF + 抽取结果
GET    /api/v1/prospectus/{id}/citation/{citationId}  # 单条引用原文

Reviews
GET    /api/v1/reviews?status=pending        # 列表
GET    /api/v1/reviews/{id}                  # 详情 + 草稿
POST   /api/v1/reviews/{id}/draft            # 保存草稿
POST   /api/v1/reviews/{id}/submit           # 提交

Proposals
GET    /api/v1/proposals?status=proposed
GET    /api/v1/proposals/{id}
POST   /api/v1/proposals/{id}/simulate       # 在历史样本上模拟效果
POST   /api/v1/proposals/{id}/decision       # accept/reject

Drift
GET    /api/v1/drift/signals
GET    /api/v1/drift/timeseries?metric=accuracy&window=30d

Alerts
GET    /api/v1/alerts?status=unacknowledged
POST   /api/v1/alerts/{id}/acknowledge

Chat
POST   /api/v1/chat/sessions                 # 新建会话
GET    /api/v1/chat/sessions/{id}/messages
WS     /api/ws/chat/{sessionId}              # 流式对话

What-If
POST   /api/v1/whatif/valuation              # 重新跑估值

System
GET    /api/v1/system/health
GET    /api/v1/system/schedulers
GET    /api/v1/system/costs?period=monthly

Audit
GET    /api/v1/audit/logs?filters=...

Settings
GET    /api/v1/settings/configs              # 列出所有 YAML
GET    /api/v1/settings/configs/{path}       # 单文件内容
GET    /api/v1/settings/prompts
PUT    /api/v1/settings/prompts/{id}         # 必须走 proposal 流程

Stream
GET    /api/stream/events                    # SSE
```

---

## 7. 认证与权限

### 7.1 角色定义

| Role | 权限范围 |
|---|---|
| **Viewer** | 只读所有数据；不能写任何东西 |
| **Reviewer** | Viewer + 提交 review + propose adjustment |
| **Senior Reviewer (IC)** | Reviewer + accept/reject proposal |
| **Operator** | 系统管理（调度器、数据源、配置编辑） |
| **Admin** | 全部 + 用户管理 |
| **Auditor** | 只读 + 全量审计日志（含敏感操作） |

### 7.2 权限检查

**前端 + 后端双重检查**：

```typescript
// 前端 - 隐藏不允许的 UI
const { hasPermission } = usePermissions();
{hasPermission('proposal.accept') && <AcceptButton />}

// 后端必须重复检查（前端只是 UX，不是安全）
// 任何写操作的 API endpoint 都要 RBAC 验证
```

### 7.3 SSO 集成

- 主认证：Auth.js + Okta / Azure AD SAML
- Fallback：本地账号（仅开发环境）
- 强制 MFA：所有 write 权限角色

### 7.4 操作签名

**所有写操作必须自动记录**：

- operator (user_id)
- timestamp
- IP address
- user agent
- before / after 状态（diff）
- 操作 ID（用于追溯）

记录到后端 `audit_log` 表（v1.2 需补充此表）。

---

## 8. 状态管理

### 8.1 状态分类

| 状态类型 | 工具 | 例子 |
|---|---|---|
| 服务端状态 | TanStack Query | IPO 数据、snapshot、alerts |
| 全局 UI 状态 | Zustand | 侧栏开关、暗色模式、pinned IPOs |
| 表单状态 | react-hook-form | review 表单、settings |
| URL 状态 | next-navigation + useSearchParams | filter、tab、pagination |
| 临时状态 | useState / useReducer | 弹窗开关、tooltip hover |

### 8.2 Zustand store 设计

```typescript
// src/lib/stores/workspace-store.ts
interface WorkspaceState {
  pinnedIpoIds: string[];
  recentlyViewedIds: string[];
  preferredCurrency: 'HKD' | 'USD' | 'CNY';
  preferredCheckpoints: number[];  // 用户偏好显示的 checkpoint
  
  pinIpo: (id: string) => void;
  unpinIpo: (id: string) => void;
  // ...
}

export const useWorkspaceStore = create<WorkspaceState>()(
  persist(
    (set) => ({ /* ... */ }),
    { name: 'workspace' }
  )
);
```

---

## 9. 构建阶段

**Claude Code 必须严格按以下阶段顺序推进**，每阶段完成后停下来等待人工确认才能进入下一阶段。

### Phase 11.0: 项目骨架 + 基础设施（2 天）

**Deliverables：**
- [x] Next.js 15 + TypeScript + Tailwind 4 项目初始化
- [x] pnpm + Biome + Vitest + Playwright 配置
- [x] shadcn/ui 安装 + 暗色主题配置
- [x] 设计系统：颜色、字体、间距 token 全部就位
- [x] Auth.js 集成 + SSO（dev 用 mock provider）
- [x] TanStack Query 配置
- [x] API client + openapi-typescript 自动生成
- [x] 主布局：sidebar + topbar + breadcrumbs
- [x] i18n 配置（zh-CN 默认）
- [x] Sentry 错误追踪
- [x] CI/CD pipeline（GitHub Actions）

**DONE 条件**：`pnpm dev` 跑得起来，能登录看到空 dashboard，类型生成自动化运行。

### Phase 11.1: Dashboard 主控台（2-3 天）

**Deliverables：**
- [x] 4 张待办卡片组件
- [x] `<LifecycleSwimlane>` 完整实现
- [x] 组合暴露 treemap
- [x] 本周事件 timeline
- [x] 系统健康卡片
- [x] 最近 checkpoint 表格
- [x] 全局命令面板（Cmd+K）
- [x] SSE 实时刷新接入

**DONE 条件**：dashboard 所有数据来自真实后端 API，实时更新生效。

### Phase 11.2: IPO 详情 — Analysis Tab（3-4 天）

**Deliverables：**
- [x] IPO 详情布局 + tab 系统
- [x] Decision summary 卡片
- [x] `<AgentScoreRadar>` 含展开抽屉
- [x] `<ValuationDistribution>` 含 what-if 面板
- [x] Debate timeline
- [x] Risk matrix
- [x] 侧栏所有信息卡片
- [x] `<FindingWithCitation>` 含引用浮层
- [x] 招股书引用溯源（PDF 片段展示）

**DONE 条件**：能完整看一份历史 snapshot 的分析报告，所有引用可点击溯源。

### Phase 11.3: IPO 详情 — Lifecycle / Outcomes / Prospectus Tabs（2-3 天）

**Deliverables：**
- [x] Lifecycle timeline 可视化
- [x] Outcomes 页面：股价图 + checkpoint 热图 + 关键事件 + 财报比对 + 归因
- [x] Prospectus tab：PDF 原文 + 抽取对照（左右双栏）
- [x] Audit tab：单 IPO 完整操作日志

**DONE 条件**：已上市公司能看到完整生命周期 + 11 个 checkpoint 表现。

### Phase 11.4: Review 工作台 + Proposal 批准（3 天）

**Deliverables：**
- [x] Review 工作台 3 列布局
- [x] Review 表单 + tiptap 编辑器
- [x] 自动保存草稿
- [x] Adjustment 子组件（accept/reject/edit）
- [x] Proposal 批准页 含 diff 视图
- [x] Simulate 功能
- [x] 提交确认 + 强制 checkbox

**DONE 条件**：能完整走完 review 提交 → proposal 批准 → adjustment apply 流程。

### Phase 11.5: 警报中心 + 学习闭环可视化（2 天）

**Deliverables：**
- [x] 警报中心：列表 + ack + snooze + escalate
- [x] Drift 可视化页面
- [x] 跨样本归因页面
- [x] 历史调整效果页面
- [x] 浏览器原生通知集成

**DONE 条件**：critical alert 实时推送 + drift 时序图准确呈现。

### Phase 11.6: AI 对话 + What-If（2-3 天）

**Deliverables：**
- [x] `<AgentChat>` 完整组件
- [x] WebSocket 流式响应
- [x] 引用浮层与溯源
- [x] 工具调用展示
- [x] 建议问题（contextual prompts）
- [x] 选中文字 → add to review notes
- [x] What-if 面板独立组件 + 实时重算

**DONE 条件**：可以对单个 IPO 与 agent 自然对话，对话可转 review note。

### Phase 11.7: Settings + System + Audit（2 天）

**Deliverables：**
- [x] YAML 配置查看（编辑必须走 proposal）
- [x] Prompt 管理（含 frontmatter 编辑）
- [x] 用户管理
- [x] 系统健康详情页（调度器 / 数据源 / 成本）
- [x] 全局审计日志（带筛选 + 导出）
- [x] 通知设置

**DONE 条件**：admin 能管理系统、reviewer 能查询全部审计记录。

### Phase 11.8: 移动 critical alert + 上线准备（2 天）

**Deliverables：**
- [x] 移动端 critical alert 页面
- [x] 移动 approve 简版
- [x] Slack 集成测试
- [x] e2e 测试套件完整覆盖
- [x] 视觉回归测试 baseline
- [x] 性能优化（Lighthouse > 90）
- [x] a11y 审计通过（WCAG AA）
- [x] 部署到生产 + DEPLOYMENT_UI.md

**DONE 条件**：移动 critical alert 4h 响应测试通过 + 生产上线 checklist 全部完成。

---

## 10. 测试规范

### 10.1 测试金字塔

- **单元测试 (Vitest)**：组件、utils、hooks — 覆盖率 ≥ 70%
- **集成测试 (Vitest + MSW)**：API 集成、复杂交互
- **e2e (Playwright)**：关键用户旅程必须覆盖
- **视觉回归 (Playwright snapshots)**：核心页面 + 关键组件

### 10.2 e2e 必须覆盖的关键旅程

1. 登录 → dashboard → 看到待办
2. 进入 IPO 详情 → 看完整分析 → 点击引用溯源
3. 与 agent 对话 → 选中文字转 review note
4. 完成 review 提交 → 批准 adjustment → 验证 apply 成功
5. 收到 critical alert → 移动端响应 → ack
6. Drift 触发 → 查看 → 跳转到样本
7. 触发重新分析 → 等待完成 → 看到新决策

### 10.3 性能要求

- LCP < 2.5s
- FID < 100ms
- CLS < 0.1
- Bundle size：单页 JS < 200KB gzipped
- 复杂图表懒加载

---

## 11. 部署与运维

### 11.1 部署架构

**生产推荐**：
- 前端：Vercel (Enterprise) 或自托管 K8s
- 后端 API：独立部署（v1.2 主 spec 定义）
- 静态资源 CDN：Vercel / Cloudflare

**约束**：
- UI 必须能独立部署（不依赖后端同步发布）
- Feature flags 控制新功能逐步推出
- 蓝绿部署 + 即时回滚能力

### 11.2 环境配置

```
.env.local              # 本地开发
.env.development        # dev 环境
.env.staging            # 预发
.env.production         # 生产
```

关键变量：
```
NEXT_PUBLIC_API_BASE_URL=
NEXT_PUBLIC_WS_URL=
NEXT_PUBLIC_SENTRY_DSN=
AUTH_SECRET=
AUTH_OKTA_CLIENT_ID=
AUTH_OKTA_CLIENT_SECRET=
AUTH_OKTA_ISSUER=
```

### 11.3 监控

- Sentry：错误追踪
- PostHog：用户行为分析（评估合规后启用）
- Web Vitals：性能监控
- 自定义 metric 上报到后端 Prometheus

---

## 12. CLAUDE.md UI 内容

新建 `hk-ipo-cornerstone-ui/CLAUDE.md`，包含：

```markdown
# Claude Code UI 工作准则

## 启动检查
1. 优先读 PROJECT_SPEC_UI.md（本目录下）
2. 检查后端 API schema 是否最新：`pnpm run generate-api-types`
3. 确认当前 Phase（git tag 或 CHANGELOG）

## 严格约束
- 严禁引入 §1 技术栈之外的核心依赖（含 MUI/Ant/Chakra/Redux/jQuery）
- 严禁直接 fetch / axios — 必须走 TanStack Query
- 严禁 inline styling — 必须用 Tailwind
- 严禁组件内硬编码业务逻辑 — 必须用后端 API
- 严禁绕过权限检查 — 必须用 usePermissions
- 严禁不写测试就提交 — 单元测试覆盖率不能下降

## 设计系统约束
- 颜色必须用 token，禁止硬编码 hex
- 数字必须 tabular-nums + font-numeric
- 间距用 Tailwind scale (`p-3` 而非 `p-[13px]`)
- 默认暗色主题，组件必须同时支持两个主题

## 数据约束
- 所有金额必须明确币种（HKD/USD/CNY）+ tooltip 显示原值
- 所有日期必须本地化 + tooltip 显示 ISO 格式
- 所有百分比明确表示 (12.5% not 0.125)
- 所有数据点都应该可溯源（引用、原始 snapshot 链接）

## 性能约束
- 任何 page 必须 SSR / RSC 优先
- 客户端组件用 'use client' 显式标注
- 大列表用虚拟滚动（TanStack Virtual）
- 图表组件按需 lazy load

## 决策原则
- 遇到歧义停下来问，不要猜
- 涉及业务逻辑必须确认是后端实现还是 UI 实现
- 修改设计 system token 必须 ADR
- 任何"改用户输入数据"的 mutation 必须二次确认

## 我应该问而不是猜
1. 新增第三方依赖
2. 修改设计 token
3. 新增 API 端点（必须后端先实现）
4. 改变权限模型
5. 任何涉及"删除"或"重构"超过 1 个组件
```

---

## 13. 项目里程碑指标（Definition of Done for v1.3 UI）

- [ ] 全部 9 个 Phase 完成（11.0-11.8）
- [ ] 单元测试覆盖率 ≥ 70%
- [ ] 7 个关键 e2e 旅程全部通过
- [ ] Lighthouse 全项 > 90
- [ ] a11y WCAG AA 通过
- [ ] 视觉回归 baseline 建立
- [ ] 3 个真实用户（Reviewer / Senior / Admin）完成各自工作流的 UAT
- [ ] DEPLOYMENT_UI.md、KEYBOARD_SHORTCUTS.md、USER_GUIDE.md 完备
- [ ] 移动 critical alert 在 5 种主流设备上测试通过（iOS Safari、Android Chrome 等）
- [ ] 与后端 v1.2 完整集成测试通过（端到端的 review → proposal → apply 闭环）

---

## 14. 关键风险

| 风险 | 防范 |
|---|---|
| 后端 API 不稳定，UI 频繁失败 | TanStack Query retry + fallback UI + error boundary |
| 权限绕过 | 前端隐藏 + 后端强制双重检查 |
| 实时数据延迟 | SSE 自动重连 + 显示"最后更新时间" |
| 大量数据加载卡顿 | 虚拟滚动 + 分页 + 懒加载 |
| 用户误操作不可恢复 | 写操作 confirm + 关键操作签名 |
| 移动端误点 critical 操作 | 移动端不允许实质操作，只 ack |
| 浏览器通知权限被拒 | 多通道（Slack + Email + 网页内通知）兜底 |
| Bundle 过大 | 路由级 code splitting + 监控 + budget 拒绝合并 |
| 设计漂移 | DESIGN_SYSTEM.md + Storybook + 视觉回归测试 |
| 后端 schema 变更没同步 | CI 强制 `generate-api-types` diff 为空 |

---

## 15. 与后端 v1.2 spec 的协作要求

**后端必须配套提供（v1.2 需补充的）**：

1. 完整 OpenAPI 3.1 schema（所有 endpoint）
2. SSE endpoint `/api/stream/events` + 事件类型清单
3. WebSocket endpoint `/api/ws/chat/{sessionId}`
4. 审计日志表 + endpoint
5. RBAC 角色定义文档
6. PDF.js 可加载的招股书 endpoint（CORS 配置）
7. What-if 估值的 API 接口
8. 在主 spec 中新增 §16 "UI 集成要求"

**协作流程**：
- API 变更先在后端 spec PR，UI 跟进
- UI 不允许 mock API 长期存在 — mock 只在新 endpoint 实现前 < 1 周用

---

## 16. 给 Claude Code 的最终指令

1. **首先**：完成 Phase 11.0 全部 deliverables，跑通 `pnpm dev` 看到登录页 + 空 dashboard。停下来等我确认。
2. **不要**跳过任何 Phase。
3. **不要**绕过设计系统 token。
4. **不要**在没有 generate-api-types 后就引用类型。
5. **不要**修改本规范的约束部分；可追加 ADR。
6. **每个 Phase 完成必须**：
   - 跑过所有相关测试
   - 视觉回归 baseline 更新
   - 截图记录关键页面
   - 总结实施细节到 `docs/decisions/`
7. **遇到任何与本文件冲突的情况，停下来问我。**

---

*Version: 1.3 (UI)*  
*Last Updated: 2026-05-16*  
*Companion to: PROJECT_SPEC.md v1.2 (backend)*  
*Tech Stack: Next.js 15 + shadcn/ui + Tailwind 4 + TanStack Query + Tremor + TradingView Charts*