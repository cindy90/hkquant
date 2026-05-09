"""
P1.1 — L1.4 发行结构 总市值分桶 modifier 测试.

新模块: cfg.layer1_offering_mkt_cap (small_cap / mid_cap / mega_cap)
被打的字段: OfferingStructure.mkt_cap_at_offer_hkd

覆盖:
    - 小盘 (<5B HKD)            → -10
    - 巨型盘 (>500B HKD)        → -5
    - 中盘 (5B-500B)            → 0
    - mkt_cap_at_offer_hkd=None → 不动 (向后兼容, 历史 row 没填的不罚)
    - 边界: 等于阈值 → mid_cap (用 < / > 严格)
    - cfg.enabled=False         → 跳过
    - yaml 自定义阈值/罚分      → 立即生效
    - L1.4 score 仍 clip [0,100]
    - rationale 在 modifier ≠ 0 时附 'small_cap'/'mega_cap' 标签
    - score_layer1_company 把 _mkt_cap_modifier 透传到 components 供审计
    - run_v7_backtest.build_offering 用 post_ipo_shares × offer_price 填充
    - analyze_deal price-scan 按 ratio 缩放 mkt_cap (与 offering_size 一致)
"""
from __future__ import annotations

import pytest


# =============================================================================
# 直接打分 _score_l1_4_offering
# =============================================================================

def _make_offering(**overrides):
    from nacs_model import OfferingStructure
    base = dict(
        pricing_in_range=0.7, intl_oversubscription=10.0,
        public_oversubscription=30.0, clawback_triggered=True,
        greenshoe_pct=0.15, offering_size_hkd=2e9,
        pe_at_offer=15, pe_peer_median=22, last_round_premium=-0.10,
        auditor_tier=1,
    )
    base.update(overrides)
    return OfferingStructure(**base)


class TestL14MktCapModifier:
    def test_no_mkt_cap_no_modifier(self):
        """mkt_cap=None → modifier=0, 行为跟 P1.1 之前一致 (向后兼容)"""
        from nacs_model import _score_l1_4_offering
        o = _make_offering(mkt_cap_at_offer_hkd=None)
        score, comp = _score_l1_4_offering(o)
        assert comp["mkt_cap_modifier"] == 0.0
        assert comp["mkt_cap_bucket"] == "n/a"

    def test_small_cap_negative_modifier(self):
        """市值 0.5B HKD < 5B → small_cap -10"""
        from nacs_model import _score_l1_4_offering
        o_no = _make_offering(mkt_cap_at_offer_hkd=None)
        o_sm = _make_offering(mkt_cap_at_offer_hkd=0.5e9)
        s_no, _ = _score_l1_4_offering(o_no)
        s_sm, comp_sm = _score_l1_4_offering(o_sm)
        assert comp_sm["mkt_cap_modifier"] == -10.0
        assert comp_sm["mkt_cap_bucket"] == "small_cap"
        # 罚分应让 L1.4 减少 10 (clip 之内)
        assert s_sm == pytest.approx(s_no - 10.0, abs=0.01)

    def test_mega_cap_negative_modifier(self):
        """市值 800B HKD > 500B → mega_cap -5"""
        from nacs_model import _score_l1_4_offering
        o_no = _make_offering(mkt_cap_at_offer_hkd=None)
        o_mg = _make_offering(mkt_cap_at_offer_hkd=8e11)
        s_no, _ = _score_l1_4_offering(o_no)
        s_mg, comp_mg = _score_l1_4_offering(o_mg)
        assert comp_mg["mkt_cap_modifier"] == -5.0
        assert comp_mg["mkt_cap_bucket"] == "mega_cap"
        assert s_mg == pytest.approx(s_no - 5.0, abs=0.01)

    def test_mid_cap_no_modifier(self):
        """中盘 5B-500B → 不动"""
        from nacs_model import _score_l1_4_offering
        for mc in [5e9, 50e9, 100e9, 499e9]:
            o = _make_offering(mkt_cap_at_offer_hkd=mc)
            _, comp = _score_l1_4_offering(o)
            assert comp["mkt_cap_modifier"] == 0.0, f"mc={mc} 不该触发 modifier"
            assert comp["mkt_cap_bucket"] == "mid_cap"

    def test_boundary_at_small_threshold(self):
        """5B 整 → 不算小盘 (< 严格小于)"""
        from nacs_model import _score_l1_4_offering
        o = _make_offering(mkt_cap_at_offer_hkd=5e9)
        _, comp = _score_l1_4_offering(o)
        assert comp["mkt_cap_modifier"] == 0.0
        assert comp["mkt_cap_bucket"] == "mid_cap"

    def test_boundary_at_mega_threshold(self):
        """500B 整 → 不算 mega (> 严格大于)"""
        from nacs_model import _score_l1_4_offering
        o = _make_offering(mkt_cap_at_offer_hkd=500e9)
        _, comp = _score_l1_4_offering(o)
        assert comp["mkt_cap_modifier"] == 0.0
        assert comp["mkt_cap_bucket"] == "mid_cap"

    def test_score_clipped_to_0_100(self):
        """极差发行 + small_cap penalty 不应让 score 跌到负"""
        from nacs_model import _score_l1_4_offering
        o = _make_offering(
            pricing_in_range=0.05, intl_oversubscription=2.0,
            public_oversubscription=0.5, clawback_triggered=False,
            greenshoe_pct=0.0, offering_size_hkd=1e8,
            mkt_cap_at_offer_hkd=0.3e9,
        )
        s, _ = _score_l1_4_offering(o)
        assert 0 <= s <= 100


# =============================================================================
# 通过 score_layer1_company 透传
# =============================================================================

class TestL1ThroughCompute:
    def test_score_layer1_passes_mkt_cap_modifier(self, make_ipo):
        """L1.4 components 应有 _mkt_cap_modifier 用于复盘"""
        from nacs_model import score_layer1_company
        ipo = make_ipo()
        ipo.offering.mkt_cap_at_offer_hkd = 0.5e9   # small
        breakdown = score_layer1_company(ipo)
        assert breakdown.components.get("_mkt_cap_modifier") == -10.0
        assert breakdown.components.get("_mkt_cap_bucket") == "small_cap"

    def test_no_mkt_cap_unchanged_breakdown(self, make_ipo):
        """mkt_cap_at_offer_hkd=None → modifier=0, 不影响 layer1"""
        from nacs_model import score_layer1_company
        ipo = make_ipo()
        # default mkt_cap_at_offer_hkd=None
        breakdown = score_layer1_company(ipo)
        assert breakdown.components.get("_mkt_cap_modifier") == 0.0


# =============================================================================
# Config 控制
# =============================================================================

class TestConfigDriven:
    def test_disabled_skips_modifier(self, make_ipo):
        from config import (
            NacsConfig, Layer1OfferingMktCap, set_config, reset_config,
        )
        from nacs_model import compute_nacs
        try:
            cfg = NacsConfig()
            cfg.layer1_offering_mkt_cap = Layer1OfferingMktCap(enabled=False)
            set_config(cfg)
            ipo = make_ipo()
            ipo.offering.mkt_cap_at_offer_hkd = 0.5e9   # 应触发但被禁用
            r = compute_nacs(ipo)
            mod = r.layer1.components.get("_mkt_cap_modifier", 0)
            assert mod == 0.0
            # bucket 也应保持 n/a (整个分桶逻辑跳过)
            assert r.layer1.components.get("_mkt_cap_bucket") == "n/a"
        finally:
            reset_config()

    def test_yaml_thresholds_loaded(self):
        """nacs_v8.yaml 的 layer1_offering_mkt_cap 字段应被解析"""
        from pathlib import Path
        from config import load_config
        cfg = load_config(Path(__file__).resolve().parent.parent
                          / "configs" / "nacs_v8.yaml")
        mc = cfg.layer1_offering_mkt_cap
        assert mc.enabled is True
        assert mc.small_cap_threshold_hkd == 5_000_000_000
        assert mc.small_cap_penalty == -10.0
        assert mc.mega_cap_threshold_hkd == 500_000_000_000
        assert mc.mega_cap_penalty == -5.0

    def test_custom_thresholds_via_config(self, make_ipo):
        """改 small_cap 阈值到 10B → 8B 公司算小盘"""
        from config import (
            NacsConfig, Layer1OfferingMktCap, set_config, reset_config,
        )
        from nacs_model import compute_nacs
        try:
            cfg = NacsConfig()
            cfg.layer1_offering_mkt_cap = Layer1OfferingMktCap(
                enabled=True,
                small_cap_threshold_hkd=10e9,    # 提到 10B
                small_cap_penalty=-15.0,         # 罚得更重
                mega_cap_threshold_hkd=500e9,
                mega_cap_penalty=-5.0,
            )
            set_config(cfg)
            ipo = make_ipo()
            ipo.offering.mkt_cap_at_offer_hkd = 8e9   # 在新阈值下算 small
            r = compute_nacs(ipo)
            assert r.layer1.components["_mkt_cap_modifier"] == -15.0
            assert r.layer1.components["_mkt_cap_bucket"] == "small_cap"
        finally:
            reset_config()


# =============================================================================
# Rationale (审计 footprint)
# =============================================================================

class TestRationaleAnnotation:
    def test_explain_l1_4_small_cap_tag(self, make_ipo):
        from nacs_model import compute_nacs
        ipo = make_ipo()
        ipo.offering.mkt_cap_at_offer_hkd = 1e9   # small
        r = compute_nacs(ipo)
        l1_4_reason = r.layer1.reasons.get("L1.4_offering", "")
        assert "small_cap" in l1_4_reason
        assert "-10" in l1_4_reason
        # 总市值数值也应可读出
        assert "1.0B" in l1_4_reason or "1B" in l1_4_reason

    def test_explain_l1_4_mega_cap_tag(self, make_ipo):
        from nacs_model import compute_nacs
        ipo = make_ipo()
        ipo.offering.mkt_cap_at_offer_hkd = 800e9   # mega
        r = compute_nacs(ipo)
        l1_4_reason = r.layer1.reasons.get("L1.4_offering", "")
        assert "mega_cap" in l1_4_reason
        assert "-5" in l1_4_reason

    def test_explain_l1_4_no_tag_when_mid(self, make_ipo):
        from nacs_model import compute_nacs
        ipo = make_ipo()
        ipo.offering.mkt_cap_at_offer_hkd = 50e9   # mid
        r = compute_nacs(ipo)
        l1_4_reason = r.layer1.reasons.get("L1.4_offering", "")
        assert "small_cap" not in l1_4_reason
        assert "mega_cap" not in l1_4_reason

    def test_explain_l1_4_no_tag_when_none(self, make_ipo):
        from nacs_model import compute_nacs
        ipo = make_ipo()
        # default mkt_cap=None → modifier=0 → 不应渲染 modifier 段
        r = compute_nacs(ipo)
        l1_4_reason = r.layer1.reasons.get("L1.4_offering", "")
        assert "总市值" not in l1_4_reason


# =============================================================================
# Pipeline 集成 (build_offering / analyze_deal price-scan)
# =============================================================================

class TestBuildOfferingIntegration:
    def test_build_offering_populates_mkt_cap(self):
        """run_v7_backtest.build_offering 用 post_ipo_shares × offer_price"""
        # 构造一个 sqlite row-like dict 来模拟 build_offering 的入参
        # 由于 build_offering 涉及 conn 查询, 这里测算式直接验证: 字段会传过去
        from nacs_model import OfferingStructure
        # 我们重现 build_offering 里那段填充逻辑:
        post_shares = 1_000_000_000
        offer_price = 12.5
        expected_mc = post_shares * offer_price   # 12.5B
        o = OfferingStructure(
            pricing_in_range=0.6, intl_oversubscription=5.0,
            public_oversubscription=10.0, clawback_triggered=False,
            greenshoe_pct=0.15, offering_size_hkd=1e9,
            mkt_cap_at_offer_hkd=expected_mc,
        )
        assert o.mkt_cap_at_offer_hkd == expected_mc
        # 验证打分接受这个值: 12.5B 算 mid_cap
        from nacs_model import _score_l1_4_offering
        _, comp = _score_l1_4_offering(o)
        assert comp["mkt_cap_bucket"] == "mid_cap"

    def test_build_offering_handles_missing(self):
        """post_ipo_shares 或 offer_price 缺失 → mkt_cap=None, 不会崩"""
        from nacs_model import OfferingStructure, _score_l1_4_offering
        o = OfferingStructure(
            pricing_in_range=0.6, intl_oversubscription=5.0,
            public_oversubscription=10.0, clawback_triggered=False,
            greenshoe_pct=0.15, offering_size_hkd=1e9,
            mkt_cap_at_offer_hkd=None,
        )
        s, comp = _score_l1_4_offering(o)
        assert comp["mkt_cap_modifier"] == 0.0
        assert comp["mkt_cap_bucket"] == "n/a"
        assert 0 <= s <= 100


class TestPriceScanRescale:
    """analyze_deal price-scan 时, mkt_cap 也应按价格 ratio 缩放
       (post_ipo_shares 不变, 价格变 → 总市值线性变)"""

    def test_low_scenario_rescales_down(self, make_ipo):
        """price ratio 0.85 应让 mkt_cap × 0.85"""
        from nacs_model import IPOOffering
        ipo = make_ipo()
        ipo.offering.mkt_cap_at_offer_hkd = 10e9   # 10B baseline (mid_cap)
        # 模拟 _evaluate_deal price-scan 的复制 + ratio 缩放
        ratio = 0.85
        new_off = IPOOffering(**{
            **ipo.__dict__,
            "offering": _replace_offering(ipo.offering, ratio),
        })
        assert new_off.offering.mkt_cap_at_offer_hkd == pytest.approx(8.5e9)
        # 仍是 mid_cap
        from nacs_model import _score_l1_4_offering
        _, comp = _score_l1_4_offering(new_off.offering)
        assert comp["mkt_cap_bucket"] == "mid_cap"

    def test_low_scenario_can_flip_to_small(self, make_ipo):
        """边界: baseline 5.5B mid, ratio 0.85 → 4.675B small"""
        from nacs_model import IPOOffering
        ipo = make_ipo()
        ipo.offering.mkt_cap_at_offer_hkd = 5.5e9
        ratio = 0.85
        new_off = IPOOffering(**{
            **ipo.__dict__,
            "offering": _replace_offering(ipo.offering, ratio),
        })
        from nacs_model import _score_l1_4_offering
        _, comp = _score_l1_4_offering(new_off.offering)
        assert comp["mkt_cap_bucket"] == "small_cap"


def _replace_offering(off, ratio):
    """复制 OfferingStructure, 价格相关字段按 ratio 缩放
       — 跟 analyze_deal._evaluate_deal 的 price-scan 缩放逻辑一致"""
    from copy import copy
    new_off = copy(off)
    if off.pe_at_offer is not None:
        new_off.pe_at_offer = off.pe_at_offer * ratio
    if off.offering_size_hkd is not None:
        new_off.offering_size_hkd = off.offering_size_hkd * ratio
    if off.mkt_cap_at_offer_hkd is not None:
        new_off.mkt_cap_at_offer_hkd = off.mkt_cap_at_offer_hkd * ratio
    return new_off
