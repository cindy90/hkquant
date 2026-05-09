"""
P1.2 — L1.3 已盈利档 盈利质量 tier multiplier 测试.

新模块: cfg.layer1_profitability_tier
被打的字段: ProfitableFundamentals.roe_avg_3y + fcf_positive_years

覆盖:
    - persistent: ROE_3y ≥ 15% AND FCF ≥ 3y → ×1.10
    - fresh:      FCF ≤ 1y                 → ×0.90
    - moderate:   两档都不命中             → ×1.00
    - 边界: roe=15% 整 + fcf=3 → persistent (用 >= 包含)
    - 边界: fcf=2 + 高 ROE     → moderate (没满 fcf 门槛)
    - roe_avg_3y=None 不算 persistent (保守)
    - cfg.enabled=False        → 全部 ×1.00
    - yaml 自定义阈值/multiplier → 立即生效
    - L1.3 score 仍 clip [0,100] (mult 后超过 100 截断)
    - 仅作用于 PROFITABLE 档; biotech / 18C 不受影响
    - rationale 在 mult ≠ 1.0 时附 'persistent'/'fresh' 标签
    - score_layer1_company 把 _profit_tier / _profit_tier_multiplier 透传到 components
"""
from __future__ import annotations

import pytest


def _make_profitable(**overrides):
    from nacs_model import ProfitableFundamentals
    base = dict(
        revenue_cagr_3y=0.20, gross_margin_trend=0.02,
        roe_avg_3y=0.10, net_debt_to_ebitda=1.0, fcf_positive_years=2,
    )
    base.update(overrides)
    return ProfitableFundamentals(**base)


# =============================================================================
# 直接打分 _score_l1_3_profitable
# =============================================================================

class TestL13ProfitabilityTier:
    def test_persistent_tier_high_roe_fcf3(self):
        from nacs_model import _score_l1_3_profitable
        f = _make_profitable(roe_avg_3y=0.20, fcf_positive_years=3)
        s, c = _score_l1_3_profitable(f)
        assert c["profit_tier"] == "persistent"
        assert c["profit_tier_multiplier"] == pytest.approx(1.10)

    def test_fresh_tier_fcf_1(self):
        from nacs_model import _score_l1_3_profitable
        f = _make_profitable(roe_avg_3y=0.18, fcf_positive_years=1)
        s, c = _score_l1_3_profitable(f)
        assert c["profit_tier"] == "fresh"
        assert c["profit_tier_multiplier"] == pytest.approx(0.90)

    def test_fresh_tier_fcf_0(self):
        """fcf=0 (default) 也算 fresh, 不是 moderate"""
        from nacs_model import _score_l1_3_profitable
        f = _make_profitable(roe_avg_3y=0.10, fcf_positive_years=0)
        _, c = _score_l1_3_profitable(f)
        assert c["profit_tier"] == "fresh"
        assert c["profit_tier_multiplier"] == pytest.approx(0.90)

    def test_moderate_tier_mid_fcf(self):
        """fcf=2, 既不是 persistent (没满 3) 也不是 fresh (>1) → moderate"""
        from nacs_model import _score_l1_3_profitable
        f = _make_profitable(roe_avg_3y=0.20, fcf_positive_years=2)
        _, c = _score_l1_3_profitable(f)
        assert c["profit_tier"] == "moderate"
        assert c["profit_tier_multiplier"] == 1.0

    def test_moderate_low_roe_high_fcf(self):
        """ROE 太低, 即使 FCF 满 3 年也不是 persistent"""
        from nacs_model import _score_l1_3_profitable
        f = _make_profitable(roe_avg_3y=0.10, fcf_positive_years=3)
        _, c = _score_l1_3_profitable(f)
        assert c["profit_tier"] == "moderate"
        assert c["profit_tier_multiplier"] == 1.0

    def test_persistent_boundary_at_threshold(self):
        """ROE=15% 整 AND fcf=3 → persistent (>= 包含)"""
        from nacs_model import _score_l1_3_profitable
        f = _make_profitable(roe_avg_3y=0.15, fcf_positive_years=3)
        _, c = _score_l1_3_profitable(f)
        assert c["profit_tier"] == "persistent"

    def test_persistent_below_threshold(self):
        """ROE=14.99% AND fcf=3 → moderate (差一点点)"""
        from nacs_model import _score_l1_3_profitable
        f = _make_profitable(roe_avg_3y=0.1499, fcf_positive_years=3)
        _, c = _score_l1_3_profitable(f)
        assert c["profit_tier"] == "moderate"

    def test_roe_none_not_persistent(self):
        """roe_avg_3y=None 不应触发 persistent (保守: 不知道就按低值)"""
        from nacs_model import _score_l1_3_profitable
        f = _make_profitable(roe_avg_3y=None, fcf_positive_years=3)
        _, c = _score_l1_3_profitable(f)
        assert c["profit_tier"] != "persistent"
        # roe=None → 0 < threshold, fcf=3 不算 fresh → moderate
        assert c["profit_tier"] == "moderate"

    def test_score_clipped_to_100_after_multiplier(self):
        """五项加和接近 100 + ×1.10 → 应 clip 到 100"""
        from nacs_model import _score_l1_3_profitable
        f = _make_profitable(
            revenue_cagr_3y=0.50,
            gross_margin_trend=0.10,
            roe_avg_3y=0.40,
            net_debt_to_ebitda=0.0,
            fcf_positive_years=3,
        )
        s, c = _score_l1_3_profitable(f)
        assert c["profit_tier"] == "persistent"
        assert s == pytest.approx(100.0, abs=0.01)

    def test_score_clipped_to_0(self):
        """糟糕基本面 + ×0.9 不应让 score 跌负"""
        from nacs_model import _score_l1_3_profitable
        f = _make_profitable(
            revenue_cagr_3y=-0.10,
            gross_margin_trend=-0.05,
            roe_avg_3y=-0.05,
            net_debt_to_ebitda=10.0,
            fcf_positive_years=0,
        )
        s, _ = _score_l1_3_profitable(f)
        assert s >= 0


# =============================================================================
# 仅作用 PROFITABLE 档 (biotech / 18C 不受影响)
# =============================================================================

class TestSubtypeIsolation:
    def test_biotech_not_affected(self, sample_cornerstones):
        """18A 走 _score_l1_3_biotech, profit_tier 不应出现在 components"""
        from nacs_model import (
            IPOOffering, ListingChapter, CompanyType,
            OfferingStructure, SponsorInfo, MarketEnvironment, LockupContext,
            BiotechFundamentals, score_layer1_company, SponsorTier,
        )
        ipo = IPOOffering(
            company_name="BiotechCo", stock_code="0002.HK",
            listing_chapter=ListingChapter.CHAPTER_18A,
            company_type=CompanyType.BIOTECH_18A, cornerstones=sample_cornerstones,
            offering=OfferingStructure(
                pricing_in_range=0.7, intl_oversubscription=10.0,
                public_oversubscription=30.0, clawback_triggered=True,
                greenshoe_pct=0.15, offering_size_hkd=2e9,
                pe_at_offer=15, pe_peer_median=22, last_round_premium=-0.10,
            ),
            sponsor=SponsorInfo(primary_sponsor="UBS",
                                primary_tier=SponsorTier.TIER_1, joint_sponsor_count=1),
            market=MarketEnvironment(
                hsi_60d_return=0.03, hsi_60d_vol_annualized=0.20,
                hsi_60d_vol_pct_rank=0.5, hsi_valuation_pct=0.5,
                hk_ipo_30d_avg_d30=0.05, hk_ipo_30d_breakage_rate=0.50,
                southbound_30d_net_normalized=0.0, sector_60d_vol_annualized=0.30),
            lockup=LockupContext(lockup_months=6, overhang_ratio=1.0,
                                 fundamental_risk_score=0.30,
                                 peer_lockup_avg_drawdown=0.10,
                                 pe_vs_history_pct=0.50),
            biotech=BiotechFundamentals(
                core_pipeline_phase="II", pipeline_count_phase2plus=2,
                cash_runway_months=18, bd_deals_count_2y=1,
            ),
        )
        breakdown = score_layer1_company(ipo)
        # biotech 不该有 _profit_tier (因为 _score_l1_3_biotech 没填)
        assert "_profit_tier" not in breakdown.components

    def test_profitable_has_tier_in_components(self, make_ipo):
        from nacs_model import score_layer1_company
        ipo = make_ipo()
        # default fixture: roe=0.20, fcf=3 → persistent
        breakdown = score_layer1_company(ipo)
        assert breakdown.components.get("_profit_tier") == "persistent"
        assert breakdown.components.get("_profit_tier_multiplier") == pytest.approx(1.10)


# =============================================================================
# Config 控制
# =============================================================================

class TestConfigDriven:
    def test_disabled_skips_tier(self, make_ipo):
        from config import (
            NacsConfig, Layer1ProfitabilityTier, set_config, reset_config,
        )
        from nacs_model import score_layer1_company
        try:
            cfg = NacsConfig()
            cfg.layer1_profitability_tier = Layer1ProfitabilityTier(enabled=False)
            set_config(cfg)
            ipo = make_ipo()   # default 是 persistent case
            breakdown = score_layer1_company(ipo)
            # disabled → tier=moderate, mult=1.0
            assert breakdown.components.get("_profit_tier") == "moderate"
            assert breakdown.components.get("_profit_tier_multiplier") == 1.0
        finally:
            reset_config()

    def test_yaml_loads_profitability_tier(self):
        """nacs_v8.yaml 的 layer1_profitability_tier 字段应被解析"""
        from pathlib import Path
        from config import load_config
        cfg = load_config(Path(__file__).resolve().parent.parent
                          / "configs" / "nacs_v8.yaml")
        pt = cfg.layer1_profitability_tier
        assert pt.enabled is True
        assert pt.persistent_roe_threshold == 0.15
        assert pt.persistent_fcf_min_years == 3
        assert pt.persistent_multiplier == pytest.approx(1.10)
        assert pt.fresh_fcf_max_years == 1
        assert pt.fresh_multiplier == pytest.approx(0.90)

    def test_custom_threshold_changes_tier(self, make_ipo):
        """改 ROE 门槛到 25% → 默认 fixture (ROE=20%) 不再算 persistent"""
        from config import (
            NacsConfig, Layer1ProfitabilityTier, set_config, reset_config,
        )
        from nacs_model import score_layer1_company
        try:
            cfg = NacsConfig()
            cfg.layer1_profitability_tier = Layer1ProfitabilityTier(
                enabled=True,
                persistent_roe_threshold=0.25,   # 拉高
                persistent_fcf_min_years=3,
                persistent_multiplier=1.10,
                fresh_fcf_max_years=1,
                fresh_multiplier=0.90,
            )
            set_config(cfg)
            ipo = make_ipo()   # ROE=0.20 < 0.25
            breakdown = score_layer1_company(ipo)
            assert breakdown.components.get("_profit_tier") == "moderate"
        finally:
            reset_config()

    def test_custom_multipliers_apply(self, make_ipo):
        """改 multiplier 到 ×1.20/×0.80 → 应直接生效"""
        from config import (
            NacsConfig, Layer1ProfitabilityTier, set_config, reset_config,
        )
        from nacs_model import score_layer1_company
        try:
            cfg = NacsConfig()
            cfg.layer1_profitability_tier = Layer1ProfitabilityTier(
                persistent_multiplier=1.20,
                fresh_multiplier=0.80,
            )
            set_config(cfg)
            ipo = make_ipo()
            breakdown = score_layer1_company(ipo)
            assert breakdown.components["_profit_tier_multiplier"] == pytest.approx(1.20)
        finally:
            reset_config()


# =============================================================================
# Rationale (审计 footprint)
# =============================================================================

class TestRationaleAnnotation:
    def test_explain_l1_3_persistent_tag(self, make_ipo):
        from nacs_model import compute_nacs
        ipo = make_ipo()
        # default → persistent
        r = compute_nacs(ipo)
        l1_3_reason = r.layer1.reasons.get("L1.3_fundamentals", "")
        assert "持续盈利" in l1_3_reason
        assert "1.10" in l1_3_reason

    def test_explain_l1_3_fresh_tag(self, make_ipo):
        from nacs_model import compute_nacs
        ipo = make_ipo()
        ipo.profitable.fcf_positive_years = 0   # fresh
        r = compute_nacs(ipo)
        l1_3_reason = r.layer1.reasons.get("L1.3_fundamentals", "")
        assert "刚转盈" in l1_3_reason
        assert "0.90" in l1_3_reason

    def test_explain_l1_3_no_tag_when_moderate(self, make_ipo):
        from nacs_model import compute_nacs
        ipo = make_ipo()
        ipo.profitable.fcf_positive_years = 2   # moderate
        ipo.profitable.roe_avg_3y = 0.10
        r = compute_nacs(ipo)
        l1_3_reason = r.layer1.reasons.get("L1.3_fundamentals", "")
        # moderate 时不应出现 tier 标签 (mult=1.0 → 不渲染)
        assert "持续盈利" not in l1_3_reason
        assert "刚转盈" not in l1_3_reason
        assert "盈利质量 tier" not in l1_3_reason
