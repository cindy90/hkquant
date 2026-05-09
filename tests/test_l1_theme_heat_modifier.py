"""
P0.1 — L1.6 市场环境 主题情绪 modifier 测试.

覆盖:
    - heat ≥ overheated_threshold (默认 80) → -5 罚分
    - heat < trough_threshold (默认 40) → +3 加分
    - 中间 (40 ≤ heat < 80) → 0
    - heat=None (无主题或未注入) → 不影响打分 (向后兼容)
    - cfg.layer1_market_theme_heat.enabled=False → 跳过
    - 阈值/罚分由 yaml 控制 (改 yaml 立即生效)
    - rationale (_explain_l1_6) 在 modifier ≠ 0 时附 'overheated' / 'trough' 标签
    - score_layer1_company 把 heat 透传给 _score_l1_6_market
    - clip 后 L1.6 score 仍在 [0, 100]
"""
from __future__ import annotations

import pytest


# =============================================================================
# 直接打分 _score_l1_6_market
# =============================================================================

def _make_market(**overrides):
    from nacs_model import MarketEnvironment
    base = dict(
        hsi_60d_return=0.03, hsi_60d_vol_annualized=0.20,
        hsi_60d_vol_pct_rank=0.5, hsi_valuation_pct=0.5,
        hk_ipo_30d_avg_d30=0.05, hk_ipo_30d_breakage_rate=0.50,
        southbound_30d_net_normalized=0.0, sector_60d_vol_annualized=0.30,
    )
    base.update(overrides)
    return MarketEnvironment(**base)


class TestL16ThemeHeatModifier:
    def test_no_heat_score_no_modifier(self):
        """heat=None → modifier=0, 行为跟改造前一致 (向后兼容)"""
        from nacs_model import _score_l1_6_market
        m = _make_market()
        score, comp = _score_l1_6_market(m, theme_heat_score=None)
        assert comp["theme_heat_modifier"] == 0.0
        # 5 子项加和 ≈ score
        sub_sum = comp["momentum"] + comp["low_vol"] + comp["ipo_30d_avg"] + \
                  comp["ipo_30d_brk"] + comp["southbound"]
        assert score == pytest.approx(sub_sum, abs=0.01)

    def test_overheated_applies_negative_modifier(self):
        from nacs_model import _score_l1_6_market
        m = _make_market()
        s_no, _ = _score_l1_6_market(m, theme_heat_score=None)
        s_hot, comp_hot = _score_l1_6_market(m, theme_heat_score=85)
        assert comp_hot["theme_heat_modifier"] == -5.0
        # heat 罚分应该让 score 减少 5 (clip 之内)
        assert s_hot == pytest.approx(s_no - 5.0, abs=0.01)

    def test_trough_applies_positive_modifier(self):
        from nacs_model import _score_l1_6_market
        m = _make_market()
        s_no, _ = _score_l1_6_market(m, theme_heat_score=None)
        s_low, comp_low = _score_l1_6_market(m, theme_heat_score=30)
        assert comp_low["theme_heat_modifier"] == 3.0
        assert s_low == pytest.approx(s_no + 3.0, abs=0.01)

    def test_moderate_heat_no_modifier(self):
        from nacs_model import _score_l1_6_market
        m = _make_market()
        for h in [40, 55, 70, 79]:
            _, comp = _score_l1_6_market(m, theme_heat_score=h)
            assert comp["theme_heat_modifier"] == 0.0, \
                f"heat={h} 不该触发 modifier"

    def test_score_clipped_to_0_100(self):
        """极端低基础 + 罚分不应该让 score 跌到负"""
        from nacs_model import _score_l1_6_market
        # 构造低基础市场
        m = _make_market(
            hsi_60d_return=-0.50, hsi_60d_vol_pct_rank=1.0,
            hk_ipo_30d_avg_d30=-0.50, hk_ipo_30d_breakage_rate=1.0,
            southbound_30d_net_normalized=-1.0,
        )
        s, _ = _score_l1_6_market(m, theme_heat_score=95)
        assert s >= 0
        assert s <= 100


# =============================================================================
# 通过 score_layer1_company 透传
# =============================================================================

class TestL1ThroughCompute:
    def test_score_layer1_passes_theme_heat(self, make_ipo):
        from nacs_model import score_layer1_company
        ipo = make_ipo()
        ipo.theme_heat_score = 85   # overheated
        breakdown = score_layer1_company(ipo)
        # L1.6 components 应有 theme_heat_modifier=-5
        assert breakdown.components.get("_theme_heat_modifier") == -5.0
        assert breakdown.components.get("_theme_heat_score") == 85.0

    def test_no_theme_score_unchanged_breakdown(self, make_ipo):
        from nacs_model import score_layer1_company
        ipo = make_ipo()
        # default theme_heat_score=None
        breakdown = score_layer1_company(ipo)
        assert breakdown.components.get("_theme_heat_modifier") == 0.0


# =============================================================================
# rationale (审计 footprint)
# =============================================================================

class TestRationaleAnnotation:
    def test_explain_l1_6_overheated_tag(self, make_ipo):
        from nacs_model import compute_nacs
        ipo = make_ipo()
        ipo.theme_heat_score = 90
        r = compute_nacs(ipo)
        l1_6_reason = r.layer1.reasons.get("L1.6_market", "")
        assert "overheated" in l1_6_reason
        assert "-5" in l1_6_reason

    def test_explain_l1_6_trough_tag(self, make_ipo):
        from nacs_model import compute_nacs
        ipo = make_ipo()
        ipo.theme_heat_score = 25
        r = compute_nacs(ipo)
        l1_6_reason = r.layer1.reasons.get("L1.6_market", "")
        assert "trough" in l1_6_reason
        assert "+3" in l1_6_reason

    def test_explain_l1_6_no_tag_when_moderate(self, make_ipo):
        from nacs_model import compute_nacs
        ipo = make_ipo()
        ipo.theme_heat_score = 60
        r = compute_nacs(ipo)
        l1_6_reason = r.layer1.reasons.get("L1.6_market", "")
        assert "overheated" not in l1_6_reason
        assert "trough" not in l1_6_reason

    def test_explain_l1_6_no_tag_when_none(self, make_ipo):
        from nacs_model import compute_nacs
        ipo = make_ipo()
        # no theme_heat_score
        r = compute_nacs(ipo)
        l1_6_reason = r.layer1.reasons.get("L1.6_market", "")
        # 主题段不应出现 (heat 被注入时才显示)
        assert "主题热度" not in l1_6_reason


# =============================================================================
# Config 控制
# =============================================================================

class TestConfigDriven:
    def test_disabled_skips_modifier(self, make_ipo):
        """cfg.layer1_market_theme_heat.enabled=False → 不应用 modifier"""
        from config import (
            NacsConfig, Layer1MarketThemeHeat, set_config, reset_config,
        )
        from nacs_model import compute_nacs
        try:
            cfg = NacsConfig()
            cfg.layer1_market_theme_heat = Layer1MarketThemeHeat(enabled=False)
            set_config(cfg)
            ipo = make_ipo()
            ipo.theme_heat_score = 90  # 应该触发但被禁用
            r = compute_nacs(ipo)
            mod = r.layer1.components.get("_theme_heat_modifier", 0)
            assert mod == 0.0
        finally:
            reset_config()

    def test_yaml_thresholds_loaded(self):
        """nacs_v8.yaml 的 layer1_market_theme_heat 字段应被解析"""
        from config import load_config
        from pathlib import Path
        cfg = load_config(Path(__file__).resolve().parent.parent
                          / "configs" / "nacs_v8.yaml")
        th = cfg.layer1_market_theme_heat
        assert th.enabled is True
        assert th.overheated_threshold == 80
        assert th.overheated_penalty == -5.0
        assert th.trough_threshold == 40
        assert th.trough_bonus == 3.0

    def test_custom_thresholds_via_config(self, make_ipo):
        """改 yaml 阈值应立即生效, 不需重启"""
        from config import (
            NacsConfig, Layer1MarketThemeHeat, set_config, reset_config,
        )
        from nacs_model import compute_nacs
        try:
            cfg = NacsConfig()
            # 把 overheated 拉到 50, penalty 改成 -10
            cfg.layer1_market_theme_heat = Layer1MarketThemeHeat(
                enabled=True,
                overheated_threshold=50,
                overheated_penalty=-10.0,
                trough_threshold=20,
                trough_bonus=5.0,
            )
            set_config(cfg)
            ipo = make_ipo()
            ipo.theme_heat_score = 60   # 在新阈值下算 overheated
            r = compute_nacs(ipo)
            assert r.layer1.components["_theme_heat_modifier"] == -10.0
        finally:
            reset_config()
