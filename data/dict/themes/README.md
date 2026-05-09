# data/dict/themes — 主题/板块成分股缓存

| 文件 | 用途 | 写入脚本 |
|---|---|---|
| `constituents_cache.json` | 月度缓存: `{(bkid, YYYY-MM): [stock_code, ...]}`; 同 (bkid, month) 一个月只查 iFinD 1 次 | `scripts/fetch_hk_market_data.py::_save_constituents_cache` |
| `constituents_probe.json` | 一次性探针, 记录 iFinD 哪个接口能拉成分股 (Step 2 决策依据) | `scripts/probe_theme_constituents.py`, `scripts/verify_p03291.py` |

## Schema

`constituents_cache.json`:
```json
{
  "_schema_version": "1.0",
  "constituents": {
    "<bkid>:<YYYY-MM>": ["0001.HK", "0002.HK", ...],
    ...
  }
}
```

## 维护

- 缓存随 `fetch_hk_market_data.py` 调用按需增量更新, 不会全量重 pull
- 想强制刷新某个 (bkid, month): 手动从 JSON 删除对应 key 后重跑脚本
- 历史月份的成分股**不会**自动失效 (港股板块成分变化罕见)

## 命名约定 (2026-05-09 起)

文件移自项目早期的 `data/theme_constituents_*.json` (在 data/ 根). 移到
`data/dict/themes/` 是为了和其它板块字典 (`hs_industry_blocks.json`, etc.) 在
`data/dict/` 下统一组织. 4 个引用脚本同步更新了路径.
