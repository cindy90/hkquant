# data/derived/ipo_classification — IPO 行业/概念分类沉淀

## 文件清单

| 文件 | 形态 | 行数 | 来源 | 说明 |
|---|---|---|---|---|
| `ipo_concepts.csv` | 1:N 长表 | 321 | DB `ipo_concepts` 表导出 | **权威数据**。225/384 IPO 有概念标签 |
| `ipo_concepts_wide.csv` | 1:1 宽表 | 384 | `scripts/fetch_hk_concepts.py` | 每只股票一行，`concept_names` 列以 `|` 分隔 |
| `concept_coverage.csv` | 字典统计 | 223 | 同上 | 每个概念命中的 IPO 数 + 在港股池总成员数 |
| `ipo_industries.csv` | 1:N 长表 | 592 | DB `ipo_industries` 表导出 | **权威数据**。`source` ∈ {`sw`, `ths_global`} |
| `ipo_industries_wide.csv` | 1:1 宽表 | 384 | `scripts/fetch_hk_industries.py` | `sw_path`、`ths_global_path` 用 ` \| ` 分隔层级 |
| `industry_coverage.csv` | 覆盖率统计 | 2 | DB | sw=54.2%, ths_global=100% |

## 表 schema

### ipo_concepts.csv (1:N)
```
ipo_id, stock_code, concept_id, concept_name, data_date
```
PK = (ipo_id, concept_id)

### ipo_industries.csv (1:N)
```
ipo_id, stock_code, source, l1_name, l2_name, l3_name, l4_name,
leaf_bid, leaf_level, data_date
```
PK = (ipo_id, source)

## 注意事项

- **恒生行业**未在此目录，存于 `ipo_master.gics_l2`（文本字段，"L1(HS)-L2(HS)-L3(HS)"格式）。命名虽叫 GICS 实际是 HS 分类，待重命名
- 159 个 IPO 概念命中数=0，集中在 2022-2025 中小新股
- `ipo_industries.csv` 中 `source=sw` 仅 208 行（A+H 偏置），`source=ths_global` 384 行（100% 覆盖）

## 重新生成

```bash
# 拉概念（写 outputs/ipo_concepts_summary.csv + DB ipo_concepts 表）
python scripts/fetch_hk_concepts.py

# 拉申万 + 同花顺全球
python scripts/fetch_hk_industries.py

# 从 DB 导出权威长表
sqlite3 data/nacs_real.db < scripts/export_classification.sql  # （待写）
```

## 数据日期

`data_date = 2026-05-09`
