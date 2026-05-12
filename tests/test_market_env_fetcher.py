"""data_sources.ifind.market_env_fetcher 的单元测试.

仅测试 _hq_unpack 纯逻辑 (不调 iFinD SDK).
"""
from __future__ import annotations

import pytest

from data_sources.ifind.market_env_fetcher import _hq_unpack


class TestHqUnpack:
    """_hq_unpack 解析 THS_HistoryQuotes 返回的两种格式."""

    def test_object_format_with_dataframe(self):
        """形态 B: 有 .errorcode + .data (DataFrame-like)."""
        import types

        # 模拟 DataFrame
        class FakeDF:
            columns = ["close", "open", "time"]

            def __getitem__(self, key):
                data = {
                    "close": FakeSeries([100.0, 105.0, 110.0]),
                    "open": FakeSeries([99.0, 104.0, 109.0]),
                    "time": FakeSeries(["2024-01-01", "2024-01-02", "2024-01-03"]),
                }
                return data[key]

        class FakeSeries:
            def __init__(self, vals):
                self._vals = vals

            def dropna(self):
                return self

            def tolist(self):
                return self._vals

        result = types.SimpleNamespace(
            errorcode=0,
            errmsg="ok",
            data=FakeDF(),
        )

        out = _hq_unpack(result)
        assert out.errorcode == 0
        assert out.closes == [100.0, 105.0, 110.0]
        assert out.opens == [99.0, 104.0, 109.0]
        assert len(out.times) == 3

    def test_dict_format(self):
        """形态 A: OrderedDict 返回."""
        result = {
            "errorcode": 0,
            "errmsg": "ok",
            "tables": [{
                "table": {
                    "close": [100.0, 105.0, 110.0],
                    "open": [99.0, 104.0, 109.0],
                },
                "time": ["2024-01-01", "2024-01-02", "2024-01-03"],
            }],
        }

        out = _hq_unpack(result)
        assert out.errorcode == 0
        assert out.closes == [100.0, 105.0, 110.0]
        assert out.opens == [99.0, 104.0, 109.0]
        assert out.times == ["2024-01-01", "2024-01-02", "2024-01-03"]

    def test_dict_format_with_indicator(self):
        """列存带 indicator 字段 (非 close/open)."""
        result = {
            "errorcode": 0,
            "errmsg": "ok",
            "tables": [{
                "table": {
                    "ths_pe_ttm_index": [12.5, 13.0, 11.8],
                },
            }],
        }
        out = _hq_unpack(result)
        assert out.values == [12.5, 13.0, 11.8]

    def test_error_code_propagated(self):
        """非 0 errorcode 应传递."""
        result = {
            "errorcode": -5003,
            "errmsg": "no data",
            "tables": [],
        }
        out = _hq_unpack(result)
        assert out.errorcode == -5003
        assert out.closes == []

    def test_unknown_type_returns_error(self):
        """未知返回类型."""
        out = _hq_unpack("unexpected_string")
        assert out.errorcode == -1
        assert "unknown" in out.errmsg.lower()

    def test_empty_tables(self):
        result = {"errorcode": 0, "errmsg": "", "tables": []}
        out = _hq_unpack(result)
        assert out.closes == []
        assert out.values == []

    def test_none_values_filtered_in_dict_format(self):
        """close 列表中有 None 值应被跳过."""
        result = {
            "errorcode": 0,
            "errmsg": "ok",
            "tables": [{
                "table": {
                    "close": [100.0, None, 110.0],
                },
            }],
        }
        out = _hq_unpack(result)
        assert out.closes == [100.0, 110.0]
