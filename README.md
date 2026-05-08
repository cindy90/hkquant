# NACS v8 — HK IPO 基石量化模型

港股 IPO 基石投资策略评分模型 v8 — 含 **Regime Gate** + **Cluster Bonus** + **砍 RELATIONSHIP 死亡区间** 的完整版。

基于 2022-01 到 2026-05 共 385 只港股 IPO 实证回测：
- **主板已盈利全样本** (n=269): 60d IC=+0.10
- **Regime Gate ≥0 过滤后子样本** (n=91): **60d IC=+0.247, L-S spread=+35%, t=+2.41 ✅**
- **★ Production 实战仓位** (regime≥0 + LARGE/TRIAL, n=8): **60d mean=+42%, win=75%**

---

## 目录结构

```
港股基石轮投资模型/
├── README.md                    本文档
├── requirements.txt             Python 依赖
├── .gitignore                   排除 .env / 缓存 / 运行产物
├── check_health.py              数据 + 模型健康检查 (5 单测)
├── run_v7_backtest.py           完整 v8 回测 (含 production 实战报告, < 1 秒)
├── build_perf_cache.py          基石性能缓存预填充 (使回测 50x 加速)
├── nacs_checklist_tool.html     交互式决策清单工具 (浏览器打开)
│
├── src/
│   ├── nacs_model.py            v8 模型核心 (40KB)
│   ├── data/                    持久化层（不要与 data/ 混淆）
│   │   ├── dao.py               数据访问层 (基石 perf 派生)
│   │   └── schema.py            DB schema 定义
│   └── data_sources/
│       └── ifind/               同花顺 iFinD 数据拉取
│           ├── README.md        安装/使用指南
│           ├── .env             凭证 (gitignore)
│           ├── .env.example     凭证模板
│           └── full_data_pull.py
│
├── data/
│   ├── nacs_real.db             SQLite 主库 (1.5MB, 含 385 IPO + 31k 缓存)
│   ├── raw/
│   │   ├── ifind/               iFinD 拉取的 7 张原始表（拉取脚本直接写入）
│   │   └── wind/                Wind 模板（历史快照）
│   └── derived/
│       ├── nacs_v7.csv          v7 评分输出 (385 行)
│       └── nacs_yearly_aff.csv  主板按年份 + affiliation 分析
│
├── docs/
│   └── ARCHITECTURE.md          模型架构 + 设计决策
│
├── legacy/wind/                 Wind 老工作流（已被 iFinD 替代，保留作历史）
│
└── outputs/                     运行 run_v7_backtest.py 后的输出（gitignore）
```

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

`pandas / numpy / openpyxl` 来自 PyPI；`iFinDPy`（同花顺 QuantAPI）需要先装 iFinD 客户端再跑客户端目录下的注册工具——详见 [src/data_sources/ifind/README.md](src/data_sources/ifind/README.md)。

如果只用回测、不拉新数据，可以不装 iFinDPy（DB 已预填）。

### 2. 验证环境

```bash
python check_health.py
```

预期输出 `✅ 全部通过`。这一步会做：
- 检查 SQLite DB 完整性 (10 张表, 含 385 IPO + 1609 基石)
- 验证 model 代码可导入
- 跑 5 个功能性单元测试 (baseline / regime gate / cluster bonus / regime helper / RELATIONSHIP=0)

### 3. 跑完整 v8 回测

```bash
python run_v7_backtest.py
```

输出 `outputs/nacs_v7_scores.csv`（385 行评分）+ 控制台打印 IC 报告 + **production 实战回报**。

**注意**：DB 已经预填充了 `cornerstone_performance_asof` 缓存表（31k 行，8.6k 有效），所以回测**< 1 秒**完成。如果换了新数据需要重新生成缓存：

```bash
python build_perf_cache.py --clear-existing  # 重建缓存, ~4 秒
```

### 4. 决策清单工具

直接在浏览器打开 `nacs_checklist_tool.html`，输入新 IPO 数据交互评分。

---

## v7→v8 迭代历程

### v7: Regime Gate + Cluster Bonus

**问题**：NACS 模型在不同年份效果差异悬殊
- 2022 年（港股冰封）: ic=-0.08
- 2023 年: ic=+0.21
- 2024 年（A股牛市抽水）: ic=+0.04
- 2025 年: ic=+0.18

**Regime Gate 解决**：每只 IPO 在 pricing_date 时点查询过去 [t-120, t-30] 天上市 IPO 的 30 日中位收益作为 `regime_score`：
- `regime_score < 0` → 强制 SKIP
- `regime_score ≥ 0` → 正常评分

效果：60d IC 从 +0.090 → **+0.245**，t-stat 从 0.26 → **2.41 ✅**

**Cluster Bonus 解决**：识别"产业资本通过多 SPV 分仓"模式（同 ultimate_holder ≥2 个基石），给 Q_ecosystem ×1.10/1.15/1.20 加成。验证：cluster≥2 IPO 60d mean +22% (vs 无关联 +14%)，std 也降低 40%。

代码定位：`src/nacs_model.py` L765-773 (cluster) + L884-922 (regime helper) + L981-994 (gate 应用)

### v8: 砍 RELATIONSHIP 死亡区间

**新发现**：NACS [0.25, 0.35) 的 RELATIONSHIP 决策档**全样本反指**——n=25, mean=+6%, median=-12%, win=32%。这个区间内 NACS 排序无法区分好坏（周六福 NACS=0.346 +57% 跟 大行科工 NACS=0.346 -27% 同分但收益完全相反）。

**v8 解决**：把 `[0.25, 0.35) → RELATIONSHIP` 的仓位从 15% 改成 **0%**——保留诊断标签（识别"是关系户"）但不实际下钱。

效果对比（regime≥0 子样本）：
| 维度 | v7 (含 RELATIONSHIP) | v8 (砍 RELATIONSHIP) |
|---|---|---|
| n | 18 | **8** |
| 60d mean | +19% | **+42%** |
| 60d win rate | 56% | **75%** |
| 30d mean | +9% | +21% |
| 180d mean | +5% | +32% |

代码定位：`src/nacs_model.py` L932-947 (`_position_from_nacs` v8)

### 离散化优化（已弃用）

曾尝试把 `_score_l1_3_profitable` 的 `gm` 二元化改连续 tanh、`nd` 派生 4 档改连续，但 A/B 测试显示对 IC 提升 < 0.002（统计上不显著）。原因是 rank IC 对同分并列不敏感，且 fundamentals 在 Q_company 6 个子项里只占 25 权重。**保留 v7 设计**。

---

## 核心评分结构

```
NACS = Q_company × Q_ecosystem × (1 - R_lockup)
```

三层乘法：
- **Q_company (Layer 1)**: 公司基本面 + 估值 + 保荐 + 发行结构 + 章节 + 市场环境
- **Q_ecosystem (Layer 2)**: 基石组合质量 (含 v7 cluster bonus)
- **R_lockup (Layer 3)**: 锁定期风险 (减分)

后处理调整：
- 18C ×0.70（高估值惩罚）
- A+H 可融券 ×1.10（套利机会）
- 第二上市 ×0.85
- v7 Regime Gate（regime<0 → SKIP）

仓位映射（v8）：
| NACS | 决策 | 仓位 |
|---|---|---|
| ≥0.55 | FULL | 100% |
| 0.45-0.55 | LARGE | 70% |
| 0.35-0.45 | TRIAL | 40% |
| 0.25-0.35 | RELATIONSHIP | **0%** (v8 砍掉) |
| <0.25 | SKIP | 0% |

---

## 数据来源

- **Wind 专业版** - 基础 IPO 信息 + 基石名册（`nacs_data_template.xlsx`）
- **iFinD（同花顺机构金融终端）** - 基石详细数据（含 `ultimate_holder`）+ 财务（`ifind_*.csv`）
- **手填补充** - 顶部 300 基石机构标签（基金性质、AUM 等）

样本范围：2022-01 至 2026-05 共 385 只港股 IPO（不含 SPAC）

---

## 已知边界

模型在以下场景表现良好：
- ✅ 主板已盈利的中性环境（regime≥0）
- ✅ A+H 双重上市（intl_oversub≥13x 阈值规则）
- ✅ 18C LLM 主题 LARGE 决策
- ✅ Cluster bonus 识别产业资本背书

模型在以下场景表现差或需特殊处理：
- ❌ 18A 生物医药（应单独建模，本框架不适用）
- ❌ 主板 5 日 IC 已饱和（短期定价已被市场吃掉）
- ⚠️  港股小盘股 5-30 日反指模式
- ⚠️  2022/2024 年（regime gate 自动 SKIP，但意味着策略停摆）
- ⚠️  Production 样本量小（n=8 主板进仓）— t-stat 信号需要继续累积样本

---

## 数据更新流程

如需把 iFinD 的最新数据灌进 DB：

```bash
# 1. 拉取最新 7 张表到 data/raw/ifind/（5-15 分钟）
PYTHONUTF8=1 python src/data_sources/ifind/full_data_pull.py

# 2. 灌入 SQLite + 重跑回测（流程脚本待补，目前是手工步骤）
```

详见 [src/data_sources/ifind/README.md](src/data_sources/ifind/README.md)。

## 进一步开发

要做扩展，建议优先方向：
- 实现 `data/raw/ifind/*.csv → nacs_real.db` 的自动 ETL 桥接（目前是手工流程）
- 给 18A 单独建模（biotech 字段已留接口）
- 把 `MarketEnvironment` 静态默认值改成动态从恒生指数实时拉取
- 实现 `R_lockup` 重构成加法结构（v9 计划）
- 把 regime_score 也接入 iFinD 实时窗口

---

## 模型沿革

| 版本 | 关键改进 | 主板 60d IC | Production mean | 备注 |
|---|---|---|---|---|
| Run 1 | baseline 启发式 | +0.122 | - | 仅 5d |
| Run 4 | 顶部基石标注 + L2 阈值 | +0.066 | - | 60d |
| Run 6 | iFinD 真实财务 99.7% | +0.097 | - | 60d |
| v6.5b | 修 derive_profitable + cluster bonus | +0.099 | - | 60d |
| v7 | + Regime Gate (regime≥0 子样本: **+0.247, t=2.41 ✅**) | +0.098 | +19% (含 RELATIONSHIP) | 60d |
| **v8** | **+ 砍 RELATIONSHIP 死亡区间** | +0.098 | **+42%, win 75%** | 60d production |

主要分水岭：
- v7：从"模型不显著"到"过滤后子样本极强显著"
- v8：从"打中很多但实战收益普通"到"打中少但实战回报翻倍"
