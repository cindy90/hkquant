"""
migration v1 + 派生 schema 单元测试.

覆盖:
    - schema.py 的 SCHEMA_SQL 创建出包含所有 v1 列的表
    - mv_ipo_full 视图存在并可查
    - ipo_cornerstone_link 的 CHECK (affiliation_flag IN (0,1,2)) 拒绝 3
    - link upsert 不产生重复 (ipo_id, cs_id)
    - compute_ipo_returns 写入 is_*_due 标志
    - 货币归一: USD ticket → ticket_size_hkd = native × 7.8
    - link_cornerstone_to_ipo 拒绝 affiliation_flag 非 0/1/2
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest


# =============================================================================
# schema 完备性
# =============================================================================

def test_schema_has_v1_columns(empty_db):
    """ipo_master / link / returns 必须包含 v1 新增字段"""
    with sqlite3.connect(str(empty_db)) as conn:
        master_cols = {r[1] for r in conn.execute("PRAGMA table_info(ipo_master)")}
        link_cols = {r[1] for r in conn.execute("PRAGMA table_info(ipo_cornerstone_link)")}
        returns_cols = {r[1] for r in conn.execute("PRAGMA table_info(ipo_returns)")}

    assert "gross_proceeds_excl_greenshoe" in master_cols
    assert "total_offer_shares" in master_cols
    assert {"currency", "ticket_size_native", "fx_to_hkd"}.issubset(link_cols)
    assert {"is_d30_due", "is_m6_due", "is_m12_due", "is_unlock_due"}.issubset(
        returns_cols
    )


def test_mv_ipo_full_view_exists(empty_db):
    with sqlite3.connect(str(empty_db)) as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='view' AND name='mv_ipo_full'"
        ).fetchone()
    assert row is not None


def test_mv_ipo_full_queryable(empty_db):
    """空 DB 下 mv_ipo_full 应返回 0 行 (列名/视图定义有效)"""
    with sqlite3.connect(str(empty_db)) as conn:
        rows = conn.execute("SELECT * FROM mv_ipo_full").fetchall()
    assert rows == []


def test_v1_schema_indexes_present(empty_db):
    """关键的 5 个索引 (M2 加的) 应该都被 init_database 创建"""
    expected = {"idx_link_ipo", "idx_link_cs", "idx_link_unique",
                "idx_ipo_stock_code", "idx_fin_code_year"}
    with sqlite3.connect(str(empty_db)) as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )}
    missing = expected - names
    assert not missing, f"缺索引: {missing}"


# =============================================================================
# CHECK 约束
# =============================================================================

def test_check_constraint_rejects_bad_affiliation_flag(empty_db):
    """affiliation_flag = 3 应被 CHECK 拒绝"""
    from data.dao import db_connect, upsert_cornerstone, upsert_ipo
    from nacs_model import CornerstoneType

    with db_connect(str(empty_db)) as conn:
        upsert_cornerstone(conn, cornerstone_id="CS_X", canonical_name="X",
                           cornerstone_type=CornerstoneType.SOVEREIGN_PENSION)
        upsert_ipo(conn, ipo_id="HK_001", stock_code="0001.HK",
                   listing_date="2024-01-01", listing_chapter="main_board")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("""
                INSERT INTO ipo_cornerstone_link (ipo_id, cornerstone_id, affiliation_flag)
                VALUES ('HK_001', 'CS_X', 3)
            """)


def test_check_constraint_accepts_0_1_2(empty_db):
    from data.dao import db_connect, upsert_cornerstone, upsert_ipo, link_cornerstone_to_ipo
    from nacs_model import CornerstoneType

    with db_connect(str(empty_db)) as conn:
        upsert_cornerstone(conn, cornerstone_id="CS_X", canonical_name="X",
                           cornerstone_type=CornerstoneType.SOVEREIGN_PENSION)
        upsert_cornerstone(conn, cornerstone_id="CS_Y", canonical_name="Y",
                           cornerstone_type=CornerstoneType.GLOBAL_LONG_ONLY)
        upsert_cornerstone(conn, cornerstone_id="CS_Z", canonical_name="Z",
                           cornerstone_type=CornerstoneType.STRATEGIC_INDUSTRIAL)
        upsert_ipo(conn, ipo_id="HK_001", stock_code="0001.HK",
                   listing_date="2024-01-01", listing_chapter="main_board")
        for cs_id, flag in [("CS_X", 0), ("CS_Y", 1), ("CS_Z", 2)]:
            link_cornerstone_to_ipo(
                conn, ipo_id="HK_001", cornerstone_id=cs_id,
                affiliation_flag=flag,
            )
        n = conn.execute("SELECT COUNT(*) FROM ipo_cornerstone_link").fetchone()[0]
        assert n == 3


def test_link_dao_validates_affiliation_flag(empty_db):
    """link_cornerstone_to_ipo 在 Python 层先行拒绝非法值"""
    from data.dao import db_connect, upsert_cornerstone, upsert_ipo, link_cornerstone_to_ipo
    from nacs_model import CornerstoneType
    with db_connect(str(empty_db)) as conn:
        upsert_cornerstone(conn, cornerstone_id="CS_X", canonical_name="X",
                           cornerstone_type=CornerstoneType.SOVEREIGN_PENSION)
        upsert_ipo(conn, ipo_id="HK_001", stock_code="0001.HK",
                   listing_date="2024-01-01", listing_chapter="main_board")
        with pytest.raises(ValueError):
            link_cornerstone_to_ipo(
                conn, ipo_id="HK_001", cornerstone_id="CS_X",
                affiliation_flag=5,
            )


# =============================================================================
# UNIQUE 索引: 同 (ipo_id, cs_id) 不再插重复
# =============================================================================

def test_link_upsert_idempotent(empty_db):
    from data.dao import db_connect, upsert_cornerstone, upsert_ipo, link_cornerstone_to_ipo
    from nacs_model import CornerstoneType
    with db_connect(str(empty_db)) as conn:
        upsert_cornerstone(conn, cornerstone_id="CS_X", canonical_name="X",
                           cornerstone_type=CornerstoneType.SOVEREIGN_PENSION)
        upsert_ipo(conn, ipo_id="HK_001", stock_code="0001.HK",
                   listing_date="2024-01-01", listing_chapter="main_board")
        # 调用两次, 第二次应是 update 而非 insert
        link_cornerstone_to_ipo(conn, ipo_id="HK_001", cornerstone_id="CS_X",
                                ticket_size_hkd=1e8)
        link_cornerstone_to_ipo(conn, ipo_id="HK_001", cornerstone_id="CS_X",
                                ticket_size_hkd=2e8)
        rows = conn.execute(
            "SELECT ticket_size_hkd FROM ipo_cornerstone_link "
            "WHERE ipo_id='HK_001' AND cornerstone_id='CS_X'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == 2e8  # 后写覆盖前写


# =============================================================================
# 货币归一
# =============================================================================

def test_currency_normalization_usd(empty_db):
    """USD 票通过 link_cornerstone_to_ipo 时, hkd 应已乘 7.8"""
    from data.dao import db_connect, upsert_cornerstone, upsert_ipo, link_cornerstone_to_ipo
    from data_sources.ifind.load_to_db import _fx_to_hkd
    from nacs_model import CornerstoneType
    with db_connect(str(empty_db)) as conn:
        upsert_cornerstone(conn, cornerstone_id="CS_X", canonical_name="X",
                           cornerstone_type=CornerstoneType.SOVEREIGN_PENSION)
        upsert_ipo(conn, ipo_id="HK_001", stock_code="0001.HK",
                   listing_date="2024-01-01", listing_chapter="main_board")
        native = 1e7  # USD
        fx = _fx_to_hkd("USD")
        link_cornerstone_to_ipo(
            conn, ipo_id="HK_001", cornerstone_id="CS_X",
            ticket_size_hkd=native * fx,
            ticket_size_native=native,
            currency="USD", fx_to_hkd=fx,
        )
        row = conn.execute(
            "SELECT currency, ticket_size_native, ticket_size_hkd, fx_to_hkd "
            "FROM ipo_cornerstone_link WHERE ipo_id='HK_001'"
        ).fetchone()
    assert row[0] == "USD"
    assert row[1] == 1e7
    assert row[2] == pytest.approx(7.8e7)
    assert row[3] == pytest.approx(7.80)


def test_load_cornerstones_converts_usd_in_csv(empty_db, tmp_path):
    """load_cornerstones 从 raw CSV 读到 USD 行时应自动换算"""
    from data.dao import db_connect
    from data_sources.ifind.load_to_db import load_ipo_info, load_cornerstones

    ipo_csv = tmp_path / "ipo.csv"
    ipo_csv.write_text(
        "p05310_f001,p05310_f033,p05310_f028,p05310_f002\n"
        "0001.HK,2024-06-01,2024-05-25,Test\n",
        encoding="utf-8",
    )
    cs_csv = tmp_path / "cs.csv"
    # p05309 列序: f001=stock f002=name_zh f003=listing_date f004=pricing
    # f005=cs_name f006=ultimate f008=ticket f009=shares f010=lockup f011=currency
    cs_csv.write_text(
        "p05309_f001,p05309_f002,p05309_f003,p05309_f016,p05309_f004,p05309_f017,"
        "p05309_f005,p05309_f018,p05309_f006,p05309_f019,p05309_f009,p05309_f008,"
        "p05309_f011,p05309_f014,p05309_f010,p05309_f015,p05309_f012,p05309_f013\n"
        "0001.HK,Test,2024-06-01,2024-05-15,2024-05-25,否,Foreign Investor LP,desc,"
        "ParentCo,--,1000000,15000000.0,USD,5.0,6,2024-12-01,行业,子行业\n",
        encoding="utf-8",
    )
    with db_connect(str(empty_db)) as conn:
        load_ipo_info(conn, ipo_csv, dry_run=False)
        load_cornerstones(conn, cs_csv, dry_run=False)
        row = conn.execute(
            "SELECT currency, ticket_size_native, ticket_size_hkd, fx_to_hkd "
            "FROM ipo_cornerstone_link"
        ).fetchone()
    assert row[0] == "USD"
    assert row[1] == 1.5e7
    assert row[2] == pytest.approx(1.5e7 * 7.80)
    assert row[3] == pytest.approx(7.80)


# =============================================================================
# Due flags via compute_ipo_returns
# =============================================================================

def test_compute_ipo_returns_sets_due_flags(empty_db):
    """compute_ipo_returns 应该按 asof 派生 is_*_due"""
    from data.dao import db_connect, upsert_ipo, compute_ipo_returns
    asof = date(2025, 1, 1)
    listing = date(2024, 6, 1)  # ld + 30=2024-07-01 ✓ due, ld+180=2024-11-28 ✓ due,
                                 # ld+365=2025-06-01 ✗ not due, unlock+30=2025-01-01 = asof ✓
    with db_connect(str(empty_db)) as conn:
        upsert_ipo(conn, ipo_id="HK_001", stock_code="0001.HK",
                   listing_date=listing.isoformat(),
                   listing_chapter="main_board",
                   offer_price_hkd=10.0, lockup_months=6)
        # 灌一行价格让 compute_ipo_returns 不返 None
        conn.execute(
            "INSERT INTO price_history (ipo_id, trade_date, close_hkd) VALUES (?, ?, ?)",
            ("HK_001", listing.isoformat(), 11.0)
        )
        result = compute_ipo_returns(conn, "HK_001", asof=asof)
        assert result is not None
        assert result["is_d30_due"] == 1
        assert result["is_m6_due"] == 1
        assert result["is_m12_due"] == 0
        assert result["is_unlock_due"] == 1

        # DB 落库的值应该匹配
        row = conn.execute(
            "SELECT is_d30_due, is_m6_due, is_m12_due, is_unlock_due "
            "FROM ipo_returns WHERE ipo_id='HK_001'"
        ).fetchone()
        assert tuple(row) == (1, 1, 0, 1)


# =============================================================================
# mv_ipo_full smoke
# =============================================================================

def test_mv_ipo_full_returns_data_after_inserts(empty_db):
    from data.dao import db_connect, upsert_ipo, upsert_cornerstone, link_cornerstone_to_ipo
    from nacs_model import CornerstoneType
    with db_connect(str(empty_db)) as conn:
        upsert_cornerstone(conn, cornerstone_id="CS_X", canonical_name="X",
                           cornerstone_type=CornerstoneType.SOVEREIGN_PENSION)
        upsert_ipo(conn, ipo_id="HK_001", stock_code="0001.HK",
                   listing_date="2024-06-01", listing_chapter="main_board",
                   offer_price_hkd=10.0)
        link_cornerstone_to_ipo(
            conn, ipo_id="HK_001", cornerstone_id="CS_X",
            ticket_size_hkd=1e8, ticket_size_native=1e8,
            currency="HKD", fx_to_hkd=1.0,
        )
        rows = conn.execute(
            "SELECT stock_code, n_cs, cs_total_hkd, cs_currencies FROM mv_ipo_full"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "0001.HK"
    assert rows[0][1] == 1
    assert rows[0][2] == 1e8
    assert rows[0][3] == "HKD"
