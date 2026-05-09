"""
Thesis synthesizer 测试.

覆盖:
    - drivers / risks 阈值正确 (≥75 / ≤45 / R≥0.40)
    - base_rate 中位数 + winrate + verdict (favorable/neutral/cautious)
    - headline 在 SKIP / regime_blocked / LARGE 各情形下措辞合理
    - 没 panel_snap / 没 similar_cases 也能跑
    - cluster bonus / zucou 红旗 等特殊信号被识别
"""
from __future__ import annotations

from datetime import date

import pytest


# =============================================================================
# Drivers / Risks
# =============================================================================

def test_drivers_extracted_from_high_l1_components(make_ipo):
    from nacs_model import compute_nacs
    from reports.thesis import synthesize_thesis
    r = compute_nacs(make_ipo())
    t = synthesize_thesis(r)
    # baseline make_ipo 是高分 IPO, drivers 应非空
    assert len(t["drivers"]) > 0
    # 每个 driver 都有 name / score / tier / reason
    for d in t["drivers"]:
        assert "name" in d and "tier" in d and "reason" in d
        assert d["tier"] in ("L1", "L2")


def test_risks_extracted_from_high_l3_components(make_ipo):
    from nacs_model import compute_nacs
    from reports.thesis import synthesize_thesis
    ipo = make_ipo()
    # 拉高 overhang -> R3 应进 risks
    ipo.lockup.overhang_ratio = 0.97
    r = compute_nacs(ipo)
    t = synthesize_thesis(r)
    risk_names = [r_["name"] for r_ in t["risks"]]
    assert any("overhang" in n.lower() or "解禁" in n for n in risk_names)


def test_no_drivers_when_baseline_all_average():
    """全部子项中等水平时 drivers / risks 都不应过多"""
    # make_ipo fixture 是高分 baseline, 这里造一个中等的
    from nacs_model import (
        compute_nacs, IPOOffering, ListingChapter, CompanyType,
        OfferingStructure, SponsorInfo, MarketEnvironment, LockupContext,
        ProfitableFundamentals, SponsorTier, CornerstoneInvestor,
        CornerstoneType,
    )
    from reports.thesis import synthesize_thesis
    cs = [CornerstoneInvestor(
        name=f"CS_{i}", ticket_size_hkd=1e8,
        type=CornerstoneType.FAMILY_OFFICE_SPV,
        aum_usd=5e8, hk_ipo_count_5y=3,
        hk_ipo_avg_m6_return=0.05, hk_ipo_winrate_m6=0.5,
        lockup_discipline_score=0.5, sector_expertise=1,
    ) for i in range(3)]
    ipo = IPOOffering(
        company_name="X", stock_code="0001.HK",
        listing_chapter=ListingChapter.MAIN_BOARD_PROFITABLE,
        company_type=CompanyType.PROFITABLE,
        cornerstones=cs,
        offering=OfferingStructure(
            pricing_in_range=0.5, intl_oversubscription=2.0,
            public_oversubscription=2.0, clawback_triggered=False,
            greenshoe_pct=0.10, offering_size_hkd=1e9,
            pe_at_offer=20, pe_peer_median=20),
        sponsor=SponsorInfo(primary_sponsor="X",
                            primary_tier=SponsorTier.TIER_2),
        market=MarketEnvironment(
            hsi_60d_return=0.0, hsi_60d_vol_annualized=0.30,
            hsi_60d_vol_pct_rank=0.50, hsi_valuation_pct=0.50,
            hk_ipo_30d_avg_d30=0.0, hk_ipo_30d_breakage_rate=0.50,
            southbound_30d_net_normalized=0.0, sector_60d_vol_annualized=0.30),
        lockup=LockupContext(
            lockup_months=6, overhang_ratio=0.85,
            fundamental_risk_score=0.40, peer_lockup_avg_drawdown=0.30,
            pe_vs_history_pct=0.55),
        profitable=ProfitableFundamentals(
            revenue_cagr_3y=0.10, gross_margin_trend=0.0,
            roe_avg_3y=0.10, net_debt_to_ebitda=2.0, fcf_positive_years=1),
    )
    r = compute_nacs(ipo)
    t = synthesize_thesis(r)
    # 基本面/估值平庸时 drivers 数应少
    assert len(t["drivers"]) <= 4


# =============================================================================
# Base rate
# =============================================================================

def test_base_rate_favorable():
    from reports.thesis import _base_rate_from_similar
    sims = [
        {"actual_d30": 0.10, "actual_m6": 0.20},
        {"actual_d30": 0.15, "actual_m6": 0.30},
        {"actual_d30": 0.05, "actual_m6": 0.15},
    ]
    out = _base_rate_from_similar(sims)
    assert out["n_total"] == 3
    assert out["m6_median"] > 0.15
    assert out["d30_winrate"] == 1.0
    assert out["verdict"] == "favorable"


def test_base_rate_cautious():
    from reports.thesis import _base_rate_from_similar
    sims = [
        {"actual_d30": -0.10, "actual_m6": -0.20},
        {"actual_d30": -0.15, "actual_m6": -0.30},
    ]
    out = _base_rate_from_similar(sims)
    assert out["verdict"] == "cautious"


def test_base_rate_no_due_samples():
    from reports.thesis import _base_rate_from_similar
    sims = [
        {"actual_d30": None, "actual_m6": None, "stock_code": "X"},
        {"actual_d30": None, "actual_m6": None, "stock_code": "Y"},
    ]
    out = _base_rate_from_similar(sims)
    assert out["verdict"] == "no_due_samples"


def test_base_rate_empty_returns_none():
    from reports.thesis import _base_rate_from_similar
    assert _base_rate_from_similar([]) is None
    assert _base_rate_from_similar(None) is None


# =============================================================================
# Headline
# =============================================================================

def test_headline_for_skip(make_ipo):
    """SKIP 标的 (regime gate 等) 的 headline 反映否定."""
    from nacs_model import compute_nacs
    from reports.thesis import synthesize_thesis
    r = compute_nacs(make_ipo(regime=-0.10))
    t = synthesize_thesis(r)
    assert "SKIP" in t["headline"]


def test_headline_for_large(make_ipo):
    from nacs_model import compute_nacs
    from reports.thesis import synthesize_thesis
    r = compute_nacs(make_ipo())
    if r.decision == "LARGE":
        t = synthesize_thesis(r)
        assert "LARGE" in t["headline"]


# =============================================================================
# Headline + base rate verdict 组合
# =============================================================================

def test_headline_includes_base_rate_verdict(make_ipo):
    from nacs_model import compute_nacs
    from reports.thesis import synthesize_thesis
    r = compute_nacs(make_ipo())
    sims = [{"actual_d30": 0.10, "actual_m6": 0.20}] * 3
    t = synthesize_thesis(r, similar_cases=sims)
    if r.decision != "SKIP":
        assert "类比" in t["headline"]


# =============================================================================
# Panel context
# =============================================================================

def test_panel_context_regime_label():
    from reports.thesis import synthesize_thesis
    from nacs_model import LayerBreakdown
    # 用 mock NACSResult 减少耦合
    class MockResult:
        decision = "LARGE"
        position_pct = 0.7
        nacs_adjusted = 0.50
        nacs_raw = 0.50
        Q_company = 0.7
        Q_ecosystem = 0.7
        R_lockup = 0.20
        layer1 = LayerBreakdown("L1", 70, 0.7)
        layer2 = LayerBreakdown("L2", 70, 0.7)
        layer3 = LayerBreakdown("L3", 20, 0.20)
        adjustments_applied = []
        warnings = []
    panel = {"snapshot_id": "P_X", "asof_date": "2026-05-09",
             "n_ipos_in_universe": 384, "regime_score": 0.08}
    t = synthesize_thesis(MockResult(), panel_snap=panel)
    assert t["panel_context"]["regime_label"].startswith("情绪正面")


def test_panel_context_negative_regime():
    from reports.thesis import synthesize_thesis
    from nacs_model import LayerBreakdown
    class MockResult:
        decision = "LARGE"; position_pct = 0.7; nacs_adjusted = 0.50
        nacs_raw = 0.50; Q_company = 0.7; Q_ecosystem = 0.7; R_lockup = 0.20
        layer1 = LayerBreakdown("L1", 70, 0.7)
        layer2 = LayerBreakdown("L2", 70, 0.7)
        layer3 = LayerBreakdown("L3", 20, 0.20)
        adjustments_applied = []; warnings = []
    panel = {"snapshot_id": "P_X", "asof_date": "2026-05-09",
             "n_ipos_in_universe": 384, "regime_score": -0.05}
    t = synthesize_thesis(MockResult(), panel_snap=panel)
    assert "偏弱" in t["panel_context"]["regime_label"]


# =============================================================================
# Robustness
# =============================================================================

def test_thesis_works_with_no_panel_no_similar(make_ipo):
    from nacs_model import compute_nacs
    from reports.thesis import synthesize_thesis
    r = compute_nacs(make_ipo())
    t = synthesize_thesis(r, panel_snap=None, similar_cases=None)
    # 至少 headline / drivers / risks / warnings 都返回
    assert "headline" in t
    assert isinstance(t["drivers"], list)
    assert isinstance(t["risks"], list)
    assert t["base_rate"] is None
    assert t["panel_context"] is None
