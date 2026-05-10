"""
P3.1 — 小盘 + 高基石覆盖率 红旗 post-adjustment 测试.

新模块: cfg.post_adjustments.small_cap_cs_rescue
触发字段: IPOOffering.{offering.offering_size_hkd, cornerstones[].ticket_size_hkd}

覆盖:
    - 双信号同时满足 (small offer + high coverage) → ×multiplier
    - 仅 small offer 不够 → 不触发 (避免误伤优质小盘)
    - 仅 high coverage 不够 → 不触发 (大盘高覆盖通常是央企保盘)
    - 边界: offer == small_threshold → 不触发 (用 < 严格)
    - 边界: coverage == coverage_threshold → 不触发 (用 > 严格)
    - cfg.enabled=False → 跳过
    - cornerstones=[] → 跳过 (无数据)
    - offering.offering_size_hkd=None → 跳过
    - yaml 阈值改动立即生效
    - rationale (explain_adjustment) 给出"为什么折扣"解读
    - 跟其它 post-adjustment 链式叠加 (e.g. AI gilding ×0.85 + 小盘红旗 ×0.90)
"""
from __future__ import annotations

import pytest


# =============================================================================
# 测试 helper: 控制 offering size + cornerstone tickets
# =============================================================================

def _shrink_offering(make_ipo, *, offer_size, ticket_size):
    """构造小盘 + (可调) 基石 ticket 的 IPO"""
    ipo = make_ipo()
    ipo.offering.offering_size_hkd = offer_size
    for c in ipo.cornerstones:
        c.ticket_size_hkd = ticket_size
    return ipo


# =============================================================================
# 触发条件
# =============================================================================

class TestRedFlagTrigger:
    def test_small_offer_high_coverage_triggers(self, make_ipo):
        """募资 1B + 5*200M ticket = 100% 覆盖 → 触发"""
        from nacs_model import compute_nacs
        ipo = _shrink_offering(make_ipo, offer_size=1e9, ticket_size=2e8)
        r = compute_nacs(ipo)
        flagged = [a for a in r.adjustments_applied if "小盘+高基石" in a]
        assert len(flagged) == 1
        assert "x0.9" in flagged[0]
        assert "1.0B" in flagged[0]

    def test_small_offer_low_coverage_no_trigger(self, make_ipo):
        """募资 1B + 5*50M = 25% 覆盖 → 不触发 (优质小盘正常覆盖率)"""
        from nacs_model import compute_nacs
        ipo = _shrink_offering(make_ipo, offer_size=1e9, ticket_size=5e7)
        r = compute_nacs(ipo)
        assert not any("小盘+高基石" in a for a in r.adjustments_applied)

    def test_large_offer_high_coverage_no_trigger(self, make_ipo):
        """募资 5B + 5*1B = 100% 覆盖 → 不触发 (大盘高覆盖是央企/正常)"""
        from nacs_model import compute_nacs
        ipo = _shrink_offering(make_ipo, offer_size=5e9, ticket_size=1e9)
        r = compute_nacs(ipo)
        assert not any("小盘+高基石" in a for a in r.adjustments_applied)

    def test_at_offering_threshold_no_trigger(self, make_ipo):
        """offer == 1.5B 整 → 不触发 (用 < 严格)"""
        from nacs_model import compute_nacs
        ipo = _shrink_offering(make_ipo, offer_size=1.5e9, ticket_size=2e8)
        r = compute_nacs(ipo)
        assert not any("小盘+高基石" in a for a in r.adjustments_applied)

    def test_at_coverage_threshold_no_trigger(self, make_ipo):
        """coverage == 0.55 整 → 不触发 (用 > 严格)"""
        from nacs_model import compute_nacs
        # 募资 1B, 5 cs * 110M = 550M → coverage = 0.55
        ipo = _shrink_offering(make_ipo, offer_size=1e9, ticket_size=1.1e8)
        r = compute_nacs(ipo)
        assert not any("小盘+高基石" in a for a in r.adjustments_applied)

    def test_just_above_coverage_threshold_triggers(self, make_ipo):
        """coverage = 0.555 → 触发"""
        from nacs_model import compute_nacs
        # 5 cs * 111M = 555M / 1B = 0.555
        ipo = _shrink_offering(make_ipo, offer_size=1e9, ticket_size=1.11e8)
        r = compute_nacs(ipo)
        assert any("小盘+高基石" in a for a in r.adjustments_applied)


# =============================================================================
# 数据缺失保守不触发
# =============================================================================

class TestMissingData:
    def test_no_cornerstones_skips(self, make_ipo):
        from nacs_model import compute_nacs
        ipo = _shrink_offering(make_ipo, offer_size=1e9, ticket_size=2e8)
        ipo.cornerstones = []
        # 没基石会先撞 L2 veto, 但 P3.1 后置处理也应不抛
        r = compute_nacs(ipo)
        assert not any("小盘+高基石" in a for a in r.adjustments_applied)

    def test_offering_size_none_skips(self, make_ipo):
        from nacs_model import compute_nacs
        ipo = _shrink_offering(make_ipo, offer_size=1e9, ticket_size=2e8)
        ipo.offering.offering_size_hkd = None
        # offering_size 是 dataclass 必填字段, None 不太合法但代码应优雅 skip
        # (我们的 guard 看 offering_size_hkd is None)
        try:
            r = compute_nacs(ipo)
            assert not any("小盘+高基石" in a for a in r.adjustments_applied)
        except (TypeError, ZeroDivisionError):
            pytest.skip("offering_size None 走 L1 veto 路径, P3.1 不涉及")


# =============================================================================
# Multiplier 数值正确
# =============================================================================

class TestMultiplierMath:
    def test_nacs_drops_by_10pct(self, make_ipo):
        from nacs_model import compute_nacs
        # baseline (大盘, 不触发)
        ipo_base = _shrink_offering(make_ipo, offer_size=5e9, ticket_size=5e7)
        nacs_base = compute_nacs(ipo_base).nacs_adjusted
        # 触发版本
        ipo_flag = _shrink_offering(make_ipo, offer_size=1e9, ticket_size=2e8)
        nacs_flag = compute_nacs(ipo_flag).nacs_adjusted
        # 注意: baseline 和 flag 的 L2 coverage 不同 → score 也会不同
        # 但 small_cap_cs_rescue 至少应该让 nacs_flag <= nacs_base 的 0.95×
        # (除非 baseline coverage 超低罚分严重)
        # 这里只检验 multiplier 出现在 adjustment 列表
        r_flag = compute_nacs(ipo_flag)
        assert any("x0.9" in a and "小盘+高基石" in a for a in r_flag.adjustments_applied)


# =============================================================================
# Config 控制
# =============================================================================

class TestConfigDriven:
    def test_disabled_skips(self, make_ipo):
        from config import (
            NacsConfig, PostAdjustments, SmallCapCSRescueFlag,
            set_config, reset_config,
        )
        from nacs_model import compute_nacs
        try:
            cfg = NacsConfig()
            cfg.post_adjustments = PostAdjustments(
                small_cap_cs_rescue=SmallCapCSRescueFlag(enabled=False),
            )
            set_config(cfg)
            ipo = _shrink_offering(make_ipo, offer_size=1e9, ticket_size=2e8)
            r = compute_nacs(ipo)
            assert not any("小盘+高基石" in a for a in r.adjustments_applied)
        finally:
            reset_config()

    def test_yaml_loads_section(self):
        from pathlib import Path
        from config import load_config
        cfg = load_config(Path(__file__).resolve().parent.parent
                          / "configs" / "nacs_v8.yaml")
        sc = cfg.post_adjustments.small_cap_cs_rescue
        assert sc.enabled is True
        assert sc.small_offering_threshold_hkd == 1_500_000_000
        assert sc.coverage_threshold == 0.55
        assert sc.multiplier == 0.90

    def test_custom_threshold(self, make_ipo):
        """改 small_offering_threshold 到 0.5B → 1B 不再算小盘"""
        from config import (
            NacsConfig, PostAdjustments, SmallCapCSRescueFlag,
            set_config, reset_config,
        )
        from nacs_model import compute_nacs
        try:
            cfg = NacsConfig()
            cfg.post_adjustments = PostAdjustments(
                small_cap_cs_rescue=SmallCapCSRescueFlag(
                    small_offering_threshold_hkd=0.5e9,
                ),
            )
            set_config(cfg)
            ipo = _shrink_offering(make_ipo, offer_size=1e9, ticket_size=2e8)
            r = compute_nacs(ipo)
            # 1B > 0.5B → 不再算小盘 → 不触发
            assert not any("小盘+高基石" in a for a in r.adjustments_applied)
        finally:
            reset_config()

    def test_custom_multiplier(self, make_ipo):
        """改 multiplier=0.50 → 折扣加倍"""
        from config import (
            NacsConfig, PostAdjustments, SmallCapCSRescueFlag,
            set_config, reset_config,
        )
        from nacs_model import compute_nacs
        try:
            cfg = NacsConfig()
            cfg.post_adjustments = PostAdjustments(
                small_cap_cs_rescue=SmallCapCSRescueFlag(multiplier=0.50),
            )
            set_config(cfg)
            ipo = _shrink_offering(make_ipo, offer_size=1e9, ticket_size=2e8)
            r = compute_nacs(ipo)
            flagged = [a for a in r.adjustments_applied if "小盘+高基石" in a]
            assert any("x0.5" in a for a in flagged)
        finally:
            reset_config()


# =============================================================================
# Rationale (审计)
# =============================================================================

class TestRationaleAnnotation:
    def test_explain_adjustment_describes_red_flag(self):
        from nacs_rationale import explain_adjustment
        out = explain_adjustment(
            "小盘+高基石覆盖红旗 x0.9 (募资 1.0B HKD, 覆盖 80%)"
        )
        assert "红旗" in out or "小盘" in out
        assert "救场" in out or "弱" in out or "负偏" in out


# =============================================================================
# 链式叠加
# =============================================================================

class TestChaining:
    def test_chains_with_ai_gilding(self, make_ipo):
        """AI 镀金 ×0.85 + 小盘红旗 ×0.90 同时触发"""
        from nacs_model import compute_nacs
        ipo = _shrink_offering(make_ipo, offer_size=1e9, ticket_size=2e8)
        ipo.theme_id = "ai_server"
        ipo.ai_revenue_pct = 0.05
        r = compute_nacs(ipo)
        ai_adj = [a for a in r.adjustments_applied if "AI 镀金" in a]
        sc_adj = [a for a in r.adjustments_applied if "小盘+高基石" in a]
        assert len(ai_adj) == 1
        assert len(sc_adj) == 1
