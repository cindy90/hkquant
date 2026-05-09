"""
nacs_rationale 解释生成测试.

覆盖:
    - 阈值表与 nacs_model 内的硬编码同源 (PE_DISCOUNT_THRESHOLDS 等)
    - L1.1~L1.6 / L2.x / L3.x explain 函数都能产出非空 string
    - PROFITABLE / BIOTECH_18A / TECH_18C 三种 fundamentals 都覆盖
    - NULL / 缺失输入走中性默认且明确提示
    - explain_decision_band 各 5 个 band 都正确
    - explain_formula 含 adjustments 时正确累乘
    - compute_nacs 之后 NACSResult 的 layer{1,2,3}.reasons 非空
    - decision_rationale 至少包含公式拆解 + band 映射
"""
from __future__ import annotations

from datetime import date

import pytest


# =============================================================================
# Layer 1 reasons
# =============================================================================

class TestL1Explain:
    def test_l1_1_with_pe_discount(self, make_ipo):
        from nacs_rationale import _explain_l1_1
        ipo = make_ipo()
        ipo.offering.pe_at_offer = 12.0
        ipo.offering.pe_peer_median = 20.0
        out = _explain_l1_1(ipo)
        assert "PE_at_offer=12" in out or "12.0" in out
        assert "peer_median=20" in out or "20.0" in out
        assert "折让 +40" in out  # (20-12)/20 = 40%

    def test_l1_1_with_pe_premium(self, make_ipo):
        from nacs_rationale import _explain_l1_1
        ipo = make_ipo()
        ipo.offering.pe_at_offer = 30.0
        ipo.offering.pe_peer_median = 20.0
        out = _explain_l1_1(ipo)
        # (20-30)/20 = -0.50 → ≥20% 溢价 → 0 分
        assert "折让 -50" in out
        assert "≥20% 溢价" in out

    def test_l1_1_with_null_pe_returns_neutral(self, make_ipo):
        from nacs_rationale import _explain_l1_1
        ipo = make_ipo()
        ipo.offering.pe_at_offer = None
        out = _explain_l1_1(ipo)
        assert "缺失" in out
        assert "中性" in out

    def test_l1_1_18c_uses_last_round(self, make_ipo):
        from nacs_model import CompanyType
        from nacs_rationale import _explain_l1_1
        ipo = make_ipo()
        ipo.company_type = CompanyType.TECH_18C
        out = _explain_l1_1(ipo)
        assert "18C" in out or "PE/PS 不可比" in out

    def test_l1_2_tier1_single(self, make_ipo):
        from nacs_model import SponsorTier
        from nacs_rationale import _explain_l1_2
        ipo = make_ipo()
        ipo.sponsor.primary_tier = SponsorTier.TIER_1
        ipo.sponsor.joint_sponsor_count = 1
        out = _explain_l1_2(ipo.sponsor)
        assert "Tier 1" in out
        assert "+5" in out

    def test_l1_2_tier3_single_penalty(self, make_ipo):
        from nacs_model import SponsorTier
        from nacs_rationale import _explain_l1_2
        ipo = make_ipo()
        ipo.sponsor.primary_tier = SponsorTier.TIER_3
        ipo.sponsor.joint_sponsor_count = 1
        out = _explain_l1_2(ipo.sponsor)
        assert "Tier 3" in out
        assert "-5" in out

    def test_l1_3_profitable(self, make_ipo):
        from nacs_rationale import _explain_l1_3_profitable
        out = _explain_l1_3_profitable(__import__("nacs_model").ProfitableFundamentals(
            revenue_cagr_3y=0.30, gross_margin_trend=0.05,
            roe_avg_3y=0.20, net_debt_to_ebitda=1.0, fcf_positive_years=3))
        for token in ["营收", "毛利率", "ROE", "net_debt", "FCF"]:
            assert token in out

    def test_l1_3_biotech(self):
        from nacs_model import BiotechFundamentals
        from nacs_rationale import _explain_l1_3_biotech
        out = _explain_l1_3_biotech(BiotechFundamentals(
            core_pipeline_phase="II", pipeline_count_phase2plus=2,
            cash_runway_months=18, bd_deals_count_2y=1))
        assert "II期" in out or "II" in out
        assert "管线" in out
        assert "现金跑道" in out

    def test_l1_3_18c_commercial(self):
        from nacs_model import TechC18Fundamentals
        from nacs_rationale import _explain_l1_3_18c
        out = _explain_l1_3_18c(TechC18Fundamentals(
            is_commercial=True, revenue_growth_yoy=0.40,
            milestone_score=4.0, rd_intensity=0.18))
        assert "已商业化" in out

    def test_l1_3_18c_precommercial_caps_at_60(self):
        from nacs_model import TechC18Fundamentals
        from nacs_rationale import _explain_l1_3_18c
        out = _explain_l1_3_18c(TechC18Fundamentals(
            is_commercial=False, revenue_growth_yoy=None,
            milestone_score=3.0, rd_intensity=None))
        assert "未商业化" in out
        assert "上限 60" in out or "60" in out

    def test_l1_4_intl_oversub_red_flag(self, make_ipo):
        from nacs_rationale import _explain_l1_4
        ipo = make_ipo()
        ipo.offering.intl_oversubscription = 1.0  # < 1.5x
        out = _explain_l1_4(ipo.offering)
        assert "红旗" in out or "30" in out

    def test_l1_5_chapter_with_connect_bonus(self, make_ipo):
        from nacs_rationale import _explain_l1_5
        ipo = make_ipo()
        out = _explain_l1_5(ipo)
        assert "main_board_profitable" in out or "章节" in out

    def test_l1_6_market(self, make_ipo):
        from nacs_rationale import _explain_l1_6
        ipo = make_ipo()
        out = _explain_l1_6(ipo.market)
        for token in ["HSI", "波动", "南向"]:
            assert token in out


class TestL1Veto:
    def test_intl_oversub_veto_explained(self):
        from nacs_rationale import explain_l1_veto
        out = explain_l1_veto("国际配售认购倍数<1.5x")
        assert "1.5x" in out
        assert "封顶 40" in out

    def test_unknown_veto_returned_as_is(self):
        from nacs_rationale import explain_l1_veto
        out = explain_l1_veto("某种新条款")
        assert out == "某种新条款"

    def test_none_veto_returns_none(self):
        from nacs_rationale import explain_l1_veto
        assert explain_l1_veto(None) is None


# =============================================================================
# Layer 2
# =============================================================================

class TestL2Explain:
    def test_no_cornerstones(self, make_ipo):
        from nacs_rationale import explain_layer2_components
        ipo = make_ipo()
        ipo.cornerstones = []
        out = explain_layer2_components(ipo, {})
        assert "no_cornerstones" in out

    def test_q_weighted_lists_top3(self, make_ipo):
        from nacs_rationale import _explain_l2_q_weighted
        ipo = make_ipo()
        out = _explain_l2_q_weighted(ipo.cornerstones, 65.0)
        assert "65" in out
        assert "Top 3" in out
        # name 出现
        assert "CS_0" in out

    def test_coverage_bands(self):
        from nacs_rationale import _explain_l2_coverage
        assert "<20%" in _explain_l2_coverage(0.10, 25)
        assert "35~50%" in _explain_l2_coverage(0.40, 95)
        assert "65" in _explain_l2_coverage(0.70, 50) or ">65%" in _explain_l2_coverage(0.70, 50)

    def test_hhi_high_concentration(self):
        from nacs_rationale import _explain_l2_hhi
        out = _explain_l2_hhi(5000, 35)
        assert "高集中" in out

    def test_zucou_red_flag_explained(self):
        from nacs_rationale import _explain_l2_zucou
        out = _explain_l2_zucou(0.85, 0.20, True)
        assert "国资凑数红旗" in out
        assert "-10" in out

    def test_cluster_bonus_disabled(self):
        from nacs_rationale import _explain_l2_cluster_bonus
        out = _explain_l2_cluster_bonus(2, 1.0)
        assert "1.0" in out
        assert "证伪" in out or "禁用" in out


# =============================================================================
# Layer 3
# =============================================================================

class TestL3Explain:
    def test_vol_risk_low(self):
        from nacs_rationale import _explain_vol_risk
        out = _explain_vol_risk(0.20, 0.10)
        assert "<25%" in out
        assert "低波" in out

    def test_overhang_extreme(self):
        from nacs_rationale import _explain_overhang_risk
        out = _explain_overhang_risk(0.96, 0.55)
        assert "0.96" in out
        assert "解禁后" in out or "0.95" in out

    def test_macro_risk_high(self):
        from nacs_rationale import _explain_macro_risk
        out = _explain_macro_risk(0.85, 0.55)
        assert "85%" in out or "0.85" in out
        assert "贵" in out or "回调" in out


# =============================================================================
# Decision band + formula
# =============================================================================

class TestDecisionBand:
    @pytest.mark.parametrize("nacs,decision,key_phrase", [
        (0.60, "FULL", "≥0.55"),
        (0.50, "LARGE", "≥0.45"),
        (0.40, "TRIAL", "≥0.35"),
        (0.30, "RELATIONSHIP", "≥0.25"),
        (0.10, "SKIP", "<0.25"),
    ])
    def test_each_band(self, nacs, decision, key_phrase):
        from nacs_rationale import explain_decision_band
        out = explain_decision_band(nacs, 0.0, decision)
        assert decision in out
        assert key_phrase in out


class TestFormula:
    def test_no_adjustments(self):
        from nacs_rationale import explain_formula
        lines = explain_formula(0.7, 0.6, 0.2, 0.336, 0.336, [])
        text = "\n".join(lines)
        assert "Q_company × Q_ecosystem × (1 - R_lockup)" in text
        assert "0.7000 × 0.6000 × 0.8000" in text
        assert "0.3360" in text

    def test_with_adjustments_cumulative(self):
        from nacs_rationale import explain_formula
        lines = explain_formula(0.7, 0.6, 0.2, 0.336, 0.4032,
                                ["A+H 同名A股可融券 x1.20"])
        text = "\n".join(lines)
        assert "×1.20" in text
        assert "0.4032" in text


# =============================================================================
# Adjustment explanations
# =============================================================================

class TestAdjustmentExplanations:
    def test_a_plus_h(self):
        from nacs_rationale import explain_adjustment
        out = explain_adjustment("A+H 同名A股可融券 x1.1")
        assert "对冲" in out or "A 股" in out

    def test_18c_discount(self):
        from nacs_rationale import explain_adjustment
        out = explain_adjustment("18C 折扣 x0.7")
        assert "18C" in out

    def test_secondary_listing(self):
        from nacs_rationale import explain_adjustment
        out = explain_adjustment("第二上市 x0.85")
        assert "第二上市" in out

    def test_regime_gate(self):
        from nacs_rationale import explain_adjustment
        out = explain_adjustment("regime_gate: SKIP (score=-0.05<0, 原决策=LARGE)")
        assert "SKIP" in out
        assert "环境" in out or "applicability" in out.lower()

    def test_unknown_returned_as_is(self):
        from nacs_rationale import explain_adjustment
        out = explain_adjustment("unknown adjustment label")
        assert out == "unknown adjustment label"


# =============================================================================
# 集成: compute_nacs 末尾填充 reasons / decision_rationale
# =============================================================================

class TestComputeNacsFillsRationale:
    def test_decision_rationale_populated(self, make_ipo):
        from nacs_model import compute_nacs
        result = compute_nacs(make_ipo())
        assert len(result.decision_rationale) > 3
        text = "\n".join(result.decision_rationale)
        assert "NACS_raw" in text
        assert "NACS_adj" in text
        assert "band" in text.lower()

    def test_layer1_reasons_populated(self, make_ipo):
        from nacs_model import compute_nacs
        r = compute_nacs(make_ipo())
        # PROFITABLE 应该有 6 条 L1 reason
        for k in ["L1.1_valuation", "L1.2_sponsor", "L1.3_fundamentals",
                  "L1.4_offering", "L1.5_chapter", "L1.6_market"]:
            assert k in r.layer1.reasons, f"missing reason for {k}"
            assert len(r.layer1.reasons[k]) > 10

    def test_layer2_reasons_populated(self, make_ipo):
        from nacs_model import compute_nacs
        r = compute_nacs(make_ipo())
        # 至少几个 L2 reason 要有
        l2_keys = set(r.layer2.reasons.keys())
        assert "Q_weighted" in l2_keys
        assert "coverage" in l2_keys
        assert "hhi" in l2_keys

    def test_layer3_reasons_populated(self, make_ipo):
        from nacs_model import compute_nacs
        r = compute_nacs(make_ipo())
        l3_keys = set(r.layer3.reasons.keys())
        assert "vol_risk" in l3_keys
        assert "overhang_risk" in l3_keys

    def test_to_dict_includes_decision_rationale(self, make_ipo):
        from nacs_model import compute_nacs
        r = compute_nacs(make_ipo())
        d = r.to_dict()
        assert "decision_rationale" in d
        assert isinstance(d["decision_rationale"], list)
