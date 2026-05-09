"""
deal_loader 单元 + 集成测试.

覆盖:
    - lint_deal 强制必填 (stock_code, expected_listing_date, listing_chapter)
    - 不允许的 ipo_master_overrides 字段被 lint 拒
    - 不合法的 listing_chapter / cornerstone_type / currency 被拒
    - load_deal_dict 写 ipo_master 一行 + N 个 cornerstone link
    - 重复跑同一 deal 是 update 不是 duplicate
    - cornerstones 货币换算正确
    - status 自动: 未来 + 没 oversub → prospectus
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest


# =============================================================================
# lint_deal
# =============================================================================

def test_lint_passes_minimal_valid():
    from data.deal_loader import lint_deal
    data = {
        "stock_code": "1187.HK",
        "expected_listing_date": "2026-01-01",
        "listing_chapter": "main_board_profitable",
    }
    assert lint_deal(data) == []


def test_lint_requires_three_required_fields():
    from data.deal_loader import lint_deal
    errs = lint_deal({})
    keys = " ".join(errs)
    assert "stock_code" in keys
    assert "expected_listing_date" in keys
    assert "listing_chapter" in keys


def test_lint_rejects_invalid_chapter():
    from data.deal_loader import lint_deal
    errs = lint_deal({
        "stock_code": "1.HK",
        "expected_listing_date": "2026-01-01",
        "listing_chapter": "bogus_chapter",
    })
    assert any("listing_chapter" in e and "bogus" in e for e in errs)


def test_lint_rejects_unknown_master_overrides():
    from data.deal_loader import lint_deal
    errs = lint_deal({
        "stock_code": "1.HK",
        "expected_listing_date": "2026-01-01",
        "listing_chapter": "main_board_profitable",
        "ipo_master_overrides": {"this_doesnt_exist": 99},
    })
    assert any("ipo_master_overrides" in e for e in errs)


def test_lint_rejects_invalid_cornerstone_type():
    from data.deal_loader import lint_deal
    errs = lint_deal({
        "stock_code": "1.HK",
        "expected_listing_date": "2026-01-01",
        "listing_chapter": "main_board_profitable",
        "cornerstones": [{
            "cornerstone_name": "X",
            "cornerstone_type": "made_up_type",
        }],
    })
    assert any("cornerstone_type" in e for e in errs)


def test_lint_rejects_invalid_currency():
    from data.deal_loader import lint_deal
    errs = lint_deal({
        "stock_code": "1.HK",
        "expected_listing_date": "2026-01-01",
        "listing_chapter": "main_board_profitable",
        "cornerstones": [{
            "cornerstone_name": "X",
            "currency": "EUR",
        }],
    })
    assert any("currency" in e for e in errs)


# =============================================================================
# 模板文件本身 lint 通过
# =============================================================================

def test_template_yaml_lints_clean(project_root):
    from data.deal_loader import lint_deal
    import yaml
    p = project_root / "data" / "deals" / "TEMPLATE.yaml"
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    # TEMPLATE 用 REPLACE.HK 等占位符, lint 应通过 (具体值校验在 load 时再做)
    errs = lint_deal(data)
    # 允许 stock_code 是占位符 — 模板的目的是结构示例, 不需要真实代码
    assert errs == [], f"TEMPLATE 不应有 lint 错: {errs}"


# =============================================================================
# load_deal_dict 集成
# =============================================================================

def test_load_deal_creates_ipo_master_row(empty_db):
    """新 deal: ipo_master 应有一行, status='prospectus' (未来日, 无 oversub)"""
    from data.dao import db_connect
    from data.deal_loader import load_deal_dict

    future_date = (date.today() + timedelta(days=180)).isoformat()
    data = {
        "stock_code": "9999.HK",
        "company_name_zh": "Test Co",
        "listing_chapter": "main_board_profitable",
        "expected_listing_date": future_date,
        "ipo_master_overrides": {
            "offer_price_low": 7.5,
            "offer_price_high": 8.2,
            "lockup_months": 6,
            "sponsor_primary": "中信里昂",
            "sponsor_tier": 1,
        },
    }
    with db_connect(str(empty_db)) as conn:
        stats = load_deal_dict(conn, data)
        row = conn.execute(
            "SELECT stock_code, status, listing_chapter, "
            "offer_price_low, offer_price_high, sponsor_tier, "
            "expected_listing_date FROM ipo_master WHERE stock_code = ?",
            ("9999.HK",),
        ).fetchone()

    assert stats.ipo_master_action == "inserted"
    assert row[0] == "9999.HK"
    assert row[1] == "prospectus"               # 未来 + 没 oversub
    assert row[2] == "main_board_profitable"
    assert row[3] == 7.5
    assert row[4] == 8.2
    assert row[5] == 1
    assert str(row[6])[:10] == future_date


def test_load_deal_idempotent(empty_db):
    """同一 deal 加载两次, 第二次是 update, 不产生 duplicate"""
    from data.dao import db_connect
    from data.deal_loader import load_deal_dict

    future_date = (date.today() + timedelta(days=120)).isoformat()
    data = {
        "stock_code": "9988.HK",
        "company_name_zh": "Foo",
        "listing_chapter": "main_board_profitable",
        "expected_listing_date": future_date,
    }
    with db_connect(str(empty_db)) as conn:
        s1 = load_deal_dict(conn, data)
        # 第二次, 改个名字
        data["company_name_zh"] = "Foo (renamed)"
        s2 = load_deal_dict(conn, data)
        n = conn.execute(
            "SELECT COUNT(*) FROM ipo_master WHERE stock_code = ?", ("9988.HK",)
        ).fetchone()[0]
        name = conn.execute(
            "SELECT company_name_zh FROM ipo_master WHERE stock_code = ?",
            ("9988.HK",),
        ).fetchone()[0]
    assert s1.ipo_master_action == "inserted"
    assert s2.ipo_master_action == "updated"
    assert n == 1
    assert name == "Foo (renamed)"


def test_load_deal_writes_cornerstones_with_currency_normalization(empty_db):
    from data.dao import db_connect
    from data.deal_loader import load_deal_dict

    future_date = (date.today() + timedelta(days=90)).isoformat()
    data = {
        "stock_code": "1234.HK",
        "company_name_zh": "Test",
        "listing_chapter": "main_board_profitable",
        "expected_listing_date": future_date,
        "cornerstones": [
            {"cornerstone_name": "GIC Private Limited",
             "cornerstone_type": "sovereign_pension",
             "ticket_size_native": 1.5e8,
             "currency": "HKD"},
            {"cornerstone_name": "Foreign Fund LP",
             "cornerstone_type": "global_long_only",
             "ticket_size_native": 2e7,
             "currency": "USD"},
        ],
    }
    with db_connect(str(empty_db)) as conn:
        stats = load_deal_dict(conn, data)
        rows = conn.execute(
            "SELECT cornerstone_name, currency, ticket_size_native, "
            "ticket_size_hkd, fx_to_hkd "
            "FROM ipo_cornerstone_link WHERE stock_code = ?",
            ("1234.HK",),
        ).fetchall()
    assert stats.cornerstones_inserted == 2
    by_name = {r[0]: r for r in rows}
    assert by_name["GIC Private Limited"][1] == "HKD"
    assert by_name["GIC Private Limited"][3] == 1.5e8
    assert by_name["Foreign Fund LP"][1] == "USD"
    assert by_name["Foreign Fund LP"][3] == pytest.approx(2e7 * 7.80)
    assert by_name["Foreign Fund LP"][4] == pytest.approx(7.80)


def test_load_deal_status_pricing_when_oversub_known(empty_db):
    """有 intl_oversub 数据 + 未来上市 → status='pricing'"""
    from data.dao import db_connect
    from data.deal_loader import load_deal_dict

    future_date = (date.today() + timedelta(days=30)).isoformat()
    data = {
        "stock_code": "5555.HK",
        "company_name_zh": "Pricing Test",
        "listing_chapter": "main_board_profitable",
        "expected_listing_date": future_date,
        "ipo_master_overrides": {"intl_oversub": 8.5},
    }
    with db_connect(str(empty_db)) as conn:
        load_deal_dict(conn, data)
        status = conn.execute(
            "SELECT status FROM ipo_master WHERE stock_code = ?", ("5555.HK",)
        ).fetchone()[0]
    assert status == "pricing"


def test_load_deal_yaml_file_roundtrip(empty_db, tmp_path):
    from data.dao import db_connect
    from data.deal_loader import load_deal_file

    future_date = (date.today() + timedelta(days=60)).isoformat()
    yaml_path = tmp_path / "test.yaml"
    yaml_path.write_text(
        f"stock_code: 7777.HK\n"
        f"company_name_zh: YAML Test\n"
        f"listing_chapter: main_board_profitable\n"
        f"expected_listing_date: {future_date}\n",
        encoding="utf-8",
    )
    with db_connect(str(empty_db)) as conn:
        stats = load_deal_file(conn, yaml_path)
    assert stats.ipo_master_action == "inserted"
    assert stats.stock_code == "7777.HK"
