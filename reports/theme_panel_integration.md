# 主题板块 ID 整合 (Step 1 + Step 2 完成)

生成时间: 2026-05-08
范围: 把 12 个 iFinD 主体概念板块 `iv_bkid` 落到主题 watchlist (Step 1),
      并接入 `THS_DR(p03291)` 拉成分股 + 批量 `THS_HistoryQuotes` 等权合成主题 close 序列 (Step 2).

## 背景

原系统 5 个 AI 主题(`fetch_hk_market_data.py:DEFAULT_THEMES`)中:
- 4 个用 `HSTECH.HK` 粗代理 → 数值完全相同, 信息熵≈0
- 1 个用 `930967.CSI`

用户从 iFinD 找出 12 个 `iv_bkid` 涵盖 AI / 机器人 / 半导体 / 数据中心
/ 脑机接口 / 汽车 / 基建 / 次新股 / 新经济等更广维度.

## 关键事实(由用户验证)

`THS_HistoryQuotes('011007_305848', 'close', '', sdate, edate)` →
**iFinD 报「无效命令」**. 即 `iv_bkid` **不能直接当行情代码**.

→ 必须走「成分股 → 等权合成」路径(原 `fetch_hk_market_data.py:428` TODO).

## Step 1 改动(本次完成)

| 文件 | 操作 |
|---|---|
| `data/watchlist.json` | 新建; 12 主题完整 metadata, 含 `iv_bkid` + `fallback_quote_code` + `proxy_status="iv_bkid_pending_constituents_api"` |
| `scripts/fetch_hk_market_data.py` | `fetch_themes` 兼容 v1/v2 schema; 新增 `_resolve_theme_quote_code` 辅助函数; 输出 `themes.json` 多了 `iv_bkid` / `proxy_status` 字段 |
| `scripts/probe_theme_constituents.py` | 新建; 探测哪个 iFinD 接口能拉板块成分股 |
| `reports/theme_panel_integration.md` | 本文档 |

**向后兼容性**: 旧 v1 watchlist (`{"ths_code": ..., ...}`) 仍能跑.
不破坏任何现有日报流程.

## 12 主题映射

| theme_key | label | iv_bkid | fallback_quote_code | 来源 |
|---|---|---|---|---|
| `ai_server` | 数据中心 | `011007_308847` | `HSTECH.HK` | 替代旧 ai_server |
| `llm` | 人工智能 | `011007_305848` | `HSTECH.HK` | 替代旧 llm |
| `humanoid_robot` | 机器人概念 | `011007_309172` | `HSTECH.HK` | 替换粗代理 |
| `semi_localization` | 半导体概念 | `011007_309171` | `930967.CSI` | 替换粗代理 |
| `ai_driving` | 无人驾驶 | `011007_305874` | `HSTECH.HK` | 替换粗代理 |
| `aigc` | AIGC 概念 | `011007_309047` | `HSTECH.HK` | 新增 |
| `bci` | 脑机接口 | `011007_309178` | `HSTECH.HK` | 新增 |
| `new_it` | 新 IT 概念 | `011007_308767` | `HSTECH.HK` | 新增 |
| `auto_manufacturing` | 汽车制造 | `011007_301988` | `HSTECH.HK` | 新增 |
| `infrastructure` | 基础建设 | `011007_308924` | `HSCI.HK` | 新增 |
| `new_listings` | 次新股 | `011007_301740` | `HSTECH.HK` | 新增 |
| `new_economy` | 新经济 | `011007_308430` | `HSTECH.HK` | 新增 |

## Step 1 当前局限(须知)

1. **数值仍粗代理**: 12 主题的 `ret_1d/5d/20d/60d` 来自 `fallback_quote_code`,
   不是真实成分股合成. 9 个主题使用同一个 `HSTECH.HK`, 导致这 9 个数值仍然相同.
2. **`iv_bkid` 仅作 metadata**: 暂无下游消费者,只是为 Step 2 做准备.
3. **MarketEnvironment 不受影响**: 实时化接入(上一轮工作)与本次 watchlist 整合
   是独立模块, 互不依赖.

## Step 2 实施(已完成 2026-05-08)

### Winner 接口

用户指定:
```python
THS_DR('p03291',
       'date=YYYYMMDD;blockname=<iv_bkid>;iv_type=allcontract',
       'p03291_f001:Y,p03291_f002:Y,p03291_f003:Y,p03291_f004:Y',
       'format:dataframe')
```

字段语义(`scripts/verify_p03291.py` 跑过 3 个 bkid 验证):
| 列 | 语义 |
|---|---|
| `p03291_f001` | 日期 |
| `p03291_f002` | **ths_code** (取这列, 含 `.HK` 后缀) |
| `p03291_f003` | 简称 |
| `p03291_f004` | 简称 |

### 落地代码

`scripts/fetch_hk_market_data.py`:
- 新增 `_hq_unpack_batch(result)` — 解多 code `THS_HistoryQuotes`(每个 thscode 一个 table)
- 新增 `_load/_save_constituents_cache()` — 月度缓存到 `data/theme_constituents_cache.json`
- 新增 `_fetch_theme_constituents(bkid, asof)` — 调 p03291, 取 f002 列, 过滤 `.HK`, 月度缓存
- 新增 `_compose_theme_close_series(codes, sdate, edate)` — 1 次批量 `THS_HistoryQuotes` 拉所有 codes 的 close,
  每只按首个有效 close 归一化为 1, 用 pandas DataFrame 对齐时间, ffill 后等权 mean
- 重写 `fetch_themes` — 路径选择: 合成 → 失败回退 `fallback_quote_code` → 失败再 v1 兼容

### 调用量(实测)

- 12 次 `THS_DR(p03291)`(成分股,**月度缓存**,同月再跑 0 次)
- 12 次批量 `THS_HistoryQuotes`(每主题 1 次, 用逗号串接全部成分股)
- **合计 24 次/日(首月) → 12 次/日(后续)**, 比单只逐个调用的 ~360 次 **节省 96%**

### 验证结果(2026-05-08)

```
ai_server          数据中心   composed n=33   60d=+4.69%
llm                人工智能   composed n=37   60d=+8.08%
humanoid_robot     机器人概念 composed n=33   60d=-1.70%
semi_localization  半导体概念 composed n=12   60d=+33.70%
ai_driving         无人驾驶   composed n=23   60d=-1.31%
aigc               AIGC概念   composed n=11   60d=+36.34%
bci                脑机接口   composed n=4    60d=-6.71%
new_it             新IT概念   composed n=13   60d=-4.58%
auto_manufacturing 汽车制造   composed n=25   60d=-9.42%
infrastructure     基础建设   composed n=48   60d=+7.06%
new_listings       次新股     composed n=148  60d=+3.12%
new_economy        新经济     composed n=77   60d=+8.92%

汇总: composed=12  fallback=0  total=12
```

**信息熵恢复**:60d 收益区间 [-9.4%, +36.3%],12 个主题不再全部相同(原 9 个用 HSTECH.HK 数值完全相同).

### 已知局限

- `bci` 仅 4 只成分股,信号噪声较高(但 ≥ 2 仍能合成,不致 fallback).
- 合成法用 `ffill` 处理停牌,极端情况(整月停牌)会失真,但对市值加权指数影响有限.
- 当前是等权,不是市值加权;后续可增 `_fetch_market_caps()` 改成市值加权(如需).
- `proxy_status='composed_path_active'` 在 watchlist 里只是元数据;运行时 `themes.json` 的 `proxy_status` 字段才是真实状态(`composed` / `composition_failed_using_fallback`).

## 验证步骤

### Step 2 端到端(推荐, 最小耗时)

```bash
# 单跑 fetch_themes 模块 (跳过 market_data / news / ipo_recent)
python scripts/verify_theme_compose.py
# 期望: 12 主题全部 composed, n_constituents 与板块实际匹配,
#      data/theme_constituents_cache.json 写入 12 个 (bkid, month) 缓存,
#      daily/{today}/themes.json 含 proxy_status='composed' 字段.
```

### 完整 daily

```bash
python scripts/fetch_hk_market_data.py
# 输出: daily/{today}/themes.json + market_data.json + news_today.json + ipo_recent.json
```

### 旧版 Step 1 验证(保留参考)

```bash
python -c "import json; d=json.load(open('data/watchlist.json',encoding='utf-8'));
           print(len(d['themes_to_track']), '主题');
           print(list(d['themes_to_track'].keys()))"
```
