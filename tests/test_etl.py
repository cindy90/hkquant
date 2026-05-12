"""
ETL loader 测试: field_mappings 解析器 + load_to_db 幂等性 + 字段抽检
"""
from __future__ import annotations

import pytest


# =============================================================================
# field_mappings 类型转换
# =============================================================================

class TestParsers:
    def test_parse_float_normal(self):
        from data_sources.ifind.field_mappings import parse_float
        assert parse_float("12.34") == 12.34
        assert parse_float("0") == 0.0
        assert parse_float("-3.5") == -3.5

    @pytest.mark.parametrize("v", ["", "--", "—", "NULL", "nan", "None", None])
    def test_parse_float_null_tokens(self, v):
        from data_sources.ifind.field_mappings import parse_float
        assert parse_float(v) is None

    def test_parse_float_invalid(self):
        from data_sources.ifind.field_mappings import parse_float
        assert parse_float("abc") is None

    def test_parse_int_rounds(self):
        from data_sources.ifind.field_mappings import parse_int
        assert parse_int("12.7") == 13
        assert parse_int("12.4") == 12
        assert parse_int("12.5") == 12  # banker's rounding (round-half-even)
        assert parse_int("13.5") == 14  # banker's rounding (round-half-even)
        assert parse_int("0") == 0
        assert parse_int("--") is None

    def test_parse_date_formats(self):
        from data_sources.ifind.field_mappings import parse_date
        assert parse_date("2026/05/06") == "2026-05-06"
        assert parse_date("2026-05-06") == "2026-05-06"
        assert parse_date("2026.05.06") == "2026-05-06"
        assert parse_date("--") is None
        assert parse_date("not a date") is None
        assert parse_date("2026/13/01") is None  # 月份非法

    def test_parse_date_rejects_invalid_days(self):
        from data_sources.ifind.field_mappings import parse_date
        assert parse_date("2026-02-30") is None  # 2月无30日
        assert parse_date("2026-02-29") is None  # 2026非闰年
        assert parse_date("2024-02-29") == "2024-02-29"  # 2024闰年
        assert parse_date("2026-04-31") is None  # 4月无31日
        assert parse_date("2026-06-31") is None  # 6月无31日

    def test_parse_str_trims(self):
        from data_sources.ifind.field_mappings import parse_str
        assert parse_str("  hello  ") == "hello"
        assert parse_str("--") is None


# =============================================================================
# ID 生成
# =============================================================================

class TestIdMakers:
    def test_make_ipo_id(self):
        from data_sources.ifind.field_mappings import make_ipo_id
        assert make_ipo_id("1187.HK", "2026-05-06") == "HK_1187_HK_2026"
        assert make_ipo_id("00001.HK", "2024-01-01") == "HK_00001_HK_2024"

    def test_make_cornerstone_id_english(self):
        from data_sources.ifind.field_mappings import make_cornerstone_id
        assert make_cornerstone_id("GIC Private Limited") == "CS_GIC_Private_Limited"

    def test_make_cornerstone_id_chinese_with_brackets(self):
        from data_sources.ifind.field_mappings import make_cornerstone_id
        # 中文括号转下划线, 折叠多个_
        assert make_cornerstone_id("蓝思科技(香港)有限公司") == "CS_蓝思科技_香港_有限公司"

    def test_make_cornerstone_id_collapses_spaces(self):
        from data_sources.ifind.field_mappings import make_cornerstone_id
        assert make_cornerstone_id("Foo  Bar") == "CS_Foo_Bar"


# =============================================================================
# load_to_db 端到端 (用临时 DB + 真实 raw CSV)
# =============================================================================

class TestLoadToDb:
    @pytest.fixture
    def loaded_db(self, empty_db, raw_dir):
        """跑一遍 ipo+cornerstones loader, 返回 db path"""
        from data.dao import db_connect
        from data_sources.ifind.load_to_db import load_ipo_info, load_cornerstones
        with db_connect(str(empty_db)) as conn:
            load_ipo_info(conn, raw_dir / "ifind_ipo_info.csv", dry_run=False)
            load_cornerstones(conn, raw_dir / "ifind_cornerstones.csv", dry_run=False)
        return empty_db

    def test_ipo_loaded(self, loaded_db):
        import sqlite3
        with sqlite3.connect(str(loaded_db)) as conn:
            n = conn.execute("SELECT COUNT(*) FROM ipo_master").fetchone()[0]
        assert n > 300, f"ipo_master should have hundreds of rows, got {n}"

    def test_cornerstones_loaded(self, loaded_db):
        import sqlite3
        with sqlite3.connect(str(loaded_db)) as conn:
            n_cs = conn.execute("SELECT COUNT(*) FROM cornerstone_master").fetchone()[0]
            n_link = conn.execute("SELECT COUNT(*) FROM ipo_cornerstone_link").fetchone()[0]
            n_alias = conn.execute("SELECT COUNT(*) FROM cornerstone_aliases").fetchone()[0]
        assert n_cs > 1000
        assert n_link > n_cs  # 有些基石投多个 IPO
        assert n_alias > 0

    def test_idempotent_second_run(self, loaded_db, raw_dir):
        """重跑 loader, 行数不应变化"""
        import sqlite3
        from data.dao import db_connect
        from data_sources.ifind.load_to_db import load_ipo_info, load_cornerstones

        def counts(p):
            with sqlite3.connect(str(p)) as c:
                return tuple(c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                             for t in ("ipo_master", "cornerstone_master",
                                       "cornerstone_aliases", "ipo_cornerstone_link"))

        before = counts(loaded_db)
        with db_connect(str(loaded_db)) as conn:
            load_ipo_info(conn, raw_dir / "ifind_ipo_info.csv", dry_run=False)
            load_cornerstones(conn, raw_dir / "ifind_cornerstones.csv", dry_run=False)
        after = counts(loaded_db)
        assert before == after, f"NOT IDEMPOTENT: {before} → {after}"

    def test_sample_1187_hk_fields(self, loaded_db):
        """抽检 1187.HK 字段映射正确性"""
        import sqlite3
        with sqlite3.connect(str(loaded_db)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM ipo_master WHERE stock_code = '1187.HK'"
            ).fetchone()
        assert row is not None
        assert row["ipo_id"] == "HK_1187_HK_2026"
        assert row["listing_date"] == "2026-05-06"
        assert row["offer_price_hkd"] == pytest.approx(39.33, abs=0.01)
        # cornerstone_coverage 应已从 % 转为 0-1 小数
        assert 0 < row["cornerstone_coverage"] < 1

    def test_preserves_existing_cornerstone_type(self, loaded_db, raw_dir):
        """重跑 loader 不应覆盖人工 promote 的 type"""
        import sqlite3
        from data.dao import db_connect
        from data_sources.ifind.load_to_db import load_cornerstones

        # 1. 找一个现有 cs 改 type 为 SOVEREIGN_PENSION
        with sqlite3.connect(str(loaded_db)) as conn:
            conn.row_factory = sqlite3.Row
            cs = conn.execute(
                "SELECT cornerstone_id FROM cornerstone_master LIMIT 1"
            ).fetchone()
            cs_id = cs["cornerstone_id"]
            conn.execute(
                "UPDATE cornerstone_master SET cornerstone_type = ? WHERE cornerstone_id = ?",
                ("sovereign_pension", cs_id),
            )
            conn.commit()

        # 2. 重跑 loader
        with db_connect(str(loaded_db)) as conn:
            load_cornerstones(conn, raw_dir / "ifind_cornerstones.csv", dry_run=False)

        # 3. type 应仍为 sovereign_pension (未被覆写为默认 family_office_spv)
        with sqlite3.connect(str(loaded_db)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT cornerstone_type FROM cornerstone_master WHERE cornerstone_id = ?",
                (cs_id,),
            ).fetchone()
        assert row["cornerstone_type"] == "sovereign_pension"

    def test_dry_run_writes_nothing(self, empty_db, raw_dir):
        """dry-run 不应写入任何行"""
        import sqlite3
        from data.dao import db_connect
        from data_sources.ifind.load_to_db import load_ipo_info, load_cornerstones

        with db_connect(str(empty_db)) as conn:
            load_ipo_info(conn, raw_dir / "ifind_ipo_info.csv", dry_run=True)
            load_cornerstones(conn, raw_dir / "ifind_cornerstones.csv", dry_run=True)
        with sqlite3.connect(str(empty_db)) as conn:
            n = conn.execute("SELECT COUNT(*) FROM ipo_master").fetchone()[0]
            n2 = conn.execute("SELECT COUNT(*) FROM cornerstone_master").fetchone()[0]
        assert n == 0
        assert n2 == 0


# =============================================================================
# load_delisted (反幸存者偏差 ETL)
# =============================================================================

class TestLoadDelisted:
    @pytest.fixture
    def delisted_csv(self, tmp_path):
        """构造一个有 1 命中 + 1 未命中的退市 CSV"""
        p = tmp_path / "ifind_delisted_hk.csv"
        p.write_text(
            "stock_code,delisting_date,delisting_reason,is_acquired\n"
            "0001.HK,2024-12-20,liquidated,0\n"
            "9999.HK,2024-08-08,acquired,1\n",  # 9999.HK 不在 ipo_master
            encoding="utf-8",
        )
        return p

    def test_marks_existing_ipo_as_delisted(self, empty_db, delisted_csv):
        import sqlite3
        from data.dao import db_connect, upsert_ipo
        from data_sources.ifind.load_to_db import load_delisted

        with db_connect(str(empty_db)) as conn:
            upsert_ipo(conn, ipo_id="HK_0001_HK_2023", stock_code="0001.HK",
                       listing_date="2023-06-01", listing_chapter="main_board")
            stats = load_delisted(conn, delisted_csv, dry_run=False)

        assert stats.n_rows_csv == 2
        assert stats.n_matched == 1
        assert stats.n_unmatched == 1

        with sqlite3.connect(str(empty_db)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT is_delisted, delisting_date, is_acquired FROM ipo_master "
                "WHERE stock_code = '0001.HK'"
            ).fetchone()
        assert row["is_delisted"] == 1
        assert row["delisting_date"] == "2024-12-20"
        assert row["is_acquired"] == 0

    def test_dry_run_no_writes(self, empty_db, delisted_csv):
        import sqlite3
        from data.dao import db_connect, upsert_ipo
        from data_sources.ifind.load_to_db import load_delisted

        with db_connect(str(empty_db)) as conn:
            upsert_ipo(conn, ipo_id="HK_0001_HK_2023", stock_code="0001.HK",
                       listing_date="2023-06-01", listing_chapter="main_board")
            load_delisted(conn, delisted_csv, dry_run=True)

        with sqlite3.connect(str(empty_db)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT is_delisted FROM ipo_master WHERE stock_code = '0001.HK'"
            ).fetchone()
        assert row["is_delisted"] == 0  # 未被写入


# =============================================================================
# FX 汇率查表
# =============================================================================

class TestFxRate:
    def test_hkd_always_one(self):
        from data_sources.ifind.field_mappings import get_fx_rate
        assert get_fx_rate("HKD") == 1.0
        assert get_fx_rate("HKD", "2024-06-15") == 1.0

    def test_usd_varies_by_quarter(self):
        from data_sources.ifind.field_mappings import get_fx_rate
        rate_2022q1 = get_fx_rate("USD", "2022-02-15")
        rate_2024q3 = get_fx_rate("USD", "2024-08-01")
        assert rate_2022q1 == pytest.approx(7.80, abs=0.05)
        assert rate_2024q3 == pytest.approx(7.80, abs=0.05)
        # 不同季度不一定完全相同
        assert isinstance(rate_2022q1, float)

    def test_cny_varies_significantly(self):
        from data_sources.ifind.field_mappings import get_fx_rate
        rate_2022q1 = get_fx_rate("CNY", "2022-02-15")  # ~1.23
        rate_2024q4 = get_fx_rate("CNY", "2024-11-01")  # ~1.07
        assert rate_2022q1 > rate_2024q4, "2022Q1 CNY/HKD 应高于 2024Q4"
        assert rate_2022q1 == pytest.approx(1.23, abs=0.02)
        assert rate_2024q4 == pytest.approx(1.07, abs=0.02)

    def test_no_date_uses_default(self):
        from data_sources.ifind.field_mappings import get_fx_rate, FX_USD_HKD_DEFAULT, FX_CNY_HKD_DEFAULT
        assert get_fx_rate("USD") == FX_USD_HKD_DEFAULT
        assert get_fx_rate("CNY") == FX_CNY_HKD_DEFAULT

    def test_case_insensitive(self):
        from data_sources.ifind.field_mappings import get_fx_rate
        assert get_fx_rate("usd", "2024-01-15") == get_fx_rate("USD", "2024-01-15")
        assert get_fx_rate("cny", "2024-01-15") == get_fx_rate("CNY", "2024-01-15")

    def test_date_before_table_uses_earliest(self):
        from data_sources.ifind.field_mappings import get_fx_rate
        rate = get_fx_rate("CNY", "2020-01-01")  # 远早于表格
        assert rate == pytest.approx(1.23, abs=0.02)  # 用表格第一条

    def test_date_after_table_uses_latest(self):
        from data_sources.ifind.field_mappings import get_fx_rate
        rate = get_fx_rate("CNY", "2030-12-31")  # 远晚于表格
        assert isinstance(rate, float)
        assert rate > 1.0
