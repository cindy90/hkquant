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
    # S3 新增: themes_provenance 应永远有值 (即使 themes_bundle=None)
    assert "themes_provenance" in t
    assert t["theme_heat"] is None
    assert t["premium_estimate"] is None


# =============================================================================
# S3 新增: theme_heat verdict 阈值
# =============================================================================

class TestHeatVerdict:
    @pytest.mark.parametrize("score,expected", [
        (None, "unknown"),
        (35, "trough"),
        (50, "moderate"),
        (70, "warm"),
        (80, "overheated"),
        (95, "overheated"),
    ])
    def test_heat_verdict_thresholds(self, score, expected):
        from reports.thesis import _heat_verdict
        assert _heat_verdict(score) == expected

    def test_overheated_emits_warning(self):
        from reports.thesis import _heat_warning_for_verdict
        w = _heat_warning_for_verdict("overheated", 85)
        assert "锁定期反转" in w

    def test_trough_emits_warning(self):
        from reports.thesis import _heat_warning_for_verdict
        w = _heat_warning_for_verdict("trough", 30)
        assert "谷底" in w

    def test_moderate_no_warning(self):
        from reports.thesis import _heat_warning_for_verdict
        assert _heat_warning_for_verdict("moderate", 55) is None


# =============================================================================
# S3 新增: theme_heat 构造
# =============================================================================

def _mock_themes_bundle(**overrides):
    """构造一个最小 themes_bundle 用于测试"""
    from reports.themes_data import Provenance
    base = {
        "heat_today": (
            {"as_of": "2026-05-08",
             "themes": {"ai_server": {
                 "label": "AI 服务器", "heat_score": 72,
                 "ret_5d": 0.03, "ret_20d": 0.09, "ret_60d": 0.05,
                 "pe_ttm_avg": 35.5, "reason": "动能强劲", "warning": None,
                 "source": "kimi"}}},
            Provenance(path="themes/heat_today.json", status="ok",
                       asof="2026-05-08")),
        "premium_curve": (
            {"fitted_at": "2026-05-08T19:00:00", "as_of_data": "2026-05-08",
             "n_samples_used": 31, "model": "log_linear",
             "params": {"a": 5.17, "b": 0.5, "c": -0.23},
             "r_squared": 0.39,
             "lookup_table": [
                 {"ai_pct": 0.0, "premium": -0.23},
                 {"ai_pct": 0.05, "premium": -0.10},
                 {"ai_pct": 0.10, "premium": 0.02},
                 {"ai_pct": 0.50, "premium": 0.85},
                 {"ai_pct": 1.00, "premium": 1.87},
             ]},
            Provenance(path="themes/premium_curve.json", status="ok",
                       asof="2026-05-08")),
        "theme_definitions": (
            {"_schema_version": "1.0",
             "themes": {"ai_server": {
                 "label": "AI 服务器",
                 "core_companies": [{"code": "00992.HK", "name": "联想"}],
                 "keywords": ["AI 服务器", "算力"]}}},
            Provenance(path="themes/theme_definitions.json", status="ok")),
        "ai_revenue_manual": (
            {"0992.HK": 0.30, "2533.HK": 1.00},
            Provenance(path="themes/ai_revenue_manual.json", status="ok")),
        "history": (
            {"ai_server": [("2026-05-06", 70), ("2026-05-07", 71),
                           ("2026-05-08", 72)]},
            Provenance(path="themes/history.csv", status="ok",
                       asof="2026-05-08")),
    }
    base.update(overrides)
    return base


class TestThemeHeatPanel:
    def test_theme_heat_built_when_classifier_hits(self, make_ipo):
        from nacs_model import compute_nacs
        from reports.thesis import synthesize_thesis
        r = compute_nacs(make_ipo())
        bundle = _mock_themes_bundle()
        t = synthesize_thesis(
            r, themes_bundle=bundle, stock_code="0992.HK",
            company_name="联想集团",
        )
        assert t["theme_heat"] is not None
        assert t["theme_heat"]["theme_id"] == "ai_server"
        assert t["theme_heat"]["heat_score"] == 72
        assert t["theme_heat"]["verdict"] == "warm"

    def test_theme_heat_none_when_no_match(self, make_ipo):
        from nacs_model import compute_nacs
        from reports.thesis import synthesize_thesis
        r = compute_nacs(make_ipo())
        bundle = _mock_themes_bundle()
        t = synthesize_thesis(
            r, themes_bundle=bundle, stock_code="9999.HK",
            company_name="无关公司", gics_l2="医疗保健",
        )
        assert t["theme_heat"] is None
        # 但 themes_provenance 仍记录"为什么没匹配"
        assert t["themes_provenance"]["theme_id"] is None
        assert t["themes_provenance"]["classification"]["confidence"] == "none"

    def test_trend_30d_truncated(self, make_ipo):
        from nacs_model import compute_nacs
        from reports.thesis import synthesize_thesis
        r = compute_nacs(make_ipo())
        bundle = _mock_themes_bundle()
        t = synthesize_thesis(
            r, themes_bundle=bundle, stock_code="0992.HK",
        )
        assert len(t["theme_heat"]["trend_30d"]) == 3   # mock 只有 3 天


# =============================================================================
# S3 新增: premium_estimate
# =============================================================================

class TestPremiumEstimate:
    def test_premium_estimate_uses_manual_lookup(self, make_ipo):
        from nacs_model import compute_nacs
        from reports.thesis import synthesize_thesis
        r = compute_nacs(make_ipo())
        bundle = _mock_themes_bundle()
        t = synthesize_thesis(
            r, themes_bundle=bundle, stock_code="2533.HK",
        )
        assert t["premium_estimate"] is not None
        assert t["premium_estimate"]["ai_revenue_pct"] == 1.0
        assert t["premium_estimate"]["expected_premium"] == pytest.approx(1.87, abs=0.05)
        assert "100% AI 收入" in t["premium_estimate"]["interpretation"]
        assert t["themes_provenance"]["ai_revenue_source"].startswith(
            "ai_revenue_manual.json")

    def test_override_takes_priority(self, make_ipo):
        from nacs_model import compute_nacs
        from reports.thesis import synthesize_thesis
        r = compute_nacs(make_ipo())
        bundle = _mock_themes_bundle()
        # ai_revenue_manual 里 0992.HK 是 0.30, 但 override 给 0.50
        t = synthesize_thesis(
            r, themes_bundle=bundle, stock_code="0992.HK",
            ai_revenue_pct_override=0.50,
        )
        assert t["premium_estimate"]["ai_revenue_pct"] == 0.50
        assert "override" in t["themes_provenance"]["ai_revenue_source"]

    def test_low_r_squared_disclaimer(self, make_ipo):
        from nacs_model import compute_nacs
        from reports.thesis import synthesize_thesis
        from reports.themes_data import Provenance
        r = compute_nacs(make_ipo())
        bundle = _mock_themes_bundle()
        # 改 r_squared 到 0.20 (低)
        bundle["premium_curve"] = (
            {**bundle["premium_curve"][0], "r_squared": 0.20},
            bundle["premium_curve"][1],
        )
        t = synthesize_thesis(
            r, themes_bundle=bundle, stock_code="2533.HK",
        )
        assert "R² 偏低" in t["premium_estimate"]["interpretation"]

    def test_no_estimate_when_pct_unknown(self, make_ipo):
        from nacs_model import compute_nacs
        from reports.thesis import synthesize_thesis
        r = compute_nacs(make_ipo())
        bundle = _mock_themes_bundle()
        t = synthesize_thesis(
            r, themes_bundle=bundle, stock_code="9999.HK",  # 不在 manual
        )
        assert t["premium_estimate"] is None


# =============================================================================
# S3 新增: themes_provenance audit
# =============================================================================

class TestThemesProvenance:
    def test_provenance_always_present(self, make_ipo):
        """即便没传 themes_bundle, themes_provenance 也应是 dict (空 dict)"""
        from nacs_model import compute_nacs
        from reports.thesis import synthesize_thesis
        r = compute_nacs(make_ipo())
        t = synthesize_thesis(r, themes_bundle=None)
        assert isinstance(t["themes_provenance"], dict)

    def test_provenance_jsonable(self, make_ipo):
        """themes_provenance 应能直接 json.dumps (写进 nacs_predictions)"""
        import json
        from nacs_model import compute_nacs
        from reports.thesis import synthesize_thesis
        r = compute_nacs(make_ipo())
        bundle = _mock_themes_bundle()
        t = synthesize_thesis(
            r, themes_bundle=bundle, stock_code="0992.HK",
        )
        s = json.dumps(t["themes_provenance"], ensure_ascii=False)
        assert "ai_server" in s
        assert "themes/heat_today.json" in s

    def test_provenance_contains_all_5_files(self, make_ipo):
        from nacs_model import compute_nacs
        from reports.thesis import synthesize_thesis
        r = compute_nacs(make_ipo())
        bundle = _mock_themes_bundle()
        t = synthesize_thesis(
            r, themes_bundle=bundle, stock_code="0992.HK",
        )
        for k in ["heat_today", "premium_curve", "theme_definitions",
                  "ai_revenue_manual", "history"]:
            assert k in t["themes_provenance"]
            assert "status" in t["themes_provenance"][k]
