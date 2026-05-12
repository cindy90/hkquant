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

    def test_eur_supported(self):
        from data_sources.ifind.field_mappings import get_fx_rate
        rate = get_fx_rate("EUR", "2024-06-15")
        assert 7.5 < rate < 9.5, f"EUR/HKD 应在合理范围, got {rate}"

    def test_gbp_supported(self):
        from data_sources.ifind.field_mappings import get_fx_rate
        rate = get_fx_rate("GBP", "2024-06-15")
        assert 9.0 < rate < 11.0, f"GBP/HKD 应在合理范围, got {rate}"

    def test_jpy_supported(self):
        from data_sources.ifind.field_mappings import get_fx_rate
        rate = get_fx_rate("JPY", "2024-06-15")
        assert 0.04 < rate < 0.08, f"JPY/HKD 应在合理范围, got {rate}"

    def test_eur_no_date_default(self):
        from data_sources.ifind.field_mappings import get_fx_rate
        rate = get_fx_rate("EUR")
        assert isinstance(rate, float)
        assert rate > 7.0

    def test_gbp_no_date_default(self):
        from data_sources.ifind.field_mappings import get_fx_rate
        rate = get_fx_rate("GBP")
        assert isinstance(rate, float)
        assert rate > 9.0

    def test_jpy_no_date_default(self):
        from data_sources.ifind.field_mappings import get_fx_rate
        rate = get_fx_rate("JPY")
        assert isinstance(rate, float)
        assert 0.04 < rate < 0.08

    def test_unsupported_currency_raises(self):
        from data_sources.ifind.field_mappings import get_fx_rate
        with pytest.raises(ValueError, match="不支持的币种"):
            get_fx_rate("CHF", "2024-01-15")

    def test_unsupported_currency_no_date_raises(self):
        from data_sources.ifind.field_mappings import get_fx_rate
        with pytest.raises(ValueError, match="不支持的币种"):
            get_fx_rate("KRW")


# =============================================================================
# P2.7: ETL 输入校验
# =============================================================================

# =============================================================================
# load_financials (ipo_financials ETL)
# =============================================================================

class TestLoadFinancials:
    @pytest.fixture
    def fin_csv(self, tmp_path):
        p = tmp_path / "ifind_financials_annual.csv"
        p.write_text(
            "thscode,total_oi,gross_selling_rate,net_profit_margin_on_sales,"
            "ths_roe_hks,ni_attr_to_cs,report_year\n"
            "1187.HK,2976958000.0,37.842,10.1452,6.0092,301683000.0,2022\n"
            "1609.HK,146596000.0,70.9017,27.5192,14.2898,40342000.0,2022\n"
            "2493.HK,,,,,,2022\n"
            "1187.HK,3500000000.0,38.5,11.0,7.0,350000000.0,2023\n",
            encoding="utf-8",
        )
        return p

    def test_basic_load(self, empty_db, fin_csv):
        import sqlite3
        from data.dao import db_connect
        from data_sources.ifind.load_to_db import load_financials

        with db_connect(str(empty_db)) as conn:
            stats = load_financials(conn, fin_csv, dry_run=False)

        assert stats.n_rows_csv == 4
        assert stats.n_upserted == 4
        assert stats.n_all_null == 1  # 2493.HK

        with sqlite3.connect(str(empty_db)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM ipo_financials ORDER BY stock_code, report_year").fetchall()
            assert len(rows) == 4

            # 检查 1187.HK 2022
            r = conn.execute(
                "SELECT * FROM ipo_financials WHERE stock_code='1187.HK' AND report_year=2022"
            ).fetchone()
            assert r["revenue_cny"] == pytest.approx(2976958000.0)
            assert r["gross_margin"] == pytest.approx(37.842)
            assert r["net_margin"] == pytest.approx(10.1452)
            assert r["roe"] == pytest.approx(6.0092)

    def test_idempotent_upsert(self, empty_db, fin_csv):
        """重跑不应重复行."""
        import sqlite3
        from data.dao import db_connect
        from data_sources.ifind.load_to_db import load_financials

        with db_connect(str(empty_db)) as conn:
            load_financials(conn, fin_csv, dry_run=False)
            load_financials(conn, fin_csv, dry_run=False)

        with sqlite3.connect(str(empty_db)) as conn:
            n = conn.execute("SELECT COUNT(*) FROM ipo_financials").fetchone()[0]
            assert n == 4  # 不重复

    def test_null_row_still_inserted(self, empty_db, fin_csv):
        """全 NULL 行仍写入 (标记'已查询过')."""
        import sqlite3
        from data.dao import db_connect
        from data_sources.ifind.load_to_db import load_financials

        with db_connect(str(empty_db)) as conn:
            load_financials(conn, fin_csv, dry_run=False)

        with sqlite3.connect(str(empty_db)) as conn:
            conn.row_factory = sqlite3.Row
            r = conn.execute(
                "SELECT * FROM ipo_financials WHERE stock_code='2493.HK'"
            ).fetchone()
            assert r is not None
            assert r["revenue_cny"] is None

    def test_dry_run_no_writes(self, empty_db, fin_csv):
        import sqlite3
        from data.dao import db_connect
        from data_sources.ifind.load_to_db import load_financials

        with db_connect(str(empty_db)) as conn:
            stats = load_financials(conn, fin_csv, dry_run=True)

        assert stats.n_upserted == 4
        with sqlite3.connect(str(empty_db)) as conn:
            n = conn.execute("SELECT COUNT(*) FROM ipo_financials").fetchone()[0]
            assert n == 0

    def test_upsert_coalesce_fills_gaps(self, empty_db, tmp_path):
        """UPSERT 用 COALESCE 补全之前的 NULL 字段."""
        import sqlite3
        from data.dao import db_connect
        from data_sources.ifind.load_to_db import load_financials

        # 第一轮: 只有 revenue
        csv1 = tmp_path / "fin1.csv"
        csv1.write_text(
            "thscode,total_oi,gross_selling_rate,net_profit_margin_on_sales,"
            "ths_roe_hks,ni_attr_to_cs,report_year\n"
            "9999.HK,1000000.0,,,,,2023\n",
            encoding="utf-8",
        )
        with db_connect(str(empty_db)) as conn:
            load_financials(conn, csv1, dry_run=False)

        # 第二轮: 只有 gross_margin
        csv2 = tmp_path / "fin2.csv"
        csv2.write_text(
            "thscode,total_oi,gross_selling_rate,net_profit_margin_on_sales,"
            "ths_roe_hks,ni_attr_to_cs,report_year\n"
            "9999.HK,,50.0,,,,2023\n",
            encoding="utf-8",
        )
        with db_connect(str(empty_db)) as conn:
            load_financials(conn, csv2, dry_run=False)

        with sqlite3.connect(str(empty_db)) as conn:
            conn.row_factory = sqlite3.Row
            r = conn.execute(
                "SELECT * FROM ipo_financials WHERE stock_code='9999.HK'"
            ).fetchone()
            # revenue 保留, gross_margin 补齐
            assert r["revenue_cny"] == pytest.approx(1000000.0)
            assert r["gross_margin"] == pytest.approx(50.0)


# =============================================================================
# validate_chapters (章节校验)
# =============================================================================

class TestValidateChapters:
    def test_valid_chapters(self, empty_db):
        import sqlite3
        from data.dao import db_connect, upsert_ipo
        from data_sources.ifind.load_to_db import validate_chapters

        with db_connect(str(empty_db)) as conn:
            upsert_ipo(conn, ipo_id="HK_0001_2024", stock_code="0001.HK",
                       listing_date="2024-01-15", listing_chapter="main_board")
            upsert_ipo(conn, ipo_id="HK_9999_2024", stock_code="9999.HK",
                       listing_date="2024-02-01", listing_chapter="18a")
            upsert_ipo(conn, ipo_id="HK_1234_2024", stock_code="1234.HK",
                       listing_date="2024-03-01", listing_chapter="a_plus_h")
            result = validate_chapters(conn)

        assert result.total_ipos == 3
        assert result.n_valid == 2  # 18a + a_plus_h
        assert result.n_defaulted_main_board == 1
        assert result.n_invalid_chapter == 0
        assert result.chapter_distribution["main_board"] == 1
        assert result.chapter_distribution["18a"] == 1

    def test_high_main_board_warning(self, empty_db):
        from data.dao import db_connect, upsert_ipo
        from data_sources.ifind.load_to_db import validate_chapters

        with db_connect(str(empty_db)) as conn:
            for i in range(20):
                upsert_ipo(conn, ipo_id=f"HK_{i:04d}_2024", stock_code=f"{i:04d}.HK",
                           listing_date="2024-01-15", listing_chapter="main_board")
            result = validate_chapters(conn)

        assert result.n_defaulted_main_board == 20
        assert any("main_board" in iss and "90%" in iss for iss in result.issues)

    def test_chapter_stats_in_load_ipo_info(self, empty_db, tmp_path):
        from data.dao import db_connect
        from data_sources.ifind.load_to_db import load_ipo_info

        # CSV 无 chapter override → 全默认 main_board
        p = tmp_path / "ipo.csv"
        p.write_text(
            "p05310_f001,p05310_f002,p05310_f033,p05310_f010\n"
            "00001.HK,测试,2024-01-15,50.0\n"
            "00002.HK,测试2,2024-02-01,60.0\n",
            encoding="utf-8",
        )
        with db_connect(str(empty_db)) as conn:
            stats = load_ipo_info(conn, p, dry_run=False,
                                  overrides={}, asof_today="2025-01-01")

        assert stats.n_chapter_defaulted == 2
        assert stats.n_chapter_invalid == 0


# =============================================================================
# load_delisted 交叉验证
# =============================================================================

class TestDelistedCrossValidation:
    def test_date_invalid_detected(self, empty_db, tmp_path):
        from data.dao import db_connect, upsert_ipo
        from data_sources.ifind.load_to_db import load_delisted

        p = tmp_path / "del.csv"
        p.write_text(
            "stock_code,delisting_date,delisting_reason,is_acquired\n"
            "0001.HK,2023-01-01,liquidated,0\n",  # 退市日 < 上市日
            encoding="utf-8",
        )
        with db_connect(str(empty_db)) as conn:
            upsert_ipo(conn, ipo_id="HK_0001_2024", stock_code="0001.HK",
                       listing_date="2024-06-01", listing_chapter="main_board")
            stats = load_delisted(conn, p, dry_run=False)

        assert stats.n_date_invalid == 1
        assert stats.n_matched == 1  # 仍然写入

    def test_normal_delisting_no_date_issue(self, empty_db, tmp_path):
        from data.dao import db_connect, upsert_ipo
        from data_sources.ifind.load_to_db import load_delisted

        p = tmp_path / "del.csv"
        p.write_text(
            "stock_code,delisting_date,delisting_reason,is_acquired\n"
            "0001.HK,2025-12-31,acquired,1\n",
            encoding="utf-8",
        )
        with db_connect(str(empty_db)) as conn:
            upsert_ipo(conn, ipo_id="HK_0001_2024", stock_code="0001.HK",
                       listing_date="2024-06-01", listing_chapter="main_board")
            stats = load_delisted(conn, p, dry_run=False)

        assert stats.n_date_invalid == 0
        assert stats.n_matched == 1


class TestIpoInfoSanitization:
    """load_ipo_info 的输入校验逻辑测试."""

    @pytest.fixture
    def csv_with_bad_data(self, tmp_path):
        """构造一份含多种不合理值的 CSV."""
        p = tmp_path / "ifind_ipo_info.csv"
        # 使用 P05310_IPO_INFO 的 raw header (与 field_mappings.py 一致)
        # f001=stock_code, f002=company_name_zh, f033=listing_date,
        # f010=offer_price_hkd, f008=offer_price_high, f023=offering_size_hkd,
        # f052=intl_oversub, f027=public_oversub, f050=cornerstone_coverage,
        # f028=pricing_date
        header = (
            "p05310_f001,p05310_f002,p05310_f033,p05310_f010,"
            "p05310_f008,p05310_f023,p05310_f052,p05310_f027,"
            "p05310_f050,p05310_f028\n"
        )
        rows = [
            # 正常行
            "00001.HK,测试A,2024-01-15,100.0,120.0,5000000000.0,3.5,200.0,35.76,2024-01-10\n",
            # offer_price <= 0
            "00002.HK,测试B,2024-02-01,-5.0,60.0,1000000000.0,2.0,100.0,20.00,2024-01-25\n",
            # pricing_date > listing_date
            "00003.HK,测试C,2024-03-01,50.0,60.0,2000000000.0,1.5,50.0,40.00,2024-04-01\n",
            # coverage > 100% (即 > 1.0 after /100)
            "00004.HK,测试D,2024-04-01,80.0,90.0,3000000000.0,5.0,300.0,150.00,2024-03-25\n",
            # intl_oversub < 0
            "00005.HK,测试E,2024-05-01,30.0,40.0,1500000000.0,-2.0,100.0,25.00,2024-04-25\n",
            # offering_size <= 0
            "00006.HK,测试F,2024-06-01,60.0,70.0,-500.0,4.0,150.0,30.00,2024-05-25\n",
            # offer_price_high < offer_price
            "00007.HK,测试G,2024-07-01,100.0,80.0,4000000000.0,6.0,250.0,45.00,2024-06-25\n",
        ]
        p.write_text(header + "".join(rows), encoding="utf-8")
        return p

    def test_sanitizes_bad_values(self, empty_db, csv_with_bad_data):
        import sqlite3
        from data.dao import db_connect
        from data_sources.ifind.load_to_db import load_ipo_info

        with db_connect(str(empty_db)) as conn:
            stats = load_ipo_info(conn, csv_with_bad_data, dry_run=False,
                                  overrides={}, asof_today="2025-01-01")

        assert stats.n_inserted == 7
        assert stats.n_sanitized >= 6  # 至少 6 个字段被清洗

        with sqlite3.connect(str(empty_db)) as conn:
            conn.row_factory = sqlite3.Row

            # 00002: offer_price <= 0 → NULL
            r = conn.execute("SELECT offer_price_hkd FROM ipo_master WHERE stock_code='00002.HK'").fetchone()
            assert r["offer_price_hkd"] is None

            # 00003: pricing_date > listing_date → pricing_date NULL
            r = conn.execute("SELECT pricing_date FROM ipo_master WHERE stock_code='00003.HK'").fetchone()
            assert r["pricing_date"] is None

            # 00004: coverage 150% → > 1.0 → NULL
            r = conn.execute("SELECT cornerstone_coverage FROM ipo_master WHERE stock_code='00004.HK'").fetchone()
            assert r["cornerstone_coverage"] is None

            # 00005: intl_oversub < 0 → NULL
            r = conn.execute("SELECT intl_oversub FROM ipo_master WHERE stock_code='00005.HK'").fetchone()
            assert r["intl_oversub"] is None

            # 00006: offering_size <= 0 → NULL
            r = conn.execute("SELECT offering_size_hkd FROM ipo_master WHERE stock_code='00006.HK'").fetchone()
            assert r["offering_size_hkd"] is None

            # 00007: offer_price_high < offer_price → high NULL
            r = conn.execute("SELECT offer_price_high FROM ipo_master WHERE stock_code='00007.HK'").fetchone()
            assert r["offer_price_high"] is None

    def test_normal_row_not_affected(self, empty_db, csv_with_bad_data):
        import sqlite3
        from data.dao import db_connect
        from data_sources.ifind.load_to_db import load_ipo_info

        with db_connect(str(empty_db)) as conn:
            load_ipo_info(conn, csv_with_bad_data, dry_run=False,
                          overrides={}, asof_today="2025-01-01")

        with sqlite3.connect(str(empty_db)) as conn:
            conn.row_factory = sqlite3.Row
            r = conn.execute("SELECT * FROM ipo_master WHERE stock_code='00001.HK'").fetchone()
            assert r["offer_price_hkd"] == pytest.approx(100.0)
            assert r["cornerstone_coverage"] == pytest.approx(0.3576, abs=0.001)


class TestCornerstoneSanitization:
    """load_cornerstones 的输入校验逻辑测试."""

    @pytest.fixture
    def cs_csv_with_bad_data(self, tmp_path):
        p = tmp_path / "ifind_cornerstones.csv"
        # P05309: f001=stock_code, f003=listing_date, f005=cornerstone_name,
        #         f008=ticket_size_hkd, f011=currency, f009=allocation_shares
        header = (
            "p05309_f001,p05309_f003,p05309_f005,"
            "p05309_f008,p05309_f011,p05309_f009\n"
        )
        rows = [
            # 正常行
            "00001.HK,2024-01-15,投资者A,50000000.0,HKD,1000000\n",
            # ticket_size <= 0
            "00001.HK,2024-01-15,投资者B,-100.0,HKD,500000\n",
            # EUR: 现在是支持币种, 应正确换算
            "00001.HK,2024-01-15,投资者C,30000000.0,EUR,800000\n",
            # 真正未知的 currency (CHF)
            "00001.HK,2024-01-15,投资者D,20000000.0,CHF,600000\n",
        ]
        p.write_text(header + "".join(rows), encoding="utf-8")
        return p

    def test_negative_ticket_nullified(self, empty_db, cs_csv_with_bad_data):
        import sqlite3
        from data.dao import db_connect, upsert_ipo
        from data_sources.ifind.load_to_db import load_cornerstones

        with db_connect(str(empty_db)) as conn:
            upsert_ipo(conn, ipo_id="HK_00001_HK_2024", stock_code="00001.HK",
                       listing_date="2024-01-15", listing_chapter="main_board")
            stats = load_cornerstones(conn, cs_csv_with_bad_data, dry_run=False)

        assert stats.n_sanitized >= 1
        with sqlite3.connect(str(empty_db)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT cornerstone_name, ticket_size_hkd FROM ipo_cornerstone_link "
                "ORDER BY cornerstone_name"
            ).fetchall()
            by_name = {r["cornerstone_name"]: r for r in rows}
            assert by_name["投资者B"]["ticket_size_hkd"] is None

    def test_eur_correctly_converted(self, empty_db, cs_csv_with_bad_data):
        """EUR 现在是支持币种, ticket_size_hkd 应为 native × EUR/HKD 汇率."""
        import sqlite3
        from data.dao import db_connect, upsert_ipo
        from data_sources.ifind.load_to_db import load_cornerstones

        with db_connect(str(empty_db)) as conn:
            upsert_ipo(conn, ipo_id="HK_00001_HK_2024", stock_code="00001.HK",
                       listing_date="2024-01-15", listing_chapter="main_board")
            stats = load_cornerstones(conn, cs_csv_with_bad_data, dry_run=False)

        assert stats.n_unknown_currency >= 1  # CHF 仍是未知

        with sqlite3.connect(str(empty_db)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT cornerstone_name, ticket_size_hkd, ticket_size_native, currency "
                "FROM ipo_cornerstone_link ORDER BY cornerstone_name"
            ).fetchall()
            by_name = {r["cornerstone_name"]: r for r in rows}
            # EUR row: native=30M, hkd = 30M × ~8.62 ≈ ~258M
            eur_row = by_name["投资者C"]
            assert eur_row["currency"] == "EUR"
            assert eur_row["ticket_size_native"] == pytest.approx(30_000_000.0)
            assert eur_row["ticket_size_hkd"] > 200_000_000  # 30M EUR × ~8.x

    def test_unknown_currency_logged(self, empty_db, cs_csv_with_bad_data):
        from data.dao import db_connect, upsert_ipo
        from data_sources.ifind.load_to_db import load_cornerstones

        with db_connect(str(empty_db)) as conn:
            upsert_ipo(conn, ipo_id="HK_00001_HK_2024", stock_code="00001.HK",
                       listing_date="2024-01-15", listing_chapter="main_board")
            stats = load_cornerstones(conn, cs_csv_with_bad_data, dry_run=False)

        assert stats.n_unknown_currency >= 1  # CHF 是未知的
