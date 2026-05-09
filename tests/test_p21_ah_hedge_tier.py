"""
P2.1 — A+H 套利乘子按 A 股 ADV 分档测试.

替换原 PostAdjustments.a_plus_h_short_borrowable 静态 ×1.10 的 ah_hedge tier.

被打的字段: IPOOffering.{is_a_h, a_share_short_borrowable, a_share_adv_cny}
新模块: cfg.post_adjustments.ah_hedge

覆盖:
    - high_liq (ADV >= 200M CNY)  → ×1.10
    - mid_liq  (50M <= ADV < 200M) → ×1.05
    - low_liq  (ADV < 50M)         → ×1.00 (奖励归零)
    - unknown_adv (None)           → ×fallback (1.10, 向后兼容)
    - is_a_h=False                 → 整段不触发
    - a_share_short_borrowable=False → 整段不触发
    - cfg.enabled=False            → 走旧静态值 m_ah (1.10)
    - 边界: ADV == high_threshold  → high_liq (用 >= 包含)
    - 边界: ADV == mid_threshold   → mid_liq
    - rationale (explain_adjustment) 区分 4 档给不同解释
    - yaml 自定义阈值/multiplier   → 立即生效
    - chains_with_other 跟 18C / 关联交易 等 multiplier 同时叠加
"""
from __future__ import annotations

import pytest


def _ah_ipo(make_ipo, *, adv=None, borrowable=True, is_ah=True):
    ipo = make_ipo()
    ipo.is_a_h = is_ah
    ipo.a_share_short_borrowable = borrowable
    ipo.a_share_adv_cny = adv
    return ipo


# =============================================================================
# 三档触发
# =============================================================================

class TestAHHedgeTriggers:
    def test_high_liquidity_full_premium(self, make_ipo):
        from nacs_model import compute_nacs
        ipo = _ah_ipo(make_ipo, adv=5e8)   # 500M CNY
        r = compute_nacs(ipo)
        adj = [a for a in r.adjustments_applied if "A+H" in a]
        assert len(adj) == 1
        assert "x1.1" in adj[0]
        assert "high_liq" in adj[0]

    def test_mid_liquidity_partial_premium(self, make_ipo):
        from nacs_model import compute_nacs
        ipo = _ah_ipo(make_ipo, adv=1e8)   # 100M CNY
        r = compute_nacs(ipo)
        adj = [a for a in r.adjustments_applied if "A+H" in a]
        assert "x1.05" in adj[0]
        assert "mid_liq" in adj[0]

    def test_low_liquidity_no_premium(self, make_ipo):
        """ADV < 50M CNY → ×1.00, 不给奖励"""
        from nacs_model import compute_nacs
        ipo = _ah_ipo(make_ipo, adv=3e7)   # 30M CNY
        r = compute_nacs(ipo)
        adj = [a for a in r.adjustments_applied if "A+H" in a]
        assert "x1.0" in adj[0]
        assert "low_liq" in adj[0]

    def test_unknown_adv_falls_back(self, make_ipo):
        """ADV=None → fallback ×1.10 (向后兼容旧静态行为)"""
        from nacs_model import compute_nacs
        ipo = _ah_ipo(make_ipo, adv=None)
        r = compute_nacs(ipo)
        adj = [a for a in r.adjustments_applied if "A+H" in a]
        assert "x1.1" in adj[0]
        assert "ADV 未知" in adj[0] or "fallback" in adj[0]


# =============================================================================
# 触发前置条件
# =============================================================================

class TestAHHedgeGuards:
    def test_no_trigger_when_not_ah(self, make_ipo):
        from nacs_model import compute_nacs
        ipo = _ah_ipo(make_ipo, adv=5e8, is_ah=False)
        r = compute_nacs(ipo)
        assert not any("A+H" in a for a in r.adjustments_applied)

    def test_no_trigger_when_not_borrowable(self, make_ipo):
        """A+H 但 A 股不可融券 → 整段跳过 (无对冲手段)"""
        from nacs_model import compute_nacs
        ipo = _ah_ipo(make_ipo, adv=5e8, borrowable=False)
        r = compute_nacs(ipo)
        assert not any("A+H" in a for a in r.adjustments_applied)


# =============================================================================
# 边界
# =============================================================================

class TestBoundary:
    def test_at_high_threshold_is_high(self, make_ipo):
        """ADV == 200M CNY (==high_threshold) → high_liq (>= 包含)"""
        from nacs_model import compute_nacs
        ipo = _ah_ipo(make_ipo, adv=2e8)
        r = compute_nacs(ipo)
        adj = [a for a in r.adjustments_applied if "A+H" in a]
        assert "high_liq" in adj[0]
        assert "x1.1" in adj[0]

    def test_at_mid_threshold_is_mid(self, make_ipo):
        """ADV == 50M CNY → mid_liq"""
        from nacs_model import compute_nacs
        ipo = _ah_ipo(make_ipo, adv=5e7)
        r = compute_nacs(ipo)
        adj = [a for a in r.adjustments_applied if "A+H" in a]
        assert "mid_liq" in adj[0]
        assert "x1.05" in adj[0]

    def test_just_below_mid_is_low(self, make_ipo):
        """ADV = 49.9M < 50M → low_liq"""
        from nacs_model import compute_nacs
        ipo = _ah_ipo(make_ipo, adv=4.99e7)
        r = compute_nacs(ipo)
        adj = [a for a in r.adjustments_applied if "A+H" in a]
        assert "low_liq" in adj[0]


# =============================================================================
# Multiplier 数值正确
# =============================================================================

class TestMultiplierMath:
    def test_high_increases_nacs_by_10pct(self, make_ipo):
        from nacs_model import compute_nacs
        # baseline (no A+H)
        ipo_base = make_ipo()
        nacs_base = compute_nacs(ipo_base).nacs_adjusted
        # high tier
        ipo_hi = _ah_ipo(make_ipo, adv=5e8)
        nacs_hi = compute_nacs(ipo_hi).nacs_adjusted
        assert nacs_hi == pytest.approx(nacs_base * 1.10, abs=0.001)

    def test_mid_increases_nacs_by_5pct(self, make_ipo):
        from nacs_model import compute_nacs
        ipo_base = make_ipo()
        ipo_mid = _ah_ipo(make_ipo, adv=1e8)
        nacs_base = compute_nacs(ipo_base).nacs_adjusted
        nacs_mid = compute_nacs(ipo_mid).nacs_adjusted
        assert nacs_mid == pytest.approx(nacs_base * 1.05, abs=0.001)

    def test_low_keeps_nacs_unchanged(self, make_ipo):
        from nacs_model import compute_nacs
        ipo_base = make_ipo()
        ipo_lo = _ah_ipo(make_ipo, adv=3e7)
        nacs_base = compute_nacs(ipo_base).nacs_adjusted
        nacs_lo = compute_nacs(ipo_lo).nacs_adjusted
        # ×1.00 → 等于 baseline
        assert nacs_lo == pytest.approx(nacs_base, abs=0.001)


# =============================================================================
# Config 控制
# =============================================================================

class TestConfigDriven:
    def test_disabled_falls_back_to_static(self, make_ipo):
        """cfg.ah_hedge.enabled=False → 走旧静态值 m_ah (1.10) 不分档"""
        from config import (
            NacsConfig, PostAdjustments, AHHedgeMultiplier,
            set_config, reset_config,
        )
        from nacs_model import compute_nacs
        try:
            cfg = NacsConfig()
            cfg.post_adjustments = PostAdjustments(
                ah_hedge=AHHedgeMultiplier(enabled=False),
            )
            set_config(cfg)
            ipo = _ah_ipo(make_ipo, adv=3e7)   # 本应是 low_liq, 但 disabled
            r = compute_nacs(ipo)
            adj = [a for a in r.adjustments_applied if "A+H" in a]
            assert "x1.1" in adj[0]
            # 不应有 tier label
            assert "low_liq" not in adj[0]
            assert "high_liq" not in adj[0]
        finally:
            reset_config()

    def test_yaml_loads_ah_hedge_section(self):
        """nacs_v8.yaml 的 ah_hedge 字段应被解析"""
        from pathlib import Path
        from config import load_config
        cfg = load_config(Path(__file__).resolve().parent.parent
                          / "configs" / "nacs_v8.yaml")
        ah = cfg.post_adjustments.ah_hedge
        assert ah.enabled is True
        assert ah.high_threshold_cny == 200_000_000
        assert ah.high_multiplier == 1.10
        assert ah.mid_threshold_cny == 50_000_000
        assert ah.mid_multiplier == 1.05
        assert ah.low_multiplier == 1.00
        assert ah.fallback_multiplier == 1.10

    def test_custom_thresholds(self, make_ipo):
        """改 high_threshold 到 1B → 500M ADV 不再算 high"""
        from config import (
            NacsConfig, PostAdjustments, AHHedgeMultiplier,
            set_config, reset_config,
        )
        from nacs_model import compute_nacs
        try:
            cfg = NacsConfig()
            cfg.post_adjustments = PostAdjustments(
                ah_hedge=AHHedgeMultiplier(high_threshold_cny=1e9),
            )
            set_config(cfg)
            ipo = _ah_ipo(make_ipo, adv=5e8)
            r = compute_nacs(ipo)
            adj = [a for a in r.adjustments_applied if "A+H" in a]
            # 在新阈值下变 mid
            assert "mid_liq" in adj[0]
        finally:
            reset_config()

    def test_custom_multipliers(self, make_ipo):
        """改 high_multiplier=1.20 → 高档 x1.20"""
        from config import (
            NacsConfig, PostAdjustments, AHHedgeMultiplier,
            set_config, reset_config,
        )
        from nacs_model import compute_nacs
        try:
            cfg = NacsConfig()
            cfg.post_adjustments = PostAdjustments(
                ah_hedge=AHHedgeMultiplier(high_multiplier=1.20),
            )
            set_config(cfg)
            ipo = _ah_ipo(make_ipo, adv=5e8)
            r = compute_nacs(ipo)
            adj = [a for a in r.adjustments_applied if "A+H" in a]
            assert "x1.2" in adj[0]
        finally:
            reset_config()


# =============================================================================
# Rationale (审计)
# =============================================================================

class TestRationaleAnnotation:
    def test_explain_high_liq(self):
        from nacs_rationale import explain_adjustment
        out = explain_adjustment(
            "A+H 同名A股可融券 x1.1 (A股 ADV 500M CNY, tier=high_liq)"
        )
        assert "高流动性" in out or "high" in out.lower()
        assert "对冲" in out

    def test_explain_mid_liq(self):
        from nacs_rationale import explain_adjustment
        out = explain_adjustment(
            "A+H 同名A股可融券 x1.05 (A股 ADV 100M CNY, tier=mid_liq)"
        )
        assert "中流动性" in out or "mid" in out.lower()

    def test_explain_low_liq(self):
        from nacs_rationale import explain_adjustment
        out = explain_adjustment(
            "A+H 同名A股可融券 x1.0 (A股 ADV 30M CNY, tier=low_liq)"
        )
        assert "低流动性" in out or "low" in out.lower()
        assert "不经济" in out or "归零" in out or "卖空" in out or "不给" in out

    def test_explain_fallback(self):
        from nacs_rationale import explain_adjustment
        out = explain_adjustment("A+H 同名A股可融券 x1.1 (ADV 未知, fallback)")
        assert "ADV" in out or "fallback" in out


# =============================================================================
# 链式叠加 (跟其他 post-adjustments)
# =============================================================================

class TestChaining:
    def test_chains_with_18c_discount(self, make_ipo):
        """18C 折扣 ×0.70 + A+H 高流动性 ×1.10 同时触发"""
        from nacs_model import (
            compute_nacs, ListingChapter, CompanyType, TechC18Fundamentals,
        )
        ipo = _ah_ipo(make_ipo, adv=5e8)
        ipo.listing_chapter = ListingChapter.CHAPTER_18C_COMMERCIAL
        ipo.company_type = CompanyType.TECH_18C
        ipo.profitable = None
        ipo.tech18c = TechC18Fundamentals(
            is_commercial=True, revenue_growth_yoy=0.4,
            milestone_score=4.0, rd_intensity=0.18,
        )
        r = compute_nacs(ipo)
        ah_adj = [a for a in r.adjustments_applied if "A+H" in a]
        c18_adj = [a for a in r.adjustments_applied if "18C" in a]
        assert len(ah_adj) == 1
        assert len(c18_adj) == 1
