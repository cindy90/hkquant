# data/raw/manual — 人工导出的原始数据

iFinD 数据浏览器手动 SCExport 出来的 Excel, 用于补充 API 没有/不便拉的字段.
跟 `data/raw/ifind/` 的自动化 CSV 相对.

## 当前文件

| 文件 | 内容 | 来源 | 下游用途 |
|---|---|---|---|
| `hk_concept_blocks_lookup.xlsx` | 港股概念板块 ID 全集 (227 行: 板块ID / 节点层级 / 父节点ID / 是否可提取成分) | iFinD 数据浏览器 → SCExport | 派生 `data/dict/hk_concept_blocks_index.csv` 时的人工核对源 |
| `southbound_flow_2026-05-08.xlsx` | 北向/南向资金 51 行交易统计 (2026-05-08) | iFinD 数据浏览器 → SCExport | 暂时未接 ETL; 与 `probe_southbound_2026-05-08.json` 配对 |

## 命名约定

- 含日期的文件加 `_YYYY-MM-DD` 后缀 (e.g. `southbound_flow_2026-05-08.xlsx`)
- 不含日期的字典文件用语义名 (e.g. `hk_concept_blocks_lookup.xlsx`)
- **禁止** 用 `Book1.xlsx` 这种通用名 (这就是历史教训: 之前在 `data/IFIND板块ID/Book{1,2}.xlsx`,
  没人知道是什么)

## 维护

- **不直接读取**: 这是原始档, 类似 `data/raw/ifind/`. 派生数据走 `data/dict/`
  和 `data/derived/`.
- **新增手动导出**: 复制到这里 + 更新本 README 说明用途.
- **过时**: 移到 `_archive/` 子目录, 不要直接删 (审计追溯).
