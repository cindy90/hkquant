# `ifind_blocks.csv` 当前不可用

## 现象
- 文件已重命名为 `ifind_blocks.csv.broken` 防止 ETL 误读
- 头部声明 `table,block_name`, 但行内容是 Python `list` 字面量被原样写入了 CSV (一行约 200KB 的日期重复)
- `pandas.read_csv` 直接抛 `ParserError: Expected 9 fields in line 21, saw 10`

## 影响
- `src/data_sources/ifind/field_mappings.py::BLOCKS` 的 `BLOCK_TO_CHAPTER` 映射 (18A / 18C / AH / 18B_SPAC) 无上游可用
- `src/data_sources/ifind/load_to_db.py` 的 ETL 当前并未消费 blocks (注释里明示 "列式 dump 需要重 pull"), 所以章节判定靠 `data/dict/hk_concept_blocks_index.csv` 等字典 + 板块成分 JSON 拼出来
- `data/derived/verification/chapter_mismatch_*.csv` 共 16 例 DB ↔ 板块字典推断不一致, 需要 blocks 表恢复后再 reconcile

## 修复计划
1. 重新 pull: `python scripts/probe_theme_constituents.py` (需 iFinD 凭据), 输出预期 schema 为 `thscode, security_name, block_name`
2. 落库到 `data/raw/ifind/ifind_blocks.csv` (语义化列名, 不含 p05xxx_fxxx 代号)
3. 在 `load_to_db.py` 增加 `load_blocks(conn, csv_path)` loader, 把 18A/18C/AH/SPAC 章节注解写到 `ipo_master.listing_chapter` 的 reconcile 报告

## 不要做的事
- 不要把 `.broken` 改回 `.csv` 当作可用上游 — `field_mappings.parse_*` 不会拒绝结构性错误, ETL 会把垃圾数据写进 DB
