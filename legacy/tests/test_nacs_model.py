"""
nacs_model 关键行为回归 (从 check_health.py 移植 + 扩展)

覆盖:
    T1 baseline 评分非 SKIP
    T2 regime_score < 0 强制 SKIP
    T3 cluster bonus 提升 Q_ecosystem
    T4 compute_regime_score 计算正确
    T5 v8 RELATIONSHIP 仓位 = 0
    + 边界: 全 NULL cornerstones, 极端 cluster, 各 position band 命中
"""
from __future__ import annotations

from datetime import date

import pytest


# =============================================================================
# T1-T5 (从 check_health.py 移植)
# =============================================================================

def test_T1_baseline_not_skip(make_ipo):
    from nacs_model import compute_nacs
    r = compute_nacs(make_ipo())
    assert r.decision != "SKIP"
    assert 0.0 < r.nacs_adjusted <= 1.0


def test_T2_regime_negative_forces_skip(make_ipo):
    from nacs_model import compute_nacs
    r = compute_nacs(make_ipo(regime=-0.05))
    assert r.decision == "SKIP"


def test_T3_cluster_bonus_lifts_q_ecosystem(make_ipo):
    from nacs_model import compute_nacs
    r0 = compute_nacs(make_ipo(cluster=0))
    r3 = compute_nacs(make_ipo(cluster=3))
    r5 = compute_nacs(make_ipo(cluster=5))
    assert r3.Q_ecosystem >= r0.Q_ecosystem
    assert r5.Q_ecosystem >= r3.Q_ecosystem


def test_T4_compute_regime_score():
    from nacs_model import compute_regime_score
    hist = [
        (date(2025, 1, 1), 0.10), (date(2025, 2, 1), 0.05),
        (date(2025, 2, 15), -0.02), (date(2025, 3, 1), 0.08),
        (date(2025, 3, 15), 0.06), (date(2025, 4, 1), 0.04),
        (date(2025, 4, 15), 0.09), (date(2025, 5, 1), 0.11),
        (date(2025, 5, 15), 0.07), (date(2025, 5, 25), 0.03),
    ]
    score = compute_regime_score(hist, date(2025, 7, 1))
    assert score is not None
    # 这批样本 30 日中位 ~5%, 应为正
    assert score > 0


def test_T5_v8_relationship_position_zero():
    """v8 关键: NACS [0.25, 0.35) → RELATIONSHIP 标签 + 仓位 0"""
    from nacs_model import _position_from_nacs
    pos, dec = _position_from_nacs(0.30)
    assert dec == "RELATIONSHIP"
    assert pos == 0.0


# =============================================================================
# 边界扩展
# =============================================================================

@pytest.mark.parametrize("nacs,exp_dec,exp_pos", [
    (0.99, "FULL", 1.00),
    (0.55, "FULL", 1.00),
    (0.45, "LARGE", 0.70),
    (0.35, "TRIAL", 0.40),
    (0.25, "RELATIONSHIP", 0.00),
    (0.10, "SKIP", 0.00),
    (0.00, "SKIP", 0.00),
])
def test_position_bands_complete(nacs, exp_dec, exp_pos):
    from nacs_model import _position_from_nacs
    pos, dec = _position_from_nacs(nacs)
    assert dec == exp_dec
    assert pos == exp_pos


def test_regime_score_returns_none_on_too_few_samples():
    from nacs_model import compute_regime_score
    # 只有 2 个样本, 默认 min_sample=5 → None
    hist = [(date(2025, 1, 1), 0.1), (date(2025, 2, 1), 0.2)]
    assert compute_regime_score(hist, date(2025, 6, 1)) is None


def test_regime_score_filters_lookahead():
    """窗口外 (含 asof 之后) 的样本必须被剔除 (防 look-ahead)"""
    from datetime import timedelta
    from nacs_model import compute_regime_score
    asof = date(2025, 6, 1)
    # 窗口 = [asof-120, asof-30] = [2025-02-01, 2025-05-02]
    in_window = asof - timedelta(days=60)   # 2025-04-02
    after = asof + timedelta(days=10)        # 越界 (look-ahead)
    too_old = asof - timedelta(days=200)     # 越界 (太久远)
    hist = [(in_window, 0.05)] * 8 + [(after, 1.00)] * 5 + [(too_old, -0.50)] * 5
    score = compute_regime_score(hist, asof)
    assert score is not None
    assert abs(score - 0.05) < 1e-6


def test_cluster_bonus_table_descending():
    """v7 cluster bonus 必须按 threshold 降序 (查表逻辑要求)"""
    from nacs_model import CLUSTER_BONUS_TABLE
    thresholds = [t for t, _ in CLUSTER_BONUS_TABLE]
    assert thresholds == sorted(thresholds, reverse=True)


def test_compute_nacs_idempotent(make_ipo):
    """同一 IPOOffering 多次 compute 结果完全一致"""
    from nacs_model import compute_nacs
    ipo = make_ipo()
    r1 = compute_nacs(ipo)
    r2 = compute_nacs(ipo)
    assert r1.nacs_adjusted == r2.nacs_adjusted
    assert r1.decision == r2.decision
    assert r1.Q_company == r2.Q_company
    assert r1.Q_ecosystem == r2.Q_ecosystem
