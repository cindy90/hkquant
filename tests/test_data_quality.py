"""data.data_quality 模块的单元测试."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from data.data_quality import (
    CORE_FIELDS,
    compute_row_quality,
    refresh_quality_scores,
    generate_quality_report,
    save_quality_report,
)
from data.schema import init_database


@pytest.fixture
def db_conn(tmp_path):
    """创建一个带 schema 的内存数据库并预填几行."""
    db_path = str(tmp_path / "test_quality.db")
    init_database(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    # 完整行
    conn.execute("""
        INSERT INTO ipo_master (
            ipo_id, stock_code, company_name_zh, listing_date, listing_chapter,
            offer_price_hkd, offering_size_hkd, total_offer_shares,
            intl_oversub, public_oversub, cornerstone_coverage, cornerstone_count,
            sponsor_primary, sponsor_tier, pe_at_offer, pe_peer_median, status
        ) VALUES (
            'HK_09999_2024', '09999.HK', '测试公司A', '2024-01-15', 'main_board',
            100.0, 5e9, 50000000,
            5.0, 200.0, 0.45, 8,
            'Goldman Sachs', 1, 25.0, 22.0, 'listed'
        )
    """)

    # 部分缺失行
    conn.execute("""
        INSERT INTO ipo_master (
            ipo_id, stock_code, company_name_zh, listing_date, listing_chapter,
            offer_price_hkd, status
        ) VALUES (
            'HK_01234_2024', '01234.HK', '测试公司B', '2024-03-01', 'main_board',
            50.0, 'listed'
        )
    """)

    # 最少字段行 (仅 NOT NULL 字段)
    conn.execute("""
        INSERT INTO ipo_master (
            ipo_id, stock_code, listing_date, listing_chapter, status
        ) VALUES (
            'HK_05678_2024', '05678.HK', '2024-06-01', '18a', 'listed'
        )
    """)

    conn.commit()
    yield conn
    conn.close()


class TestComputeRowQuality:
    def test_full_row(self):
        row = {f: "some_value" for f in CORE_FIELDS}
        assert compute_row_quality(row) == 1.0

    def test_empty_row(self):
        row = {}
        assert compute_row_quality(row) == 0.0

    def test_half_fields(self):
        half = CORE_FIELDS[: len(CORE_FIELDS) // 2]
        row = {f: "v" for f in half}
        expected = round(len(half) / len(CORE_FIELDS), 4)
        assert compute_row_quality(row) == expected

    def test_none_values_not_counted(self):
        row = {f: None for f in CORE_FIELDS}
        assert compute_row_quality(row) == 0.0

    def test_empty_string_not_counted(self):
        row = {f: "" for f in CORE_FIELDS}
        assert compute_row_quality(row) == 0.0


class TestRefreshQualityScores:
    def test_updates_all_rows(self, db_conn):
        n = refresh_quality_scores(db_conn)
        assert n == 3

    def test_full_row_gets_1(self, db_conn):
        refresh_quality_scores(db_conn)
        row = db_conn.execute(
            "SELECT data_quality_score FROM ipo_master WHERE ipo_id='HK_09999_2024'"
        ).fetchone()
        assert row["data_quality_score"] == 1.0

    def test_partial_row_gets_fractional(self, db_conn):
        refresh_quality_scores(db_conn)
        row = db_conn.execute(
            "SELECT data_quality_score FROM ipo_master WHERE ipo_id='HK_01234_2024'"
        ).fetchone()
        score = row["data_quality_score"]
        # stock_code, company_name_zh, listing_date, listing_chapter, offer_price_hkd = 5/15
        assert 0.3 <= score <= 0.4

    def test_minimal_row_gets_low_score(self, db_conn):
        refresh_quality_scores(db_conn)
        row = db_conn.execute(
            "SELECT data_quality_score FROM ipo_master WHERE ipo_id='HK_05678_2024'"
        ).fetchone()
        score = row["data_quality_score"]
        # stock_code, listing_date, listing_chapter = 3/15
        assert score == round(3 / 15, 4)


class TestGenerateQualityReport:
    def test_report_structure(self, db_conn):
        refresh_quality_scores(db_conn)
        report = generate_quality_report(db_conn)
        assert report["total_ipos"] == 3
        assert "avg_quality_score" in report
        assert "score_distribution" in report
        assert "field_coverage" in report
        assert "worst_ipos" in report

    def test_field_coverage_complete_field(self, db_conn):
        refresh_quality_scores(db_conn)
        report = generate_quality_report(db_conn)
        # stock_code 三行都有
        assert report["field_coverage"]["stock_code"] == 1.0

    def test_field_coverage_partial_field(self, db_conn):
        refresh_quality_scores(db_conn)
        report = generate_quality_report(db_conn)
        # pe_at_offer 只有第一行有 → 1/3
        assert report["field_coverage"]["pe_at_offer"] == round(1 / 3, 4)

    def test_worst_ipos_ordered(self, db_conn):
        refresh_quality_scores(db_conn)
        report = generate_quality_report(db_conn)
        scores = [w["score"] for w in report["worst_ipos"]]
        assert scores == sorted(scores)

    def test_distribution_sums(self, db_conn):
        refresh_quality_scores(db_conn)
        report = generate_quality_report(db_conn)
        dist = report["score_distribution"]
        total = sum(dist.values())
        assert total == 3


class TestSaveQualityReport:
    def test_creates_json_file(self, tmp_path, db_conn):
        refresh_quality_scores(db_conn)
        report = generate_quality_report(db_conn)
        out = tmp_path / "quality.json"
        result = save_quality_report(report, out)
        assert result == out
        assert out.exists()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["total_ipos"] == 3
