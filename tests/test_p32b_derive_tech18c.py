"""
P3.2.B — _derive_tech18c 测试: 从 ipo_financials CNY 数据推 TechC18Fundamentals.

旧版 build_offering 给 18C 用 hardcoded growth=0.30 stub, 导致 backtest 路径上
18C 的 PS/G 子项始终走"假 growth", 没真信号. 新版从 ipo_financials.revenue_cny
按 fx_cny_hkd=1.10 转 HKD, 算最近一年 revenue_latest_hkd + YoY growth.

覆盖:
    - 最近 2 年财务齐 → revenue 转 HKD + YoY 计算正确
    - 单年财务 → revenue 有, growth 走 stub 0.30
    - 无财务 → revenue=None, growth 走 stub
    - revenue=None 或 0 行被跳过 (yrs 仅取有 revenue 的)
    - 极端 YoY (>2.0 / <-0.50) 自动 clip 防虚高
    - is_commercial 跟 chapter 联动 (commercial vs precommercial)
    - fx 一致性: 默认 1.10 跟 panel.compute_panel_aggregates 同口径
"""
from __future__ import annotations

import pytest


# =============================================================================
# Helpers
# =============================================================================

def _fin(*years_revenue):
    """构造 fin dict: _fin((2022, 1e9), (2023, 2e9))"""
    return {y: {"revenue": r, "gm": None, "nm": None, "roe": None}
            for (y, r) in years_revenue}


# =============================================================================
# Two-year YoY
# =============================================================================

class TestYoYComputation:
    def test_two_years_yoy_correct(self):
        from run_v7_backtest import _derive_tech18c, _FX_CNY_TO_HKD
        from nacs_model import ListingChapter
        # 2022: 1B CNY, 2023: 2B CNY → YoY = 1.0 (100%)
        fin = _fin((2022, 1e9), (2023, 2e9))
        t = _derive_tech18c(fin, ListingChapter.CHAPTER_18C_COMMERCIAL)
        # latest revenue HKD = 2B * 1.10 = 2.2B
        assert t.revenue_latest_hkd == pytest.approx(2.2e9, rel=0.001)
        # YoY = 100%
        assert t.revenue_growth_yoy == pytest.approx(1.0, abs=0.001)
        assert t.is_commercial is True

    def test_growth_negative(self):
        """收入下滑 (-30%): 1.5B → 1.05B"""
        from run_v7_backtest import _derive_tech18c
        from nacs_model import ListingChapter
        fin = _fin((2022, 1.5e9), (2023, 1.05e9))
        t = _derive_tech18c(fin, ListingChapter.CHAPTER_18C_COMMERCIAL)
        assert t.revenue_growth_yoy == pytest.approx(-0.30, abs=0.001)

    def test_growth_clipped_high(self):
        """100x 增长被 clip 到 2.0 (base effect 防虚高)"""
        from run_v7_backtest import _derive_tech18c
        from nacs_model import ListingChapter
        fin = _fin((2022, 1e7), (2023, 1e9))   # 100x
        t = _derive_tech18c(fin, ListingChapter.CHAPTER_18C_COMMERCIAL)
        assert t.revenue_growth_yoy == pytest.approx(2.0, abs=0.001)

    def test_growth_clipped_low(self):
        """-90% 被 clip 到 -0.50"""
        from run_v7_backtest import _derive_tech18c
        from nacs_model import ListingChapter
        fin = _fin((2022, 10e9), (2023, 1e9))
        t = _derive_tech18c(fin, ListingChapter.CHAPTER_18C_COMMERCIAL)
        assert t.revenue_growth_yoy == pytest.approx(-0.50, abs=0.001)


# =============================================================================
# 单年 / 无数据 fallback
# =============================================================================

class TestFallback:
    def test_single_year_revenue_only(self):
        """只有 1 年 revenue → revenue 有, growth=0.30 stub"""
        from run_v7_backtest import _derive_tech18c
        from nacs_model import ListingChapter
        fin = _fin((2023, 2e9))
        t = _derive_tech18c(fin, ListingChapter.CHAPTER_18C_COMMERCIAL)
        assert t.revenue_latest_hkd == pytest.approx(2.2e9, rel=0.001)
        assert t.revenue_growth_yoy == pytest.approx(0.30)   # stub

    def test_no_financials_at_all(self):
        from run_v7_backtest import _derive_tech18c
        from nacs_model import ListingChapter
        t = _derive_tech18c(None, ListingChapter.CHAPTER_18C_COMMERCIAL)
        assert t.revenue_latest_hkd is None
        assert t.revenue_growth_yoy == pytest.approx(0.30)
        assert t.is_commercial is True

    def test_empty_financials_dict(self):
        from run_v7_backtest import _derive_tech18c
        from nacs_model import ListingChapter
        t = _derive_tech18c({}, ListingChapter.CHAPTER_18C_COMMERCIAL)
        assert t.revenue_latest_hkd is None
        assert t.revenue_growth_yoy == pytest.approx(0.30)

    def test_all_revenues_none(self):
        """fin 有 entries 但 revenue 都 None → fallback"""
        from run_v7_backtest import _derive_tech18c
        from nacs_model import ListingChapter
        fin = {2022: {"revenue": None, "gm": 0.30, "nm": 0.10, "roe": 0.05},
               2023: {"revenue": None, "gm": 0.32, "nm": 0.11, "roe": 0.06}}
        t = _derive_tech18c(fin, ListingChapter.CHAPTER_18C_COMMERCIAL)
        assert t.revenue_latest_hkd is None
        assert t.revenue_growth_yoy == pytest.approx(0.30)

    def test_prior_revenue_zero_falls_back(self):
        """prior year revenue=0 → 不能除零, 走 stub"""
        from run_v7_backtest import _derive_tech18c
        from nacs_model import ListingChapter
        # rev_cny 列表过滤掉 None, 但保留 0... 让我看
        # 实际上 _derive_tech18c 只过滤 falsy revenue (None / 0); 列表 = [2e9]
        # 这就 len < 2 → stub
        fin = {2022: {"revenue": 0, "gm": None, "nm": None, "roe": None},
               2023: {"revenue": 2e9, "gm": None, "nm": None, "roe": None}}
        t = _derive_tech18c(fin, ListingChapter.CHAPTER_18C_COMMERCIAL)
        assert t.revenue_latest_hkd == pytest.approx(2.2e9, rel=0.001)
        # 单年 → stub
        assert t.revenue_growth_yoy == pytest.approx(0.30)


# =============================================================================
# Chapter-driven is_commercial
# =============================================================================

class TestCommercialFlag:
    def test_18c_commercial_chapter(self):
        from run_v7_backtest import _derive_tech18c
        from nacs_model import ListingChapter
        t = _derive_tech18c(None, ListingChapter.CHAPTER_18C_COMMERCIAL)
        assert t.is_commercial is True

    def test_18c_precommercial_chapter(self):
        from run_v7_backtest import _derive_tech18c
        from nacs_model import ListingChapter
        t = _derive_tech18c(None, ListingChapter.CHAPTER_18C_PRECOMMERCIAL)
        assert t.is_commercial is False


# =============================================================================
# FX 一致性: 默认 1.10 跟 compute_panel_aggregates 必须一致
# =============================================================================

class TestFXConsistency:
    def test_fx_constant_matches_panel_default(self):
        """run_v7_backtest._FX_CNY_TO_HKD 跟 compute_panel_aggregates 默认 fx 一致.
           不一致会让 deal 的 ps_at_offer 跟 panel ps_peer_median 不在同一单位上.
        """
        import inspect
        from run_v7_backtest import _FX_CNY_TO_HKD
        from data.panel_snapshot import compute_panel_aggregates
        sig = inspect.signature(compute_panel_aggregates)
        panel_default = sig.parameters["fx_cny_hkd"].default
        assert panel_default == _FX_CNY_TO_HKD, \
            f"panel fx={panel_default} 跟 backtest fx={_FX_CNY_TO_HKD} 不一致, " \
            f"会让 deal P/S 跟 peer P/S 量级偏差"
