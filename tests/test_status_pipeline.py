"""
ipo_master.status 列与 ETL 自动分类测试.

覆盖:
    - 新 schema 加 status / prospectus_pdf_path / expected_listing_date 三列
    - CHECK 约束拒绝 'invalid' 状态
    - _classify_status 各分支
    - load_ipo_info 按 listing_date / intl_oversub 自动分类
    - load_delisted 把 status 写为 'delisted'
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


# =============================================================================
# Schema 完备性
# =============================================================================

def test_status_columns_exist(empty_db):
    cols = {r[1] for r in
            sqlite3.connect(str(empty_db)).execute("PRAGMA table_info(ipo_master)")}
    assert {"status", "prospectus_pdf_path", "expected_listing_date"}.issubset(cols)


def test_status_check_constraint_rejects_invalid(empty_db):
    from data.dao import db_connect, upsert_ipo
    with db_connect(str(empty_db)) as conn:
        upsert_ipo(conn, ipo_id="HK_001", stock_code="0001.HK",
                   listing_date="2024-01-01", listing_chapter="main_board")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE ipo_master SET status = 'bogus' WHERE ipo_id = ?",
                ("HK_001",),
            )


@pytest.mark.parametrize("status", ["prospectus", "pricing", "listed",
                                    "delisted", "withdrawn"])
def test_status_check_accepts_valid(empty_db, status):
    from data.dao import db_connect, upsert_ipo
    with db_connect(str(empty_db)) as conn:
        upsert_ipo(conn, ipo_id="HK_001", stock_code="0001.HK",
                   listing_date="2024-01-01", listing_chapter="main_board",
                   status=status)
        row = conn.execute(
            "SELECT status FROM ipo_master WHERE ipo_id = ?", ("HK_001",)
        ).fetchone()
    assert row[0] == status


def test_status_default_is_listed(empty_db):
    """新行 INSERT 不传 status → 默认 'listed'"""
    from data.dao import db_connect, upsert_ipo
    with db_connect(str(empty_db)) as conn:
        upsert_ipo(conn, ipo_id="HK_001", stock_code="0001.HK",
                   listing_date="2024-01-01", listing_chapter="main_board")
        row = conn.execute(
            "SELECT status FROM ipo_master WHERE ipo_id = ?", ("HK_001",)
        ).fetchone()
    assert row[0] == "listed"


# =============================================================================
# _classify_status 单元
# =============================================================================

class TestClassifyStatus:
    def test_past_listing_is_listed(self):
        from data_sources.ifind.load_to_db import _classify_status
        assert _classify_status("2024-01-01", 5.0, "2025-01-01") == "listed"
        assert _classify_status("2024-01-01", None, "2025-01-01") == "listed"

    def test_future_listing_with_oversub_is_pricing(self):
        from data_sources.ifind.load_to_db import _classify_status
        assert _classify_status("2026-12-01", 8.0, "2026-05-09") == "pricing"

    def test_future_listing_without_oversub_is_prospectus(self):
        from data_sources.ifind.load_to_db import _classify_status
        assert _classify_status("2026-12-01", None, "2026-05-09") == "prospectus"

    def test_listing_today_is_listed(self):
        from data_sources.ifind.load_to_db import _classify_status
        # listing_date == today → listed (boundary)
        assert _classify_status("2026-05-09", None, "2026-05-09") == "listed"


# =============================================================================
# load_ipo_info 端到端
# =============================================================================

def test_load_ipo_info_assigns_status_from_data(empty_db, tmp_path):
    """构造 3 行 CSV, 检验 ETL 把 status 正确分到 listed / pricing / prospectus"""
    from data.dao import db_connect
    from data_sources.ifind.load_to_db import load_ipo_info

    csv = tmp_path / "ipo.csv"
    csv.write_text(
        # f001=stock_code f002=name_zh f028=pricing f033=listing_date f052=intl_oversub
        "p05310_f001,p05310_f002,p05310_f028,p05310_f033,p05310_f052\n"
        "0001.HK,Past listed,2024-01-25,2024-02-01,5.5\n"     # listed (past)
        "0002.HK,Pricing soon,2026-06-25,2026-07-01,8.0\n"   # pricing (future + oversub)
        "0003.HK,In prospectus,--,2026-12-01,--\n"            # prospectus (future, no oversub)
        "0004.HK,No listing date,2026-06-01,--,3.0\n",        # skipped
        encoding="utf-8",
    )
    with db_connect(str(empty_db)) as conn:
        # 注入固定 today 让测试可重复
        stats = load_ipo_info(conn, csv, asof_today="2026-05-09")
        rows = {r["stock_code"]: r["status"] for r in
                conn.execute("SELECT stock_code, status FROM ipo_master")}

    assert stats.n_inserted == 3
    assert stats.n_skipped_no_date == 1
    assert stats.n_status_prospectus == 1
    assert stats.n_status_pricing == 1
    assert rows == {
        "0001.HK": "listed",
        "0002.HK": "pricing",
        "0003.HK": "prospectus",
    }


def test_load_delisted_sets_status_delisted(empty_db, tmp_path):
    """退市 CSV 加载后 status 应自动变 'delisted'"""
    from data.dao import db_connect, upsert_ipo
    from data_sources.ifind.load_to_db import load_delisted

    csv = tmp_path / "delisted.csv"
    csv.write_text(
        "stock_code,delisting_date,delisting_reason,is_acquired\n"
        "0001.HK,2024-12-20,liquidated,0\n",
        encoding="utf-8",
    )
    with db_connect(str(empty_db)) as conn:
        upsert_ipo(conn, ipo_id="HK_001", stock_code="0001.HK",
                   listing_date="2023-06-01", listing_chapter="main_board",
                   status="listed")
        load_delisted(conn, csv, dry_run=False)
        row = conn.execute(
            "SELECT status, is_delisted FROM ipo_master WHERE ipo_id='HK_001'"
        ).fetchone()
    assert row[0] == "delisted"
    assert row[1] == 1
