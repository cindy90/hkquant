"""Tests for synthesizer/scoring.py — overall scorecard with NACS modifiers."""

from __future__ import annotations

from hk_ipo_agent.agents.workflow_extras import WorkflowExtras
from hk_ipo_agent.common.enums import AgentRole
from hk_ipo_agent.common.schemas import AgentOutput
from hk_ipo_agent.synthesizer.scoring import build_scorecard


def _ao(role: AgentRole, overall: float) -> AgentOutput:
    return AgentOutput(
        agent_role=role,
        scores={"x": overall},
        overall_score=overall,
        runtime_seconds=0.1,
    )


def test_scorecard_base_average() -> None:
    outs = {
        "fundamental": _ao(AgentRole.FUNDAMENTAL, 60.0),
        "industry": _ao(AgentRole.INDUSTRY, 80.0),
    }
    extras = WorkflowExtras()
    sc = build_scorecard(outs, extras)
    assert sc["base_avg"] == 70.0
    # ADR 0020: overall = base + Σadj (no 0.6 compression).
    assert sc["overall"] == 70.0


def test_scorecard_positive_regime_boost() -> None:
    outs = {"fundamental": _ao(AgentRole.FUNDAMENTAL, 70.0)}
    extras = WorkflowExtras(regime_score=0.10)
    sc = build_scorecard(outs, extras)
    assert sc["regime_adj"] == 10.0  # min(20, 0.10 * 100)
    # ADR 0020: overall = base + adj = 70 + 10 = 80.
    assert sc["overall"] == 80.0


def test_scorecard_negative_regime_penalty() -> None:
    outs = {"fundamental": _ao(AgentRole.FUNDAMENTAL, 70.0)}
    extras = WorkflowExtras(regime_score=-0.30)
    sc = build_scorecard(outs, extras)
    assert sc["regime_adj"] == -20.0  # max(-20, -0.30 * 100) = -20 (capped)
    # ADR 0020: overall = 70 + (-20) = 50.
    assert sc["overall"] == 50.0


def test_scorecard_cluster_bonus() -> None:
    outs = {"fundamental": _ao(AgentRole.FUNDAMENTAL, 50.0)}
    extras = WorkflowExtras(cluster_bonus_multiplier=1.20)
    sc = build_scorecard(outs, extras)
    # 1.20x cluster → 5.0 * log(1.20)/log(1.20) = 5.0
    assert sc["cluster_adj"] == 5.0


def test_scorecard_gilding_penalty() -> None:
    outs = {"fundamental": _ao(AgentRole.FUNDAMENTAL, 70.0)}
    extras = WorkflowExtras(ai_gilding_flag=True)
    sc = build_scorecard(outs, extras)
    assert sc["gilding_adj"] == -10.0


def test_scorecard_theme_heat_modifiers() -> None:
    outs = {"fundamental": _ao(AgentRole.FUNDAMENTAL, 70.0)}
    hot = build_scorecard(outs, WorkflowExtras(theme_heat=0.85))
    cold = build_scorecard(outs, WorkflowExtras(theme_heat=0.15))
    neutral = build_scorecard(outs, WorkflowExtras(theme_heat=0.50))
    assert hot["theme_adj"] == 5.0
    assert cold["theme_adj"] == -5.0
    assert neutral["theme_adj"] == 0.0


def test_scorecard_overall_clamped_to_0_100() -> None:
    outs = {"fundamental": _ao(AgentRole.FUNDAMENTAL, 100.0)}
    extras = WorkflowExtras(
        regime_score=0.50,
        cluster_bonus_multiplier=1.20,
        theme_heat=0.90,
    )
    sc = build_scorecard(outs, extras)
    assert 0.0 <= sc["overall"] <= 100.0
    # Upper clamp must kick in: base=100 + (+20 regime + 5 cluster + 5 theme) = 130 → 100.
    assert sc["overall"] == 100.0


def test_scorecard_adr_0020_regression_dobot() -> None:
    """Regression: 越疆 2432.HK with base≈44 + zero adj must NOT be SKIP.

    Pre-ADR-0020 formula (base*0.6 + Σadj) gave overall=26.57 → SKIP.
    Per decision_engine.py thresholds (<45 = SKIP, ≥45 = WAIT_FOR_SIGNAL),
    the corrected formula must put a base≈44 IPO in the WAIT band, not
    SKIP, when no NACS modifiers apply. See ADR 0020 §Evidence.
    """
    # Replicate the 越疆 agent_outputs distribution that produced base 44.29.
    outs = {
        "policy": _ao(AgentRole.POLICY, 33.33),
        "industry": _ao(AgentRole.INDUSTRY, 53.33),
        "liquidity": _ao(AgentRole.LIQUIDITY, 51.67),
        "sentiment": _ao(AgentRole.SENTIMENT, 30.0),
        "valuation": _ao(AgentRole.VALUATION, 50.0),
        "fundamental": _ao(AgentRole.FUNDAMENTAL, 58.33),
        "cornerstone_signal": _ao(AgentRole.CORNERSTONE_SIGNAL, 33.33),
    }
    extras = WorkflowExtras()  # all adj = 0 (matches 越疆 InMemory state)
    sc = build_scorecard(outs, extras)
    assert round(sc["base_avg"], 2) == 44.28
    # Post-fix: overall == base_avg when adj are all zero.
    assert sc["overall"] == sc["base_avg"]
    # And critically, overall ≥ 44 keeps the IPO out of the <45 SKIP band.
    assert sc["overall"] >= 44.0
