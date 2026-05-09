"""
overrides.yaml 加载与应用单元测试.

覆盖:
    - load_overrides 文件不存在时返回 {}
    - lint_overrides 校验 _reason / _source 必填
    - apply_ipo_overrides 字段级合并
    - apply_ipo_overrides 不修改原 list (返回新)
    - 元数据字段 (_reason / _source) 不写入合并结果
    - 不在 ALLOWED_IPO_FIELDS 的字段被忽略
"""
from __future__ import annotations

import pytest


def test_load_overrides_missing_file_returns_empty(tmp_path):
    from data_sources.ifind.overrides import load_overrides
    p = tmp_path / "nonexistent.yaml"
    assert load_overrides(p) == {}


def test_load_overrides_actual_project_file_lints_clean():
    from data_sources.ifind.overrides import load_overrides, lint_overrides
    ov = load_overrides()
    errs = lint_overrides(ov)
    assert errs == [], f"项目 overrides.yaml 不应有 lint 错误: {errs}"


def test_lint_requires_reason_and_source():
    from data_sources.ifind.overrides import lint_overrides
    bad = {"ipo_info": {"0001.HK": {"listing_date": "2024-01-01"}}}
    errs = lint_overrides(bad)
    assert any("_reason" in e for e in errs)
    assert any("_source" in e for e in errs)


def test_lint_rejects_non_allowed_field():
    from data_sources.ifind.overrides import lint_overrides
    bad = {"ipo_info": {"0001.HK": {
        "this_field_doesnt_exist": "x",
        "_reason": "test", "_source": "test",
    }}}
    errs = lint_overrides(bad)
    assert any("不允许覆盖" in e for e in errs)


def test_apply_ipo_overrides_merges_fields():
    from data_sources.ifind.overrides import apply_ipo_overrides
    rows = [
        {"stock_code": "0001.HK", "listing_date": "--", "company_name_zh": "甲"},
        {"stock_code": "0002.HK", "listing_date": "2024-06-01", "company_name_zh": "乙"},
    ]
    overrides = {"ipo_info": {
        "0001.HK": {"listing_date": "2024-01-01",
                    "_reason": "x", "_source": "y"},
    }}
    out = apply_ipo_overrides(rows, overrides)
    assert out[0]["listing_date"] == "2024-01-01"
    assert out[0]["company_name_zh"] == "甲"  # 未被覆写
    assert out[1] == rows[1]  # 没匹配的行不变


def test_apply_does_not_mutate_input():
    from data_sources.ifind.overrides import apply_ipo_overrides
    rows = [{"stock_code": "0001.HK", "listing_date": "--"}]
    out = apply_ipo_overrides(rows, {"ipo_info": {
        "0001.HK": {"listing_date": "2024-01-01", "_reason": "x", "_source": "y"}
    }})
    assert rows[0]["listing_date"] == "--"  # 原 list 不变
    assert out[0]["listing_date"] == "2024-01-01"


def test_apply_skips_underscore_meta_fields():
    """_reason / _source 不应作为字段写进合并结果"""
    from data_sources.ifind.overrides import apply_ipo_overrides
    rows = [{"stock_code": "0001.HK"}]
    out = apply_ipo_overrides(rows, {"ipo_info": {
        "0001.HK": {"listing_date": "2024-01-01",
                    "_reason": "x", "_source": "y"}
    }})
    assert "_reason" not in out[0]
    assert "_source" not in out[0]
    assert out[0]["listing_date"] == "2024-01-01"


def test_apply_with_no_overrides_passthrough():
    from data_sources.ifind.overrides import apply_ipo_overrides
    rows = [{"stock_code": "0001.HK"}]
    assert apply_ipo_overrides(rows, {}) == rows
    assert apply_ipo_overrides(rows, {"ipo_info": {}}) == rows


# =============================================================================
# Integration: load_ipo_info 行为变化
# =============================================================================

def test_load_ipo_info_picks_up_overridden_listing_date(tmp_path):
    """raw CSV 里 listing_date='--' 的 IPO, overrides 提供日期后能进 DB"""
    import sqlite3
    from data.schema import SCHEMA_SQL
    from data_sources.ifind.load_to_db import load_ipo_info

    csv_path = tmp_path / "ipo.csv"
    csv_path.write_text(
        "p05310_f001,p05310_f033,p05310_f028,p05310_f002\n"
        "0001.HK,--,--,Test\n"
        "0002.HK,2024-06-01,2024-05-25,Test2\n",
        encoding="utf-8",
    )
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)

    overrides = {"ipo_info": {"0001.HK": {
        "listing_date": "2024-03-15",
        "_reason": "test", "_source": "test",
    }}}
    stats = load_ipo_info(conn, csv_path, overrides=overrides)
    assert stats.n_inserted == 2
    assert stats.n_overrides_applied == 1

    rows = {r["stock_code"]: r for r in
            conn.execute("SELECT stock_code, listing_date FROM ipo_master")}
    assert rows["0001.HK"]["listing_date"] == "2024-03-15"
    assert rows["0002.HK"]["listing_date"] == "2024-06-01"
