"""
P0.2 — AI 镀金 post-adjustment 测试.

覆盖:
    - 触发条件: theme ∈ ai_themes AND ai_revenue_pct < threshold → ×multiplier
    - 不触发: theme 不在 ai_themes / theme=None / ai_revenue_pct >= threshold /
              ai_revenue_pct=None / enabled=false
    - adjustments_applied 列表带可读条目 (含 theme_id + AI 收入 + threshold)
    - explain_adjustment 给出"为什么折扣"解读
    - 跟其它 post-adjustment 链式叠加 (e.g. 18C ×0.70 + AI 镀金 ×0.85)
    - yaml 阈值改动立即生效 (改 threshold 到 0.20 让 100% 之外的边界 case 触发)
"""
from __future__ import annotations

import pytest


# =============================================================================
# 触发逻辑
# =============================================================================

class TestGildingTrigger:
    def test_triggers_when_ai_theme_low_pct(self, make_ipo):
        from nacs_model import compute_nacs
        ipo = make_ipo()
        ipo.theme_id = "ai_server"
        ipo.ai_revenue_pct = 0.05    # < 10% threshold
        r = compute_nacs(ipo)
        gilded = [a for a in r.adjustments_applied if "AI 镀金" in a]
        assert len(gilded) == 1
        assert "x0.85" in gilded[0]
        assert "ai_server" in gilded[0]
        assert "5%" in gilded[0]

    def test_no_trigger_when_pct_above_threshold(self, make_ipo):
        from nacs_model import compute_nacs
        ipo = make_ipo()
        ipo.theme_id = "ai_server"
        ipo.ai_revenue_pct = 0.50
        r = compute_nacs(ipo)
        assert not any("AI 镀金" in a for a in r.adjustments_applied)

    def test_no_trigger_when_pct_at_threshold(self, make_ipo):
        """边界: pct == threshold 不触发 (使用 < 严格小于)"""
        from nacs_model import compute_nacs
        ipo = make_ipo()
        ipo.theme_id = "ai_server"
        ipo.ai_revenue_pct = 0.10    # 等于阈值
        r = compute_nacs(ipo)
        assert not any("AI 镀金" in a for a in r.adjustments_applied)

    def test_no_trigger_when_theme_not_ai(self, make_ipo):
        from nacs_model import compute_nacs
        ipo = make_ipo()
        ipo.theme_id = "innovative_drug"   # 不在 ai_themes 默认集
        ipo.ai_revenue_pct = 0.02
        r = compute_nacs(ipo)
        assert not any("AI 镀金" in a for a in r.adjustments_applied)

    def test_no_trigger_when_theme_none(self, make_ipo):
        from nacs_model import compute_nacs
        ipo = make_ipo()
        ipo.theme_id = None
        ipo.ai_revenue_pct = 0.02
        r = compute_nacs(ipo)
        assert not any("AI 镀金" in a for a in r.adjustments_applied)

    def test_no_trigger_when_pct_none(self, make_ipo):
        """ai_revenue_pct unknown 时保守不应用 (不能因为不知道就罚)"""
        from nacs_model import compute_nacs
        ipo = make_ipo()
        ipo.theme_id = "ai_server"
        ipo.ai_revenue_pct = None
        r = compute_nacs(ipo)
        assert not any("AI 镀金" in a for a in r.adjustments_applied)


# =============================================================================
# Multiplier 数值正确
# =============================================================================

class TestMultiplierValue:
    def test_nacs_drops_by_85pct(self, make_ipo):
        from nacs_model import compute_nacs
        # baseline (no theme/AI)
        ipo_base = make_ipo()
        nacs_base = compute_nacs(ipo_base).nacs_adjusted

        # gilding triggered
        ipo_gild = make_ipo()
        ipo_gild.theme_id = "ai_server"
        ipo_gild.ai_revenue_pct = 0.03
        nacs_gild = compute_nacs(ipo_gild).nacs_adjusted

        # 镀金折扣应该让 nacs_adj 减约 15%
        assert nacs_gild == pytest.approx(nacs_base * 0.85, abs=0.001)

    def test_chains_with_18c_discount(self, make_ipo):
        """18C deal + AI 镀金 → 应同时叠加 ×0.70 × ×0.85"""
        from nacs_model import compute_nacs, ListingChapter, CompanyType, TechC18Fundamentals
        ipo = make_ipo()
        ipo.listing_chapter = ListingChapter.CHAPTER_18C_COMMERCIAL
        ipo.company_type = CompanyType.TECH_18C
        ipo.tech18c = TechC18Fundamentals(
            is_commercial=True, revenue_growth_yoy=0.4,
            milestone_score=4.0, rd_intensity=0.18,
        )
        ipo.theme_id = "ai_server"
        ipo.ai_revenue_pct = 0.05
        r = compute_nacs(ipo)
        # 应有 2 个 multiplier
        adjs = [a for a in r.adjustments_applied if "x0." in a]
        assert any("18C" in a for a in adjs)
        assert any("AI 镀金" in a for a in adjs)


# =============================================================================
# Config 控制
# =============================================================================

class TestConfigDriven:
    def test_disabled_skips(self, make_ipo):
        from config import (
            NacsConfig, PostAdjustments, AiGildingAdjustment,
            set_config, reset_config,
        )
        from nacs_model import compute_nacs
        try:
            cfg = NacsConfig()
            cfg.post_adjustments = PostAdjustments(
                ai_gilding=AiGildingAdjustment(enabled=False),
            )
            set_config(cfg)
            ipo = make_ipo()
            ipo.theme_id = "ai_server"
            ipo.ai_revenue_pct = 0.02
            r = compute_nacs(ipo)
            assert not any("AI 镀金" in a for a in r.adjustments_applied)
        finally:
            reset_config()

    def test_threshold_changeable(self, make_ipo):
        """改 threshold=0.30 → AI 收入 25% 触发 (默认 10% 不触发)"""
        from config import (
            NacsConfig, PostAdjustments, AiGildingAdjustment,
            set_config, reset_config,
        )
        from nacs_model import compute_nacs
        try:
            cfg = NacsConfig()
            cfg.post_adjustments = PostAdjustments(
                ai_gilding=AiGildingAdjustment(threshold=0.30),
            )
            set_config(cfg)
            ipo = make_ipo()
            ipo.theme_id = "ai_server"
            ipo.ai_revenue_pct = 0.25
            r = compute_nacs(ipo)
            assert any("AI 镀金" in a for a in r.adjustments_applied)
        finally:
            reset_config()

    def test_custom_themes_set(self, make_ipo):
        """改 ai_themes 把 innovative_drug 也算 AI"""
        from config import (
            NacsConfig, PostAdjustments, AiGildingAdjustment,
            set_config, reset_config,
        )
        from nacs_model import compute_nacs
        try:
            cfg = NacsConfig()
            cfg.post_adjustments = PostAdjustments(
                ai_gilding=AiGildingAdjustment(
                    ai_themes=["innovative_drug"],
                ),
            )
            set_config(cfg)
            ipo = make_ipo()
            ipo.theme_id = "innovative_drug"
            ipo.ai_revenue_pct = 0.05
            r = compute_nacs(ipo)
            assert any("AI 镀金" in a for a in r.adjustments_applied)
        finally:
            reset_config()

    def test_yaml_loads_ai_gilding_section(self):
        """nacs_v8.yaml 的 post_adjustments.ai_gilding 应被解析"""
        from pathlib import Path
        from config import load_config
        cfg = load_config(Path(__file__).resolve().parent.parent
                          / "configs" / "nacs_v8.yaml")
        ag = cfg.post_adjustments.ai_gilding
        assert ag.enabled is True
        assert "ai_server" in ag.ai_themes
        assert ag.threshold == 0.10
        assert ag.multiplier == 0.85


# =============================================================================
# Audit / rationale
# =============================================================================

class TestAudit:
    def test_explain_adjustment_describes_gilding(self):
        from nacs_rationale import explain_adjustment
        out = explain_adjustment("AI 镀金折扣 x0.85 (theme=ai_server, AI 收入 5% < 10%)")
        assert "镀金" in out
        assert "AI 业务" in out or "AI 概念" in out

    def test_layer1_components_record_theme_for_audit(self, make_ipo):
        """layer1 components 应有 _theme_heat_score (P0.1) 用于复盘
           — gilding 触发时 case 在 nacs_predictions 里也记录得清楚"""
        from nacs_model import compute_nacs
        ipo = make_ipo()
        ipo.theme_id = "ai_server"
        ipo.theme_heat_score = 72
        ipo.ai_revenue_pct = 0.05
        r = compute_nacs(ipo)
        # heat 应进 layer1.components (P0.1 集成)
        assert "_theme_heat_score" in r.layer1.components
        # gilding adjustment 应进 adjustments_applied
        assert any("AI 镀金" in a for a in r.adjustments_applied)
