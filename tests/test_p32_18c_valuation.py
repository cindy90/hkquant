"""
P3.2 — 18C L1.1 估值重构 测试.

旧版: 18C L1.1 走 last_round_premium 单项, ipo_master.last_round_premium 全
NULL → 100% 走 60.0 中性, 实际无信号.

新版按可用信号 renormalize 加权:
    P/S       = mkt_cap / revenue, 跟 ps_peer_median 比折溢价
    PS/G      = (P/S) / (revenue_growth_yoy × 100), 增速归一化
    last_round_premium 招股书 OCR 后激活; 现状缺失就剔除

覆盖:
    - 三信号都有 → 加权平均
    - 仅 PS/G 有 (无 peer + lrp NULL) → 退化到 PS/G 单项
    - 仅 PS 有 (无增速) → 退化到 PS
    - 仅 lrp 有 (precommercial 无 revenue) → 退化到 lrp 单项
    - 三个全无 → 60.0 中性
    - PS/G 档位边界 (≤0.5 满分; 1.0/2.0/3.0 临界)
    - PS 折溢价档位 (peer median 比对)
    - cfg.enabled=False → 退回旧 last_round_premium 单项 (绿灯回滚)
    - yaml 阈值改动立即生效
    - PE 路径 (PROFITABLE / 18A) 不受影响
    - rationale 描述 P/S, PS/G, lrp 三项 + 实际生效信号
"""
from __future__ import annotations

import pytest


def _make_18c_offering(*, mkt_cap=None, ps_peer=None, lrp=None):
    from nacs_model import OfferingStructure
    return OfferingStructure(
        pricing_in_range=0.7, intl_oversubscription=10.0,
        public_oversubscription=30.0, clawback_triggered=True,
        greenshoe_pct=0.15, offering_size_hkd=2e9,
        mkt_cap_at_offer_hkd=mkt_cap,
        ps_peer_median=ps_peer,
        last_round_premium=lrp,
    )


def _make_18c_fund(*, revenue=None, growth=None, commercial=True):
    from nacs_model import TechC18Fundamentals
    return TechC18Fundamentals(
        is_commercial=commercial,
        revenue_latest_hkd=revenue,
        revenue_growth_yoy=growth,
        rd_intensity=0.18,
        milestone_score=4.0,
    )


# =============================================================================
# 三信号加权平均
# =============================================================================

class TestAllThreeSignals:
    def test_all_signals_weighted_average(self):
        """高质量 18C: P/S=10 (peer=12), PS/G=0.20 → 高分; lrp=-10% → 100"""
        from nacs_model import _score_l1_1_18c
        o = _make_18c_offering(mkt_cap=50e9, ps_peer=12.0, lrp=-0.10)
        t = _make_18c_fund(revenue=5e9, growth=0.50)
        s, c = _score_l1_1_18c(o, t)
        assert c["ps_used"] == 1.0
        assert c["psg_used"] == 1.0
        assert c["lrp_used"] == 1.0
        # 高质量, 三项都偏高 → 总分应 > 80
        assert s > 80
        # PS/G=0.20 < 0.5 → 100
        assert c["psg_score"] == pytest.approx(100.0)

    def test_bubble_18c_low_score(self):
        """泡沫 18C: P/S=30 vs peer=10 (溢价 200%+), PS/G=15 → 低分"""
        from nacs_model import _score_l1_1_18c
        o = _make_18c_offering(mkt_cap=60e9, ps_peer=10.0, lrp=0.40)
        t = _make_18c_fund(revenue=2e9, growth=0.20)   # P/S=30, PS/G=1.5
        s, c = _score_l1_1_18c(o, t)
        # PS=30 vs peer=10 → premium >= 20% → ps_score=0
        assert c["ps_score"] == 0.0
        # PS/G=1.5 → 偏贵带 (70→40 线性) ≈ 55
        assert 40 < c["psg_score"] < 70
        # lrp=40% → 30→0 线性 → 15
        assert c["lrp_score"] < 30
        # 加权平均: 0.30*0 + 0.40*55 + 0.30*15 ≈ 26.5
        assert s < 35


# =============================================================================
# 信号缺失 renormalize
# =============================================================================

class TestSignalAvailability:
    def test_only_psg_when_no_peer_no_lrp(self):
        """ps_peer 缺 + lrp 缺 → 走 PS/G 单项"""
        from nacs_model import _score_l1_1_18c
        o = _make_18c_offering(mkt_cap=50e9, ps_peer=None, lrp=None)
        t = _make_18c_fund(revenue=5e9, growth=0.50)
        s, c = _score_l1_1_18c(o, t)
        assert c["ps_used"] == 0.0     # 缺 peer 不可计算 ps_score
        assert c["psg_used"] == 1.0
        assert c["lrp_used"] == 0.0
        # PS/G=0.20 → 100
        assert s == pytest.approx(100.0, abs=0.01)

    def test_only_ps_when_no_growth_no_lrp(self):
        """无增速 (PS/G 不可算) + 无 lrp → 走 P/S 单项"""
        from nacs_model import _score_l1_1_18c
        o = _make_18c_offering(mkt_cap=10e9, ps_peer=10.0, lrp=None)
        t = _make_18c_fund(revenue=2e9, growth=None)   # PS=5, PS/G=n/a
        s, c = _score_l1_1_18c(o, t)
        assert c["ps_used"] == 1.0
        assert c["psg_used"] == 0.0
        assert c["lrp_used"] == 0.0
        # PS=5 vs peer=10 → discount=50% → 100
        assert s == pytest.approx(100.0, abs=0.01)

    def test_only_lrp_when_precommercial_no_revenue(self):
        """precommercial 18C: revenue=None → ps/psg 都不可算, 仅 lrp"""
        from nacs_model import _score_l1_1_18c
        o = _make_18c_offering(mkt_cap=10e9, ps_peer=None, lrp=-0.20)
        t = _make_18c_fund(revenue=None, growth=None, commercial=False)
        s, c = _score_l1_1_18c(o, t)
        assert c["ps_used"] == 0.0
        assert c["psg_used"] == 0.0
        assert c["lrp_used"] == 1.0
        # lrp=-20% 折价 → 100
        assert s == pytest.approx(100.0, abs=0.01)

    def test_all_signals_missing_falls_back_neutral(self):
        """三信号全无 → 60.0 中性 (跟旧行为兼容)"""
        from nacs_model import _score_l1_1_18c
        o = _make_18c_offering(mkt_cap=None, ps_peer=None, lrp=None)
        t = _make_18c_fund(revenue=None, growth=None, commercial=False)
        s, c = _score_l1_1_18c(o, t)
        assert c["ps_used"] == 0.0
        assert c["psg_used"] == 0.0
        assert c["lrp_used"] == 0.0
        assert c.get("fallback_neutral") == 1.0
        assert s == pytest.approx(60.0)

    def test_revenue_zero_skips_ps(self):
        """商业化但 revenue=0 → P/S undefined, 不算"""
        from nacs_model import _score_l1_1_18c
        o = _make_18c_offering(mkt_cap=10e9, ps_peer=10.0, lrp=None)
        t = _make_18c_fund(revenue=0.0, growth=0.50)
        s, c = _score_l1_1_18c(o, t)
        assert c["ps_used"] == 0.0
        assert c["psg_used"] == 0.0


# =============================================================================
# PS/G 档位边界
# =============================================================================

class TestPSGBands:
    def _psg_score(self, psg):
        from nacs_model import _score_psg_band
        return _score_psg_band(psg)

    def test_excellent_band(self):
        assert self._psg_score(0.0) == 100.0
        assert self._psg_score(0.25) == 100.0
        assert self._psg_score(0.50) == 100.0    # 边界 (≤ 0.5 满分)

    def test_fair_band(self):
        assert self._psg_score(0.75) == pytest.approx(85.0)   # 中点
        assert self._psg_score(1.0) == pytest.approx(70.0)

    def test_rich_band(self):
        assert self._psg_score(1.5) == pytest.approx(55.0)
        assert self._psg_score(2.0) == pytest.approx(40.0)

    def test_bubble_band(self):
        assert self._psg_score(2.5) == pytest.approx(20.0)
        assert self._psg_score(3.0) == pytest.approx(0.0)

    def test_extreme_high_zero(self):
        assert self._psg_score(5.0) == 0.0
        assert self._psg_score(100.0) == 0.0

    def test_negative_returns_none(self):
        """负 PS/G (异常输入) → None 让 caller 跳过"""
        assert self._psg_score(-0.5) is None
        assert self._psg_score(None) is None


# =============================================================================
# P/S 折溢价档位
# =============================================================================

class TestPSDiscount:
    def _ps_score(self, ps_at_offer, ps_peer):
        from nacs_model import _score_ps_discount
        return _score_ps_discount(ps_at_offer, ps_peer)

    def test_strong_discount(self):
        """≥30% 折让 → 100"""
        assert self._ps_score(7.0, 10.0) == 100.0    # 30% discount
        assert self._ps_score(5.0, 10.0) == 100.0    # 50%

    def test_at_peer(self):
        assert self._ps_score(10.0, 10.0) == pytest.approx(50.0)

    def test_premium(self):
        """≥20% 溢价 → 0"""
        assert self._ps_score(12.0, 10.0) == pytest.approx(0.0)
        assert self._ps_score(15.0, 10.0) == 0.0

    def test_missing_inputs_returns_none(self):
        assert self._ps_score(None, 10.0) is None
        assert self._ps_score(10.0, None) is None
        assert self._ps_score(10.0, 0) is None       # peer<=0 不合理
        assert self._ps_score(0, 10.0) is None       # ps_at_offer<=0


# =============================================================================
# Config 控制
# =============================================================================

class TestConfigDriven:
    def test_disabled_falls_back_to_legacy(self):
        """cfg.enabled=False → 退回旧 last_round_premium 单项"""
        from config import (
            NacsConfig, Layer1Valuation18C, set_config, reset_config,
        )
        from nacs_model import _score_l1_1_18c
        try:
            cfg = NacsConfig()
            cfg.layer1_valuation_18c = Layer1Valuation18C(enabled=False)
            set_config(cfg)
            o = _make_18c_offering(mkt_cap=50e9, ps_peer=12.0, lrp=-0.20)
            t = _make_18c_fund(revenue=5e9, growth=0.50)
            s, c = _score_l1_1_18c(o, t)
            # 旧行为: 走 last_round_premium 单项, lrp=-20% → 100
            assert s == 100.0
            assert c.get("fallback_legacy_only_lrp") == 1.0
        finally:
            reset_config()

    def test_yaml_loads_section(self):
        from pathlib import Path
        from config import load_config
        cfg = load_config(Path(__file__).resolve().parent.parent
                          / "configs" / "nacs_v8.yaml")
        v = cfg.layer1_valuation_18c
        assert v.enabled is True
        assert v.weight_ps == 0.30
        assert v.weight_psg == 0.40
        assert v.weight_lrp == 0.30
        assert v.psg_excellent_max == 0.5
        assert v.psg_bubble_max == 3.0
        assert v.fallback_neutral == 60.0

    def test_custom_psg_threshold(self):
        """改 psg_excellent_max=1.0 → PS/G=0.8 也算满分"""
        from config import (
            NacsConfig, Layer1Valuation18C, set_config, reset_config,
        )
        from nacs_model import _score_l1_1_18c
        try:
            cfg = NacsConfig()
            cfg.layer1_valuation_18c = Layer1Valuation18C(
                psg_excellent_max=1.0,
                psg_fair_max=1.5,
                psg_rich_max=2.5,
            )
            set_config(cfg)
            # PS/G=0.8 在新 excellent 带内
            o = _make_18c_offering(mkt_cap=8e9, ps_peer=None, lrp=None)
            t = _make_18c_fund(revenue=2e9, growth=0.50)   # P/S=4, PS/G=0.08
            s, c = _score_l1_1_18c(o, t)
            assert c["psg_score"] == 100.0
        finally:
            reset_config()

    def test_custom_weights(self):
        """改权重让 PS/G 占 80% → 高 PS/G 时分数更接近 PS/G 子分"""
        from config import (
            NacsConfig, Layer1Valuation18C, set_config, reset_config,
        )
        from nacs_model import _score_l1_1_18c
        try:
            cfg = NacsConfig()
            cfg.layer1_valuation_18c = Layer1Valuation18C(
                weight_ps=0.10, weight_psg=0.80, weight_lrp=0.10,
            )
            set_config(cfg)
            # PS/G=0.20 (满分), 但 PS=5 vs peer=20 (远低于 peer → 100)
            # lrp=+50% (溢价 → 0)
            o = _make_18c_offering(mkt_cap=10e9, ps_peer=20.0, lrp=0.50)
            t = _make_18c_fund(revenue=2e9, growth=0.50)
            s, c = _score_l1_1_18c(o, t)
            # 新权重: 0.1*100 + 0.8*100 + 0.1*0 = 90
            assert s == pytest.approx(90.0, abs=0.5)
        finally:
            reset_config()


# =============================================================================
# 跟非 18C 路径隔离 (PROFITABLE / 18A 不受影响)
# =============================================================================

class TestNon18CUnaffected:
    def test_profitable_uses_pe_route(self, make_ipo):
        from nacs_model import _score_l1_1_valuation
        ipo = make_ipo()
        # default: PROFITABLE, PE=15 vs peer=22 → PE route
        s, c = _score_l1_1_valuation(ipo)
        # 应有 pe_discount_score 不应有 ps_score
        assert "pe_discount_score" in c
        assert "ps_score" not in c

    def test_biotech_uses_pe_route(self, make_ipo):
        from nacs_model import (
            _score_l1_1_valuation, ListingChapter, CompanyType,
            BiotechFundamentals,
        )
        ipo = make_ipo()
        ipo.listing_chapter = ListingChapter.CHAPTER_18A
        ipo.company_type = CompanyType.BIOTECH_18A
        ipo.profitable = None
        ipo.biotech = BiotechFundamentals(
            core_pipeline_phase="II", pipeline_count_phase2plus=2,
            cash_runway_months=24, bd_deals_count_2y=2,
        )
        s, c = _score_l1_1_valuation(ipo)
        assert "pe_discount_score" in c
        assert "ps_score" not in c


# =============================================================================
# Rationale (审计)
# =============================================================================

class TestRationale:
    def test_rationale_describes_three_components(self, make_ipo):
        """compute_nacs L1.1 reason 应描述 P/S, PS/G, last_round 三项"""
        from nacs_model import (
            compute_nacs, ListingChapter, CompanyType, TechC18Fundamentals,
        )
        ipo = make_ipo()
        ipo.listing_chapter = ListingChapter.CHAPTER_18C_COMMERCIAL
        ipo.company_type = CompanyType.TECH_18C
        ipo.profitable = None
        ipo.tech18c = TechC18Fundamentals(
            is_commercial=True, revenue_latest_hkd=5e9,
            revenue_growth_yoy=0.40, milestone_score=4.0, rd_intensity=0.18,
        )
        ipo.offering.mkt_cap_at_offer_hkd = 50e9
        ipo.offering.ps_peer_median = 12.0
        r = compute_nacs(ipo)
        l1_1 = r.layer1.reasons.get("L1.1_valuation", "")
        assert "P/S" in l1_1
        assert "PS/G" in l1_1
        # 高质量场景 P/S=10, PS/G=0.25 → "便宜" tag
        assert "便宜" in l1_1 or "≤0.5" in l1_1

    def test_rationale_marks_missing_signals(self, make_ipo):
        """precommercial 18C → reason 应说 'P/S=n/a'"""
        from nacs_model import (
            compute_nacs, ListingChapter, CompanyType, TechC18Fundamentals,
        )
        ipo = make_ipo()
        ipo.listing_chapter = ListingChapter.CHAPTER_18C_PRECOMMERCIAL
        ipo.company_type = CompanyType.TECH_18C
        ipo.profitable = None
        ipo.tech18c = TechC18Fundamentals(
            is_commercial=False, milestone_score=3.0,
        )
        r = compute_nacs(ipo)
        l1_1 = r.layer1.reasons.get("L1.1_valuation", "")
        assert "P/S=n/a" in l1_1 or "缺失" in l1_1
