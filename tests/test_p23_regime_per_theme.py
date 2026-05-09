"""
P2.3 — regime_score per-theme 测试.

新函数: compute_regime_score_for_theme(history_with_theme, current_date, theme_id)

跟 compute_regime_score 用同样的窗口 (lookback / min_lag), 但样本只取
同 theme_id 的 listed IPO 子集.

覆盖:
    - theme_id=None → 退化到 panel 全量 (跟旧 compute_regime_score 等价)
    - 同主题样本充足 → 返回 (median(theme_returns), theme_id)
    - 同主题样本 < theme_min_sample + fallback_to_panel=True → 退回 panel
    - 同主题样本不足 + fallback_to_panel=False → (None, theme_id)
    - 时间窗过滤: 窗口外的不入样本
    - panel 也不足 → (None, None)
    - cfg.per_theme_min_sample 改动立即生效
    - cfg.fallback_to_panel 改动立即生效
    - yaml: regime_gate per_theme_* 字段被解析
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest


def _build_history(theme_returns, current_date, days_ago=60):
    """构造 history_with_theme: 所有 IPO 都落在 [d-120, d-30] 窗口里"""
    listing_d = current_date - timedelta(days=days_ago)
    return [(listing_d, ret, theme) for (ret, theme) in theme_returns]


# =============================================================================
# 基本行为
# =============================================================================

class TestPerThemeRegime:
    def test_theme_id_none_falls_back_to_panel(self):
        from nacs_model import compute_regime_score_for_theme
        cd = date(2026, 5, 9)
        hist = _build_history([
            (0.10, "ai_server"),
            (0.20, "innovative_drug"),
            (0.05, "ai_server"),
            (-0.05, None),
            (0.15, "ai_server"),
        ], cd)
        score, theme_used = compute_regime_score_for_theme(hist, cd, theme_id=None)
        assert theme_used is None
        # panel 中位 = median of [0.10, 0.20, 0.05, -0.05, 0.15] = 0.10
        assert score == pytest.approx(0.10, abs=0.001)

    def test_theme_match_uses_theme_subset(self):
        from nacs_model import compute_regime_score_for_theme
        cd = date(2026, 5, 9)
        # 5 个 ai_server (0.10, 0.20, 0.30, 0.40, 0.50 → median=0.30)
        # + 多只其他主题 (噪音, 不应入计算)
        hist = _build_history([
            (0.10, "ai_server"), (0.20, "ai_server"),
            (0.30, "ai_server"), (0.40, "ai_server"),
            (0.50, "ai_server"),
            (-0.50, "innovative_drug"),
            (-0.50, "innovative_drug"),
        ], cd)
        score, theme_used = compute_regime_score_for_theme(hist, cd, theme_id="ai_server")
        assert theme_used == "ai_server"
        assert score == pytest.approx(0.30, abs=0.001)

    def test_theme_below_min_sample_fallback_to_panel(self):
        """同主题 < 5 → fallback 到 panel"""
        from nacs_model import compute_regime_score_for_theme
        cd = date(2026, 5, 9)
        # 只有 2 个 ai_server, 但 panel 全量有 6 个 → fallback
        hist = _build_history([
            (0.50, "ai_server"), (0.40, "ai_server"),
            (0.10, "innovative_drug"), (0.20, "innovative_drug"),
            (0.30, "innovative_drug"), (0.40, "innovative_drug"),
        ], cd)
        score, theme_used = compute_regime_score_for_theme(
            hist, cd, theme_id="ai_server",
            theme_min_sample=5, fallback_to_panel=True,
        )
        # fallback 走 panel: median([0.50, 0.40, 0.10, 0.20, 0.30, 0.40]) ~= 0.35
        assert theme_used is None   # fallback 标识
        assert score == pytest.approx(0.35, abs=0.01)

    def test_theme_below_min_sample_no_fallback_returns_none(self):
        """同主题 < 5 + fallback 关闭 → (None, theme_id)"""
        from nacs_model import compute_regime_score_for_theme
        cd = date(2026, 5, 9)
        hist = _build_history([
            (0.50, "ai_server"), (0.40, "ai_server"),
            (0.10, "innovative_drug"), (0.20, "innovative_drug"),
            (0.30, "innovative_drug"), (0.40, "innovative_drug"),
        ], cd)
        score, theme_used = compute_regime_score_for_theme(
            hist, cd, theme_id="ai_server",
            theme_min_sample=5, fallback_to_panel=False,
        )
        assert score is None
        assert theme_used == "ai_server"   # 标识"用了主题但样本不足"

    def test_panel_also_insufficient_returns_none(self):
        """主题 < 5 + fallback panel 也 < min_sample → (None, None)"""
        from nacs_model import compute_regime_score_for_theme
        cd = date(2026, 5, 9)
        # 总共 3 个 IPO, panel min_sample=5 也不够
        hist = _build_history([
            (0.10, "ai_server"),
            (0.20, "innovative_drug"),
            (0.30, None),
        ], cd)
        score, theme_used = compute_regime_score_for_theme(
            hist, cd, theme_id="ai_server",
            theme_min_sample=5, fallback_to_panel=True,
        )
        assert score is None
        assert theme_used is None


# =============================================================================
# 时间窗过滤
# =============================================================================

class TestWindowFilter:
    def test_excludes_outside_window(self):
        """窗口外 (d>cutoff_recent 或 d<cutoff_old) 的 IPO 不入样本"""
        from nacs_model import compute_regime_score_for_theme
        cd = date(2026, 5, 9)
        # default lookback=120, min_lag=30 → window = [-120d, -30d]
        # 制造 5 个窗口内 ai_server + 10 个窗口外 ai_server (太久前 / 太近)
        in_window = [(cd - timedelta(days=60), 0.10, "ai_server")] * 5
        too_old = [(cd - timedelta(days=200), -0.50, "ai_server")] * 5
        too_recent = [(cd - timedelta(days=10), 1.00, "ai_server")] * 5
        hist = in_window + too_old + too_recent
        score, theme = compute_regime_score_for_theme(
            hist, cd, theme_id="ai_server",
        )
        assert theme == "ai_server"
        # 只有 in_window 的 0.10 入计算
        assert score == pytest.approx(0.10, abs=0.001)


# =============================================================================
# Config-driven
# =============================================================================

class TestConfigDriven:
    def test_yaml_loads_per_theme_fields(self):
        from pathlib import Path
        from config import load_config
        cfg = load_config(Path(__file__).resolve().parent.parent
                          / "configs" / "nacs_v8.yaml")
        rg = cfg.regime_gate
        assert rg.per_theme_enabled is False
        assert rg.per_theme_min_sample == 5
        assert rg.fallback_to_panel is True

    def test_custom_min_sample_via_config(self):
        """改 per_theme_min_sample=2 → 同主题 2 个就够"""
        from config import NacsConfig, RegimeGateConfig, set_config, reset_config
        from nacs_model import compute_regime_score_for_theme
        try:
            cfg = NacsConfig()
            cfg.regime_gate = RegimeGateConfig(per_theme_min_sample=2)
            set_config(cfg)
            cd = date(2026, 5, 9)
            hist = _build_history([
                (0.10, "ai_server"), (0.30, "ai_server"),   # 2 个 → 满足 min=2
                (0.50, "innovative_drug"), (0.40, "innovative_drug"),
                (0.20, "innovative_drug"),
            ], cd)
            score, theme = compute_regime_score_for_theme(hist, cd, theme_id="ai_server")
            assert theme == "ai_server"
            # median(0.10, 0.30) = 0.20
            assert score == pytest.approx(0.20, abs=0.001)
        finally:
            reset_config()

    def test_custom_fallback_disabled_via_config(self):
        from config import NacsConfig, RegimeGateConfig, set_config, reset_config
        from nacs_model import compute_regime_score_for_theme
        try:
            cfg = NacsConfig()
            cfg.regime_gate = RegimeGateConfig(
                per_theme_min_sample=10,    # 故意拉高让主题不够
                fallback_to_panel=False,
            )
            set_config(cfg)
            cd = date(2026, 5, 9)
            hist = _build_history([
                (0.10, "ai_server"), (0.30, "ai_server"),
                (0.50, "innovative_drug"),
                (0.40, "innovative_drug"),
                (0.20, "innovative_drug"),
                (0.05, "innovative_drug"),
                (0.15, "innovative_drug"),
            ], cd)
            score, theme = compute_regime_score_for_theme(hist, cd, theme_id="ai_server")
            # fallback 关闭 + 主题样本不足 → (None, theme_id)
            assert score is None
            assert theme == "ai_server"
        finally:
            reset_config()


# =============================================================================
# 跟旧 compute_regime_score 一致性
# =============================================================================

class TestPanelEquivalence:
    def test_theme_none_matches_legacy_function(self):
        """theme_id=None 时返回值跟 compute_regime_score(原版) 数值一致"""
        from nacs_model import compute_regime_score, compute_regime_score_for_theme
        cd = date(2026, 5, 9)
        history_legacy = [
            (cd - timedelta(days=60), 0.10),
            (cd - timedelta(days=70), 0.20),
            (cd - timedelta(days=80), 0.05),
            (cd - timedelta(days=90), -0.05),
            (cd - timedelta(days=100), 0.15),
        ]
        history_with_theme = [(d, r, "ai_server") for (d, r) in history_legacy]
        legacy = compute_regime_score(history_legacy, cd)
        new_score, _ = compute_regime_score_for_theme(
            history_with_theme, cd, theme_id=None,
        )
        assert legacy == pytest.approx(new_score, abs=0.001)
