"""data_sources.ifind.announcement_fetcher 的单元测试.

仅测试纯逻辑 (代码标准化, payload 解析, 日期工具).
不真正调 iFinD HTTP 接口.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from data_sources.ifind.announcement_fetcher import (
    _normalize_code_for_report_query,
    _to_ymd,
    _parse_payload,
    _row_to_record,
    AnnouncementRecord,
    DEFAULT_OUTPUT_FIELDS,
)


class TestNormalizeCode:
    @pytest.mark.parametrize("input_code, expected", [
        ("00700.HK", "0700.HK"),
        ("0700.HK", "0700.HK"),
        ("700.HK", "0700.HK"),
        ("09618.HK", "9618.HK"),    # 前导零剥离后 4 位 → zfill(4)
        ("0001.HK", "0001.HK"),
        ("001339.SZ", "001339.SZ"),  # A 股不处理
        ("AAPL.US", "AAPL.US"),      # 美股不处理
    ])
    def test_normalize(self, input_code, expected):
        assert _normalize_code_for_report_query(input_code) == expected

    def test_case_insensitive(self):
        assert _normalize_code_for_report_query("0700.hk") == "0700.HK"

    def test_whitespace_stripped(self):
        assert _normalize_code_for_report_query("  0700.HK  ") == "0700.HK"


class TestToYmd:
    def test_none(self):
        assert _to_ymd(None) is None

    def test_empty_string(self):
        assert _to_ymd("") is None

    def test_iso_passthrough(self):
        assert _to_ymd("2024-01-15") == "2024-01-15"

    def test_compact_yyyymmdd(self):
        assert _to_ymd("20240115") == "2024-01-15"

    def test_date_object(self):
        assert _to_ymd(date(2024, 1, 15)) == "2024-01-15"

    def test_datetime_object(self):
        assert _to_ymd(datetime(2024, 1, 15, 10, 30)) == "2024-01-15"


class TestParsePayload:
    def test_column_store_format(self):
        """列存格式: table 是 dict[field] = list[values]"""
        payload = {
            "tables": [{
                "thscode": "0700.HK",
                "secName": "腾讯控股",
                "table": {
                    "reportDate": ["2024-01-10", "2024-01-11"],
                    "reportTitle": ["公告A", "公告B"],
                    "pdfURL": ["http://a.pdf", "http://b.pdf"],
                },
            }],
        }
        records = _parse_payload(payload, DEFAULT_OUTPUT_FIELDS)
        assert len(records) == 2
        assert records[0].stock_code == "0700.HK"
        assert records[0].title == "公告A"
        assert records[1].title == "公告B"

    def test_row_store_format(self):
        """行存格式: table 是 list[dict]"""
        payload = {
            "tables": [{
                "thscode": "0001.HK",
                "secName": "长和",
                "table": [
                    {"reportDate": "20240101", "reportTitle": "年报", "pdfURL": "http://c.pdf"},
                ],
            }],
        }
        records = _parse_payload(payload, DEFAULT_OUTPUT_FIELDS)
        assert len(records) == 1
        assert records[0].stock_code == "0001.HK"
        assert records[0].title == "年报"

    def test_empty_tables(self):
        payload = {"tables": []}
        assert _parse_payload(payload, DEFAULT_OUTPUT_FIELDS) == []

    def test_data_key_fallback(self):
        """有些返回用 'data' 而非 'tables'"""
        payload = {
            "data": [{
                "thscode": "0388.HK",
                "table": [{"reportTitle": "T1"}],
            }],
        }
        records = _parse_payload(payload, DEFAULT_OUTPUT_FIELDS)
        assert len(records) == 1
        assert records[0].title == "T1"


class TestAnnouncementRecord:
    def test_to_dict(self):
        rec = AnnouncementRecord(
            stock_code="0700.HK",
            company_name="腾讯",
            title="test",
        )
        d = rec.to_dict()
        assert d["stock_code"] == "0700.HK"
        assert d["source"] == "ifind_report_query"

    def test_defaults(self):
        rec = AnnouncementRecord(stock_code="x", company_name="y")
        assert rec.announcement_date is None
        assert rec.pdf_url == ""
        assert rec.raw_fields == {}
