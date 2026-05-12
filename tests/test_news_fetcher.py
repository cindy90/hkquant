"""data_sources.akshare.news_fetcher 的单元测试.

仅测试纯逻辑 (代码标准化, 辅助函数, NewsRecord).
不真正调 akshare API.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest

from data_sources.akshare.news_fetcher import (
    normalize_hk_symbol_for_akshare,
    _safe_str,
    _parse_dt,
    NewsRecord,
    fetch_news,
)


class TestNormalizeHkSymbol:
    @pytest.mark.parametrize("input_code, expected", [
        ("00700.HK", "00700"),
        ("700.HK", "00700"),
        ("0700.HK", "00700"),
        ("00700", "00700"),
        ("700", "00700"),
        ("09618.HK", "09618"),
        ("9618", "09618"),
    ])
    def test_normalize(self, input_code, expected):
        assert normalize_hk_symbol_for_akshare(input_code) == expected

    def test_empty(self):
        assert normalize_hk_symbol_for_akshare("") == ""

    def test_case_insensitive(self):
        result = normalize_hk_symbol_for_akshare("700.hk")
        assert result == "00700"


class TestSafeStr:
    def test_none(self):
        assert _safe_str(None) == ""

    def test_normal_string(self):
        assert _safe_str("hello") == "hello"

    def test_number(self):
        assert _safe_str(123) == "123"

    def test_whitespace_stripped(self):
        assert _safe_str("  abc  ") == "abc"

    def test_pandas_nan(self):
        """如果 pandas 可用, NaN 应返回空串."""
        try:
            import pandas as pd
            assert _safe_str(float("nan")) == ""
        except ImportError:
            pytest.skip("pandas not available")


class TestParseDt:
    def test_none(self):
        assert _parse_dt(None) is None

    def test_empty_string(self):
        assert _parse_dt("") is None

    def test_date_only(self):
        result = _parse_dt("2024-01-15")
        assert result == datetime(2024, 1, 15, 0, 0, 0)

    def test_datetime_full(self):
        result = _parse_dt("2024-01-15 14:30:00")
        assert result == datetime(2024, 1, 15, 14, 30, 0)

    def test_invalid_format(self):
        assert _parse_dt("20240115") is None


class TestNewsRecord:
    def test_to_dict(self):
        rec = NewsRecord(
            stock_code="00700.HK",
            company_keyword="腾讯",
            headline="测试新闻",
        )
        d = rec.to_dict()
        assert d["stock_code"] == "00700.HK"
        assert d["fetched_via"] == "akshare:stock_news_em"

    def test_defaults(self):
        rec = NewsRecord(stock_code="x", company_keyword="y", headline="z")
        assert rec.published_at is None
        assert rec.content == ""
        assert rec.source_url == ""


class TestFetchNewsMocked:
    def test_import_error_if_no_akshare(self, monkeypatch):
        """akshare 未安装时应抛 ImportError."""
        import data_sources.akshare.news_fetcher as mod
        # 模拟 akshare 不可用
        with patch.dict("sys.modules", {"akshare": None}):
            with pytest.raises(ImportError, match="akshare"):
                fetch_news("00700.HK")

    def test_empty_symbol_returns_empty(self):
        # 空代码直接返回 []
        # fetch_news 内部会先标准化, 空字符串 → ''
        # 但由于 akshare import 在函数内, 我们 mock 它
        mock_ak = MagicMock()
        with patch.dict("sys.modules", {"akshare": mock_ak}):
            result = fetch_news("")
            assert result == []
