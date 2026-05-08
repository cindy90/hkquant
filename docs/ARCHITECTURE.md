# NACS 模型架构

## 评分公式

```
NACS = Q_company × Q_ecosystem × (1 - R_lockup)
```

三因子结构 — 每个因子独立计算并归一化到 [0, 1]，相乘得到原始 NACS（也在 [0, 1]）。

### Q_company（公司基本面，Layer 1）

按上市章节分别评分：
- **主板已盈利**：营收 CAGR + 毛利率趋势 + ROE + 净负债 + FCF 持续年数
- **18A 生物医药**：核心管线阶段 + 二期+管线数 + 现金跑道 + BD 交易
- **18C 特专科技**：商业化状态 + 营收增速 + 里程碑分 + R&D 强度

L1 否决条款：`intl_oversubscription < 1.5x` → Q_company = 0（国际配售认购不足意味着定价被砸盘）

### Q_ecosystem（基石生态，Layer 2）

7 子项加权（v7 新增 cluster bonus 在 raw 计算后 multiply）：
- 0.30 × Q_weighted（基石个体加权质量分）
- 0.15 × coverage（基石/募资额覆盖率）
- 0.10 × HHI（集中度反向）
- 0.10 × diversity_entropy（基石类型多样性）
- 0.20 × pollution（关联污染反向）
- 0.10 × synergy（产业协同分）
- 0.05 × zucou（国资凑数红旗）
- **× cluster_bonus（v7 新增）**：cluster_count ≥2/3/5 → ×1.10/1.15/1.20

L2 否决条款：
- `affiliation_pct > 50%` → Q_e ≤ 30/100
- `Q_weighted < 30` → Q_e ≤ 40/100
- 基石数 <3 且最大单一占比 >60% → Q_e ≤ 35/100

### R_lockup（锁定期风险，Layer 3）

5 维加法（不是乘法 — 这点是已知的设计弱点，待后续重构）：
- lockup_months / overhang_ratio / fundamental_risk_score / peer_lockup_avg_drawdown / pe_vs_history_pct

R_lockup 越大，`(1 - R_lockup)` 越小，最终 NACS 越低。

## v7 调整链

```
Q_c × Q_e × (1 - R_l) = nacs_raw

→ 应用章节调整:
   18C: ×0.70
   A+H 可融券: ×1.10
   第二上市: ×0.85
   控股股东重大关联交易: ×0.85

→ nacs_adjusted (clipped to [0, 1])

→ 仓位映射 (_position_from_nacs):
   ≥0.55 → FULL (100%)
   ≥0.45 → LARGE (70%)
   ≥0.35 → TRIAL (40%)
   ≥0.25 → RELATIONSHIP (15%)
   <0.25 → SKIP (0%)

→ ★ v7 Regime Gate (最后应用):
   regime_score < 0 → 强制 SKIP (覆盖以上决策)
                       但 nacs_adjusted 数值保留 (透明度)
```

## 数据流

```
原始数据 (Wind/iFinD)
    ↓
data_cleaner.py → 清洗、关联匹配、affiliation_flag 派生
    ↓
nacs_real.db (SQLite)
    ↓
run_v7_backtest.py:
    1. 加载 IPO + 基石 + 财务 + 收益
    2. 算每只 IPO 的 regime_score
    3. 算每只 IPO 的 cluster_cornerstone_count
    4. 构造 IPOOffering → compute_nacs() → NACSResult
    ↓
nacs_v7_scores.csv + IC 报告
```

## 关键设计决策

### 为什么 regime gate 是 conditional 而不是 multiplicative？

实验过：用 regime_score 做单因子（ρ ≈ 0），不显著。
但用作"开关"（>0 才信任 NACS）时，过滤后子样本的 IC 从 +0.09 → +0.25。

这是因为 `regime_score` 不预测**哪只 IPO 会涨**，它预测**当下市场环境是否适合用 NACS 这个模型**。
本质是 model **conditional applicability**，不是 alpha 因子。

### 为什么 cluster_count 比 affiliation_pct 更准？

旧的 `affiliation_flag` 用"基石占融资比例 ≥33%"做启发式 → 假阳性高，把所有大基石都当成关联方。

升级后用三向匹配：
1. `cornerstone_name` 含 IPO 公司核心词 → flag=1（强关联，应减分）
2. `ultimate_holder` 含 IPO 公司核心词 → flag=1
3. **同 IPO 内多个基石共享 `ultimate_holder`** → flag=2（簇基石，**应加分**）

第三类（簇基石）才是真有价值的信号——产业资本/家族办公室通过多 SPV 重仓时，60d 收益更高且波动更小。

### 为什么 NACS = Q_c × Q_e × (1-R_l) 而非加法？

乘法结构对**任何一个维度极差**都敏感：基本面差 → Q_c 接近 0 → NACS 接近 0；基石垃圾 → Q_e 接近 0 → NACS 接近 0。

加法（Q_c + Q_e − R_l）会让"一个维度差但其他维度好"的 IPO 还能高分。基石策略需要**全维度都过关**才进。

副作用：R_lockup 维度有方差时，`(1 - R_l)` 的乘法对结果敏感。这是已知的设计弱点 — 计划 v8 重构 R_lockup 为加法。

## 模型 = 评分 + 决策器

注意 NACS 不是单纯的 alpha 信号 — 它直接输出仓位决策（FULL/LARGE/TRIAL/RELATIONSHIP/SKIP）。

这意味着评估指标也是双层的：
- IC（rank correlation）：测评分单调性
- L-S spread / t-stat：测顶/底分位的实际收益差
- 决策迁移矩阵：测真实决策有效性

v7 的关键卖点是**所有三个指标在 regime≥0 子样本上同时显著**：
- IC=+0.247 ✓ (>1.96σ)
- L-S spread=+34.85%
- t=2.41 ✅ (>2)
