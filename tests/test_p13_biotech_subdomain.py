"""
P1.3 — L1.3 18A 子领域 multiplier 测试.

新模块: cfg.layer1_biotech_subdomain
被打的字段: BiotechFundamentals.subdomain (创新药/器械/细胞基因/诊断)

覆盖:
    - innovative_drug   → ×1.00 (基线)
    - medical_device    → ×0.90
    - cell_gene         → ×1.10
    - diagnostics       → ×0.85
    - subdomain=None    → ×1.00 (向后兼容, 历史 row 没填的不动)
    - subdomain=未知 str → ×1.00 (multipliers dict 没匹配走默认)
    - cfg.enabled=False → 全部 ×1.00 (不查 multipliers)
    - L1.3 score 仍 clip [0,100]
    - 仅作用于 BIOTECH_18A; profitable / 18C 不受影响
    - rationale 在 mult ≠ 1.0 时附中文子领域标签
    - score_layer1_company 把 _subdomain / _subdomain_multiplier 透传到 components
    - yaml 自定义 multipliers dict → 立即生效
"""
from __future__ import annotations

import pytest


def _make_biotech(**overrides):
    from nacs_model import BiotechFundamentals
    base = dict(
        core_pipeline_phase="II",
        pipeline_count_phase2plus=2,
        cash_runway_months=18,
        bd_deals_count_2y=1,
    )
    base.update(overrides)
    return BiotechFundamentals(**base)


def _make_18a_ipo(sample_cornerstones, **biotech_overrides):
    from nacs_model import (
        IPOOffering, ListingChapter, CompanyType,
        OfferingStructure, SponsorInfo, MarketEnvironment, LockupContext,
        SponsorTier,
    )
    return IPOOffering(
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
        biotech=_make_biotech(**biotech_overrides),
    )


# =============================================================================
# 直接打分 _score_l1_3_biotech
# =============================================================================

class TestL13BiotechSubdomain:
    def test_innovative_drug_multiplier_baseline(self):
        from nacs_model import _score_l1_3_biotech
        b = _make_biotech(subdomain="innovative_drug")
        s, c = _score_l1_3_biotech(b)
        assert c["subdomain"] == "innovative_drug"
        assert c["subdomain_multiplier"] == pytest.approx(1.00)

    def test_medical_device_haircut(self):
        from nacs_model import _score_l1_3_biotech
        b_no = _make_biotech(subdomain=None)
        b_md = _make_biotech(subdomain="medical_device")
        s_no, _ = _score_l1_3_biotech(b_no)
        s_md, c = _score_l1_3_biotech(b_md)
        assert c["subdomain_multiplier"] == pytest.approx(0.90)
        assert s_md == pytest.approx(s_no * 0.90, abs=0.01)

    def test_cell_gene_bonus(self):
        from nacs_model import _score_l1_3_biotech
        b_no = _make_biotech(subdomain=None)
        b_cg = _make_biotech(subdomain="cell_gene")
        s_no, _ = _score_l1_3_biotech(b_no)
        s_cg, c = _score_l1_3_biotech(b_cg)
        assert c["subdomain_multiplier"] == pytest.approx(1.10)
        assert s_cg == pytest.approx(s_no * 1.10, abs=0.01)

    def test_diagnostics_haircut(self):
        from nacs_model import _score_l1_3_biotech
        b = _make_biotech(subdomain="diagnostics")
        s, c = _score_l1_3_biotech(b)
        assert c["subdomain_multiplier"] == pytest.approx(0.85)

    def test_no_subdomain_no_modifier(self):
        """subdomain=None 走 1.0 不动 (向后兼容)"""
        from nacs_model import _score_l1_3_biotech
        b = _make_biotech(subdomain=None)
        s, c = _score_l1_3_biotech(b)
        assert c["subdomain"] == "unknown"
        assert c["subdomain_multiplier"] == 1.0

    def test_unknown_subdomain_falls_back_to_1(self):
        """未知 subdomain str → 不在 multipliers dict 里 → 走 1.0"""
        from nacs_model import _score_l1_3_biotech
        b = _make_biotech(subdomain="foo_bar_xyz")
        s, c = _score_l1_3_biotech(b)
        assert c["subdomain"] == "foo_bar_xyz"
        assert c["subdomain_multiplier"] == 1.0

    def test_score_clipped_to_100(self):
        """phase=Approved + 高其他项 + ×1.10 → clip 到 100"""
        from nacs_model import _score_l1_3_biotech
        b = _make_biotech(
            core_pipeline_phase="Approved",
            pipeline_count_phase2plus=5,
            cash_runway_months=36,
            bd_deals_count_2y=3,
            subdomain="cell_gene",
        )
        s, _ = _score_l1_3_biotech(b)
        assert s == pytest.approx(100.0, abs=0.01)

    def test_score_clipped_to_0(self):
        """phase=Pre 0 项目 + ×0.85 不让 score 跌负"""
        from nacs_model import _score_l1_3_biotech
        b = _make_biotech(
            core_pipeline_phase="Pre",
            pipeline_count_phase2plus=0,
            cash_runway_months=0,
            bd_deals_count_2y=0,
            subdomain="diagnostics",
        )
        s, _ = _score_l1_3_biotech(b)
        assert s >= 0


# =============================================================================
# 子类隔离 (profitable / 18C 不受影响)
# =============================================================================

class TestSubtypeIsolation:
    def test_profitable_not_affected(self, make_ipo):
        """make_ipo 默认是 PROFITABLE; components 不应有 _subdomain"""
        from nacs_model import score_layer1_company
        ipo = make_ipo()
        breakdown = score_layer1_company(ipo)
        assert "_subdomain" not in breakdown.components
        assert "_subdomain_multiplier" not in breakdown.components

    def test_18a_has_subdomain_in_components(self, sample_cornerstones):
        from nacs_model import score_layer1_company
        ipo = _make_18a_ipo(sample_cornerstones, subdomain="cell_gene")
        breakdown = score_layer1_company(ipo)
        assert breakdown.components.get("_subdomain") == "cell_gene"
        assert breakdown.components.get("_subdomain_multiplier") == pytest.approx(1.10)


# =============================================================================
# Config 控制
# =============================================================================

class TestConfigDriven:
    def test_disabled_skips_multiplier(self, sample_cornerstones):
        from config import (
            NacsConfig, Layer1BiotechSubdomain, set_config, reset_config,
        )
        from nacs_model import score_layer1_company
        try:
            cfg = NacsConfig()
            cfg.layer1_biotech_subdomain = Layer1BiotechSubdomain(enabled=False)
            set_config(cfg)
            ipo = _make_18a_ipo(sample_cornerstones, subdomain="cell_gene")
            breakdown = score_layer1_company(ipo)
            # disabled → multiplier=1.0 (即使 subdomain 已知)
            assert breakdown.components["_subdomain_multiplier"] == 1.0
        finally:
            reset_config()

    def test_yaml_loads_subdomain(self):
        """nacs_v8.yaml 的 layer1_biotech_subdomain 字段应被解析"""
        from pathlib import Path
        from config import load_config
        cfg = load_config(Path(__file__).resolve().parent.parent
                          / "configs" / "nacs_v8.yaml")
        sd = cfg.layer1_biotech_subdomain
        assert sd.enabled is True
        assert sd.multipliers["innovative_drug"] == pytest.approx(1.00)
        assert sd.multipliers["medical_device"] == pytest.approx(0.90)
        assert sd.multipliers["cell_gene"] == pytest.approx(1.10)
        assert sd.multipliers["diagnostics"] == pytest.approx(0.85)

    def test_custom_multipliers_apply(self, sample_cornerstones):
        """改 multiplier dict 直接生效"""
        from config import (
            NacsConfig, Layer1BiotechSubdomain, set_config, reset_config,
        )
        from nacs_model import score_layer1_company
        try:
            cfg = NacsConfig()
            cfg.layer1_biotech_subdomain = Layer1BiotechSubdomain(
                multipliers={
                    "innovative_drug": 1.20,
                    "diagnostics": 0.50,
                },
            )
            set_config(cfg)
            ipo = _make_18a_ipo(sample_cornerstones, subdomain="innovative_drug")
            breakdown = score_layer1_company(ipo)
            assert breakdown.components["_subdomain_multiplier"] == pytest.approx(1.20)
        finally:
            reset_config()


# =============================================================================
# Rationale (审计 footprint)
# =============================================================================

class TestRationaleAnnotation:
    def test_explain_l1_3_innovative_drug_no_tag(self, sample_cornerstones):
        """innovative_drug ×1.0 不应渲染 multiplier 段 (跟 moderate 一致)"""
        from nacs_model import compute_nacs
        ipo = _make_18a_ipo(sample_cornerstones, subdomain="innovative_drug")
        r = compute_nacs(ipo)
        l1_3_reason = r.layer1.reasons.get("L1.3_fundamentals", "")
        assert "18A 子领域" not in l1_3_reason

    def test_explain_l1_3_cell_gene_tag(self, sample_cornerstones):
        from nacs_model import compute_nacs
        ipo = _make_18a_ipo(sample_cornerstones, subdomain="cell_gene")
        r = compute_nacs(ipo)
        l1_3_reason = r.layer1.reasons.get("L1.3_fundamentals", "")
        assert "细胞基因" in l1_3_reason
        assert "1.10" in l1_3_reason

    def test_explain_l1_3_medical_device_tag(self, sample_cornerstones):
        from nacs_model import compute_nacs
        ipo = _make_18a_ipo(sample_cornerstones, subdomain="medical_device")
        r = compute_nacs(ipo)
        l1_3_reason = r.layer1.reasons.get("L1.3_fundamentals", "")
        assert "医疗器械" in l1_3_reason
        assert "0.90" in l1_3_reason

    def test_explain_l1_3_diagnostics_tag(self, sample_cornerstones):
        from nacs_model import compute_nacs
        ipo = _make_18a_ipo(sample_cornerstones, subdomain="diagnostics")
        r = compute_nacs(ipo)
        l1_3_reason = r.layer1.reasons.get("L1.3_fundamentals", "")
        assert "诊断" in l1_3_reason or "IVD" in l1_3_reason
        assert "0.85" in l1_3_reason

    def test_explain_l1_3_no_tag_when_none(self, sample_cornerstones):
        """subdomain=None → mult=1.0 → 不渲染"""
        from nacs_model import compute_nacs
        ipo = _make_18a_ipo(sample_cornerstones, subdomain=None)
        r = compute_nacs(ipo)
        l1_3_reason = r.layer1.reasons.get("L1.3_fundamentals", "")
        assert "18A 子领域" not in l1_3_reason
