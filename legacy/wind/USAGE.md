# NACS 数据收集 — 使用指南

## 你将要做的事

1. 用 Wind Excel 插件填好这个模板 (~9 个 sheet)
2. 把价格历史导出到一个 `prices/` 目录下的 N 个 CSV
3. 把整套发回, loader 自动灌库 + 跑回测

预计工时: **3-5 天** (取决于数据完整性目标)

---

## 文件清单

```
nacs_data_template.xlsx       # 主模板
loaders/wind_loader.py        # 灌库脚本 (你不需要跑)
prices/                       # 你需要建的目录
  ├── 03296_HK.csv
  ├── 02476_HK.csv
  └── ...
```

---

## 工作流（按 sheet 顺序）

### Step 0 — 打开模板

打开 `nacs_data_template.xlsx`，依次过一遍：
- **00_README**: 看一下颜色规则和注意事项
- **91_wind_formulas**: 这是 Wind 公式速查表，**收藏好**

### Step 1 — IPO 主表 (sheet `01_ipo_master`)

#### 1a. 拉 IPO 列表

在 Wind 终端任意空白单元格运行：

```excel
=WSET("newstock","startdate=2022-01-01;enddate=2026-04-30;exchange=hkex;sectorcode=a201020700000000")
```

⚠ **关键**: 右键函数 → 选项 → 勾选 **"包含已退市/已私有化"**，否则有幸存者偏差。

#### 1b. 把代码列表粘贴到 sheet01 的 A 列

预计 ~250 行。

#### 1c. 用 Wind 公式拉横截面字段

模板第4行已有公式模板，把它们复制到第3行(华勤示例下面的实际数据行)，然后下拖到所有行。

或者一次性拉多个字段（更高效）：

```excel
=WSS($A3,"ipo_listdate,ipo_setdate,ipo_price,ipo_amount,ipo_intoversub,ipo_puboversub,ipo_pe,ipo_underwriter,sec_gicsindustryname2,sec_status,delist_date")
```

返回值会按字段顺序排成一行，你需要分列。

#### 1d. 手填的字段 (Wind 没有)

- `listing_chapter`: 看招股书首页判断 → 下拉框选择
  - 主板已盈利 → `main_board_profitable`
  - A+H → `a_plus_h`
  - 18A → `18a`
  - 18C 已商业化 → `18c_commercial`
  - 18C 未商业化 → `18c_precommercial`
- `is_a_h`: 0/1
- `a_share_code`: 如果是A+H，填A股代码 (如 `603296.SH`)
- `pe_peer_median`: 这个**很重要**，但 Wind 没现成的——从同行业可比 5-10 家算中位数
- `last_round_premium`: 招股书"主要股东"章节查 Pre-IPO 最后一轮估值，算 `(IPO估值 / 上轮估值) - 1`
- `sponsor_tier`: 见数据字典 sheet, 1=中金/MS/GS, 2=海通/招银, 3=其他
- `auditor_tier`: 1=四大, 2=本地大所, 3=其他

### Step 2 — 基石明细 (sheet `02_ipo_cornerstones`)

这是**最费时**的部分。Wind 没有现成函数。

#### 2a. 数据来源

去 [HKEX 披露易](https://www.hkexnews.hk) 下载招股书最终版（不是聆讯材料）。基石名单在招股书的 **"基石投资者" (Cornerstone Investors)** 章节，通常在"全球发售有关协议"后面。

#### 2b. 复制的格式

每只 IPO 的每个基石占一行。**关键字段**：

| 字段 | 说明 |
|---|---|
| `cornerstone_raw_name` | **招股书原文！不要归一**，例如直接写 "Green Better" |
| `cornerstone_full_name` | 完整名带说明，例如 "Green Better (小米集团 01810.HK 全资子公司)" |
| `ticket_amount_value` | 认购金额数值 |
| `ticket_amount_ccy` | 币种，**通常招股书披露的是 USD**，单独标注 |
| `affiliation_disclosed` | 招股书"关连人士"章节是否披露 → 1=有, 0=明确无, unknown=未提 |

⚠ 招股书里通常写 **"基石投资者已同意认购总金额约 X 亿美元"**，X 通常是总额而**不是逐家**。

**两种处理**：
- **理想**：18家逐家披露 (华勤的招股书是这样)
- **粗放**：只有总额时，按金额平均分到 N 家，把每行 `data_source` 改成 `"prospectus_estimated"`

#### 2c. 关联性判定 (`affiliation_disclosed`)

判定标准（**任一**触发即标 `1`）：
- 招股书"关连人士"章节直接披露
- 该基石与发行人是 **客户/供应商关系**（如华勤的小米/豪威）
- 该基石与发行人 **同集团**或 **共同控股股东**
- 该基石是 **保荐人/审计师/法律顾问** 的母公司或子公司

判定标准模糊时填 `unknown`，**别瞎猜**——loader 会用类型先验代偿。

### Step 3 — 公司基本面 (sheet `03_company_fundamentals`)

招股书"业务" + "财务资料" 章节。每只 IPO 一行，N-3, N-2, N-1 是招股书披露的最近3个会计年度。

⚠ 18A/18C 公司多数无营收或营收很小，填 0 即可，loader 会按 chapter 自动切换打分子流程。

### Step 4 — CCASS 解禁日数据 (sheet `04_ccass_unlocks`)

只对**已解禁**的 IPO 填 (上市日 + 6个月 < 今天)。

数据源（任选其一）：
- Wind: `=WSS("03296.HK","ccass_pct;tradeDate=2026-10-23")`
- HKEX 官网 [CCASS Stock Tracker](https://www3.hkexnews.hk/sdw/search/searchsdw.aspx)

填 4 个时点的基石持仓占比：解禁前30天 / 解禁日 / 解禁后30天 / 解禁后90天。**这是"基石锁定期纪律分"的关键数据**。

### Step 5 — 恒指日频 (sheet `05_hsi_macro`)

一次性拉全期间最高效：

```excel
=WSD("HSI.HI","close,amt","2022-01-01","2026-04-30","")
```

把返回的二维数据粘到 sheet05 即可（约 1300 行）。

恒科指 / HIBOR / 南向资金类似，模板里有公式。

### Step 6 — 价格历史 (sheet `06_price_index` + `prices/` 目录)

#### 6a. 在 sheet06 用公式自动生成每只 IPO 要拉的价格区间

公式参考模板第 3 行。

#### 6b. 给每只 IPO 拉价格

对每只 IPO，运行类似：

```excel
=WSD("03296.HK","close,amt,trade_status","2026-04-18","2027-05-28","")
```

- 起止: 上市日前 5 天 → 上市日后 400 天 (覆盖 M+12 + 解禁后 90 天)
- 把每只 IPO 的输出**保存为独立 CSV**，命名规则: `{stock_code}.csv`，但点改下划线，例如 `03296_HK.csv`

⚠ 250 只 IPO × 400 天 ≈ 100,000 行，单文件太大查询慢，所以分文件存。

#### 6c. CSV 列要求

至少 3 列：`date`, `close`, `amt` (或 `turnover`)。列名容忍中英文，loader 会自动识别。

---

## 数据采集质量自检

填完后，跑这个**自检公式**：

```excel
=COUNTA('01_ipo_master'!A:A)-3   ' IPO 总数, 应 ≈ 250
=COUNTA('02_ipo_cornerstones'!A:A)-3  ' 基石明细数, 应 ≈ 1500-2500
=COUNTA('03_company_fundamentals'!A:A)-3  ' 应等于 IPO 总数
```

---

## 常见陷阱

| # | 陷阱 | 怎么避免 |
|---|---|---|
| 1 | 漏了已退市股票 | 务必勾选 Wind "包含已退市" |
| 2 | A+H 公司用了 A 股代码 | 一律用 H 股代码 (如 `03296.HK`) |
| 3 | 基石原文做了归一 | **保持招股书原文**，归一是 loader 的事 |
| 4 | 认购金额单位混用 | 在 `ticket_amount_ccy` 列明确标注 |
| 5 | 把聆讯材料当招股书 | 用最终版，基石可能在路演期间增减 |
| 6 | `pe_peer_median` 留空 | 这个字段很重要，缺失会让估值层打分失真 |
| 7 | 18A/18C 公司硬塞财务字段 | sheet03 留空即可，loader 会按 chapter 切流程 |

---

## 交付清单

```
nacs_data_filled.xlsx              # 你填好的模板
prices/                            # 价格目录
  ├── 03296_HK.csv
  ├── 02476_HK.csv
  └── ... (~250 个文件)
```

把 zip 整个发过来。Loader 会输出：

- `nacs.db` 物化数据库（可视化用 [DB Browser for SQLite](https://sqlitebrowser.org/) 直接打开）
- `data_quality_report.md` 缺失字段报告
- `backtest_report.md` 真实回测报告（含 Decile / IC / L-S / 子样本）

---

## 你可能问的问题

**Q: 没有 CCASS 数据怎么办？**
A: 不填 sheet04 即可。Loader 会用"解禁后股价回撤"作为锁定期纪律的代理指标，回测精度会低一点但能跑。

**Q: 基石详细 ticket size 拿不到怎么办？**
A: 在 sheet02 把每只 IPO 的总额平均分到 N 家，`data_source` 写 `"prospectus_estimated"`。模型会给这部分项目 `data_quality_score` 折扣。

**Q: 18C 样本太少 (~10 只) 怎么处理？**
A: 主回测自动剔除 18C，单独做 case study。这是模板设计的，你只管填。

**Q: 国际配售认购倍数 Wind 拉不到？**
A: 看配售结果公告，HKEX 上市公告里通常会写 "国际配售约 N 倍"。这个字段**一定要填**，不然回测会把无该字段的 IPO 按倍数 < 1.5 否决。

**Q: 我可以分阶段交付吗？**
A: 完全可以。先把 sheet01 + sheet02 给我，我可以先跑一版"基石生态分"单独的回测，验证 Q_ecosystem 是否对收益有显著解释力。其他 sheet 后续补。

---

## 准备好了？

如果你看完这份指南，建议先**用1-2只熟悉的 IPO 走一遍流程**，确认你能从 Wind 取出我列的所有字段。这能在批量做之前发现潜在问题。

如果某些 Wind 字段(尤其 `ipo_intoversub`)你的 Wind 终端拉不到，告诉我具体是哪些，我看看用别的字段名/路径替代。
