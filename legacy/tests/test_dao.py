"""
data.dao 测试: upsert + alias 解析 + as-of-date 边界
"""
from __future__ import annotations

import pytest


# =============================================================================
# upsert 幂等
# =============================================================================

class TestCornerstoneUpsert:
    def test_insert_then_update(self, empty_db):
        import sqlite3
        from data.dao import db_connect, upsert_cornerstone
        from nacs_model import CornerstoneType

        with db_connect(str(empty_db)) as conn:
            upsert_cornerstone(conn,
                cornerstone_id="CS_TEST", canonical_name="Test Co",
                cornerstone_type=CornerstoneType.FAMILY_OFFICE_SPV)
            upsert_cornerstone(conn,
                cornerstone_id="CS_TEST", canonical_name="Test Company Ltd",
                cornerstone_type=CornerstoneType.SOVEREIGN_PENSION)

        with sqlite3.connect(str(empty_db)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM cornerstone_master").fetchone()
        assert row["canonical_name"] == "Test Company Ltd"
        assert row["cornerstone_type"] == "sovereign_pension"
        assert row["is_longterm"] == 1  # SOVEREIGN_PENSION ∈ LONGTERM_TYPES

    def test_chinese_flag_derived(self, empty_db):
        import sqlite3
        from data.dao import db_connect, upsert_cornerstone
        from nacs_model import CornerstoneType
        with db_connect(str(empty_db)) as conn:
            upsert_cornerstone(conn,
                cornerstone_id="CS_PICC", canonical_name="China PICC",
                cornerstone_type=CornerstoneType.CHINESE_MUTUAL_INSURANCE)
        with sqlite3.connect(str(empty_db)) as conn:
            row = conn.execute(
                "SELECT is_chinese, is_longterm FROM cornerstone_master"
            ).fetchone()
        assert row[0] == 1  # CHINESE_TYPES
        assert row[1] == 1  # LONGTERM_TYPES (CN insurance 同时是长线)


# =============================================================================
# alias 解析
# =============================================================================

class TestAliasResolve:
    @pytest.fixture
    def populated_db(self, empty_db):
        from data.dao import db_connect, upsert_cornerstone, add_alias
        from nacs_model import CornerstoneType
        with db_connect(str(empty_db)) as conn:
            upsert_cornerstone(conn,
                cornerstone_id="CS_GIC", canonical_name="GIC Private Limited",
                cornerstone_type=CornerstoneType.SOVEREIGN_PENSION)
            add_alias(conn, cornerstone_id="CS_GIC",
                      alias_text="GIC Private Limited", alias_type="legal_name")
            add_alias(conn, cornerstone_id="CS_GIC",
                      alias_text="GIC", alias_type="abbreviation",
                      match_confidence=0.9)

            upsert_cornerstone(conn,
                cornerstone_id="CS_BlackRock", canonical_name="BlackRock Inc",
                cornerstone_type=CornerstoneType.GLOBAL_LONG_ONLY)
            add_alias(conn, cornerstone_id="CS_BlackRock",
                      alias_text="BlackRock Inc", alias_type="legal_name")
        return empty_db

    def test_exact_match(self, populated_db):
        import sqlite3
        from data.dao import resolve_cornerstone_id
        with sqlite3.connect(str(populated_db)) as conn:
            conn.row_factory = sqlite3.Row
            r = resolve_cornerstone_id(conn, "GIC Private Limited")
        assert r is not None
        assert r[0] == "CS_GIC"
        assert r[1] == 1.0

    def test_case_insensitive(self, populated_db):
        import sqlite3
        from data.dao import resolve_cornerstone_id
        with sqlite3.connect(str(populated_db)) as conn:
            conn.row_factory = sqlite3.Row
            r = resolve_cornerstone_id(conn, "gic private limited")
        assert r is not None and r[0] == "CS_GIC"

    def test_substring_match(self, populated_db):
        """raw 包含 alias 子串"""
        import sqlite3
        from data.dao import resolve_cornerstone_id
        with sqlite3.connect(str(populated_db)) as conn:
            conn.row_factory = sqlite3.Row
            r = resolve_cornerstone_id(conn, "GIC")
        assert r is not None and r[0] == "CS_GIC"

    def test_no_match_returns_none(self, populated_db):
        import sqlite3
        from data.dao import resolve_cornerstone_id
        with sqlite3.connect(str(populated_db)) as conn:
            conn.row_factory = sqlite3.Row
            r = resolve_cornerstone_id(conn, "Some Random Fund That Doesn't Exist")
        assert r is None


# =============================================================================
# as-of-date 防 look-ahead
# =============================================================================

class TestAsOfDateBoundary:
    def test_perf_excludes_lookahead_ipos(self, empty_db):
        """compute_cornerstone_perf_asof 不能纳入 listing_date >= asof 的 IPO"""
        import sqlite3
        from datetime import date
        from data.dao import (db_connect, upsert_cornerstone, upsert_ipo,
                              link_cornerstone_to_ipo, compute_cornerstone_perf_asof)
        from nacs_model import CornerstoneType

        with db_connect(str(empty_db)) as conn:
            upsert_cornerstone(conn,
                cornerstone_id="CS_X", canonical_name="X",
                cornerstone_type=CornerstoneType.SOVEREIGN_PENSION)
            # 一只 2024 IPO (在 asof=2025-01 之前)
            upsert_ipo(conn, ipo_id="HK_001_2024", stock_code="0001.HK",
                       listing_date="2024-06-01", listing_chapter="main_board")
            link_cornerstone_to_ipo(conn, ipo_id="HK_001_2024",
                                    cornerstone_id="CS_X", ticket_size_hkd=1e8)
            # 一只 2025-06 IPO (在 asof=2025-01 之后, 应被排除)
            upsert_ipo(conn, ipo_id="HK_002_2025", stock_code="0002.HK",
                       listing_date="2025-06-01", listing_chapter="main_board")
            link_cornerstone_to_ipo(conn, ipo_id="HK_002_2025",
                                    cornerstone_id="CS_X", ticket_size_hkd=1e8)

        with sqlite3.connect(str(empty_db)) as conn:
            conn.row_factory = sqlite3.Row
            perf = compute_cornerstone_perf_asof(conn, "CS_X", date(2025, 1, 1))
        assert perf["ipo_count_5y"] == 1  # 只算 2024 那一只


# =============================================================================
# 反幸存者偏差: list_ipos_in_universe_asof
# =============================================================================

class TestUniverseAsOf:
    @pytest.fixture
    def universe_db(self, empty_db):
        from data.dao import db_connect, upsert_ipo
        with db_connect(str(empty_db)) as conn:
            # A: 2023-06 上市, 一直活着 → 任何 asof>=2023-06 都在 universe
            upsert_ipo(conn, ipo_id="HK_A_2023", stock_code="A.HK",
                       listing_date="2023-06-01", listing_chapter="main_board")
            # B: 2024-01 上市, 2024-12 退市 → asof=2025-06 时 NOT 在 universe;
            #     但 asof=2024-06 时仍在 universe
            upsert_ipo(conn, ipo_id="HK_B_2024", stock_code="B.HK",
                       listing_date="2024-01-15", listing_chapter="main_board")
            conn.execute(
                "UPDATE ipo_master SET is_delisted=1, delisting_date=?, "
                "is_acquired=0 WHERE ipo_id=?",
                ("2024-12-20", "HK_B_2024"),
            )
            # C: 2024-09 上市, 2025-09 被收购 → asof=2025-06 时还在 universe
            upsert_ipo(conn, ipo_id="HK_C_2024", stock_code="C.HK",
                       listing_date="2024-09-10", listing_chapter="main_board")
            conn.execute(
                "UPDATE ipo_master SET is_delisted=1, delisting_date=?, "
                "is_acquired=1 WHERE ipo_id=?",
                ("2025-09-10", "HK_C_2024"),
            )
            # D: 2025-08 上市 → asof=2025-06 时未上市, NOT 在 universe
            upsert_ipo(conn, ipo_id="HK_D_2025", stock_code="D.HK",
                       listing_date="2025-08-01", listing_chapter="main_board")
        return empty_db

    def test_universe_at_2025_06(self, universe_db):
        """2025-06 时点: A 活着 ✓, B 已退 ✗, C 退市日 > asof ✓, D 未上市 ✗"""
        import sqlite3
        from datetime import date
        from data.dao import list_ipos_in_universe_asof
        with sqlite3.connect(str(universe_db)) as conn:
            conn.row_factory = sqlite3.Row
            ids = list_ipos_in_universe_asof(conn, date(2025, 6, 1))
        assert ids == ["HK_A_2023", "HK_C_2024"]

    def test_universe_at_2024_06(self, universe_db):
        """2024-06 时点: A ✓, B 还没退 ✓, C 还没上 ✗, D 还没上 ✗"""
        import sqlite3
        from datetime import date
        from data.dao import list_ipos_in_universe_asof
        with sqlite3.connect(str(universe_db)) as conn:
            conn.row_factory = sqlite3.Row
            ids = list_ipos_in_universe_asof(conn, date(2024, 6, 1))
        assert ids == ["HK_A_2023", "HK_B_2024"]

    def test_universe_at_2026_01(self, universe_db):
        """2026-01: 只剩 A 活着"""
        import sqlite3
        from datetime import date
        from data.dao import list_ipos_in_universe_asof
        with sqlite3.connect(str(universe_db)) as conn:
            conn.row_factory = sqlite3.Row
            ids = list_ipos_in_universe_asof(conn, date(2026, 1, 1))
        assert ids == ["HK_A_2023", "HK_D_2025"]
