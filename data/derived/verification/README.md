# data/derived/verification — DB 字段对 iFinD 一致性校验

## 文件清单（2026-05-09 快照）

| 文件 | 行数 | OK | MISMATCH | 含义 |
|---|---|---|---|---|
| `chapter_report_20260509.csv` | 384 | 368 | 16 | 全部 IPO 上市章节（main_board_profitable / 18a / 18c / a_plus_h / secondary）逐条对照 |
| `chapter_mismatch_20260509.csv` | 16 | 0 | 16 | 仅不一致行的明细 |
| `industry_report_20260509.csv` | 384 | 354 | 30 | 全部 IPO 恒生三级行业逐条对照（DB.gics_l2 vs iFinD 011002 反查） |
| `industry_mismatch_20260509.csv` | 30 | 0 | 30 | 不一致明细（实际 100% 仅命名差异，详见说明） |

## 说明

### chapter_report
状态：**修复前快照**。本会话已通过 `scripts/fix_listing_chapter.py` 类逻辑修复 DB，理论上重新跑一次会得 `MISMATCH=0`。
参考 DB 备份：`data/nacs_real.db.bak_p3_chapter_20260509_164748`

### industry_report
状态：**纯命名差异**。30 条 MISMATCH 全部是格式差：
- 后缀 `Ⅲ`：`半导体Ⅲ` (DB) vs `半导体` (iFinD)
- 别名：`商用运输工具及货车` (DB) vs `商业用车及货车` (iFinD)

经规范化处理后一致性 = 100%。**未修改 DB**（保留历史命名）。规范化映射写在 `scripts/verify_industry_via_ifind.py` 内。

## Schema

### chapter_report
```
ipo_id, stock_code, stock_code_normalized, company_name_zh,
listing_date, db_chapter, inferred_chapter, ifind_block_hits, status
```

### industry_report
```
ipo_id, stock_code, company_name_zh, listing_chapter,
db_l1, db_l2, db_l3,
inferred_l1, inferred_l2, inferred_l3, inferred_bids, n_hits, status
```

## 重新生成

```bash
python scripts/verify_listing_chapter.py    # → outputs/verify_chapter_*.csv
python scripts/verify_industry_via_ifind.py  # → outputs/verify_industry_*.csv
```
