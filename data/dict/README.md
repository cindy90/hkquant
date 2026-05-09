# data/dict — 板块字典

港股 iFinD 板块 ID 字典。结构均为 `{block_id: [stock_codes]}`，由 `THS_DataPool` 拉取得到。

## 文件清单

| 文件 | 块体系 | bid 段 | 末级层数 | 末级数量 | 上游脚本 |
|---|---|---|---|---|---|
| `hs_industry_blocks.json` | 恒生行业类 | 011002 | L5（三级末叶） | 112 | `scripts/verify_industry_via_ifind.py` |
| `hk_concept_blocks.json` | 港股概念类 | 011007 | L3（概念） | 223 | `scripts/fetch_hk_concepts.py` |
| `ths_global_industry_blocks.json` | 同花顺港股全球行业类 | 011003 | L6 | 163 | `scripts/fetch_hk_industries.py` |
| `sw_industry_blocks.json` | 港股申万行业类 | 011008 | L5 | 346 | `scripts/fetch_hk_industries.py` |

## 配套人类可读索引（CSV）

| CSV | 说明 |
|---|---|
| `hs_industry_blocks_index.csv` | block_id, l1/l2/l3 名称, member_count, has_name（87/112 命名来自 verify_industry_report 反推） |
| `hk_concept_blocks_index.csv` | concept_id, concept_name, member_count, has_db_name |
| `ths_global_industry_blocks_index.csv` | block_id, block_name（DB 反推叶节点名）, member_count, has_db_name |
| `sw_industry_blocks_index.csv` | 同上 |

## 字段口径

- `block_id`: iFinD 块 ID，例如 `011002005003001` 表示「恒生行业类→五段层级→末叶」
- `member_count`: 该块当日（拉取日 2026-05-09）的港股成员数
- 拉取参数 `data_date = 2026-05-09`

## 注意事项

- 申万分类对港股覆盖率仅 54%（主要标 A+H 联合体），本字典含全部 346 末级，但 ~80 个块在港股侧无成员（A 股专属类目）
- 部分恒生末级在港股 0 成员（如 SPAC、宠物用品），属正常现象
- 概念块对新/小股票覆盖较弱（159/384 IPO 命中数=0），iFinD 概念库本身偏稀疏

## 重新生成

```bash
python scripts/fetch_hk_concepts.py     # → outputs/concept_blocks.json
python scripts/fetch_hk_industries.py   # → outputs/industry_blocks_*.json
python scripts/verify_industry_via_ifind.py  # → outputs/verify_industry_blocks.json
```

随后将 `outputs/*.json` 按上表映射搬迁至本目录并重命名。
