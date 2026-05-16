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
    assert sc["overall"] == 42.0  # 70 × 0.6 + 0 modifiers


def test_scorecard_positive_regime_boost() -> None:
    outs = {"fundamental": _ao(AgentRole.FUNDAMENTAL, 70.0)}
    extras = WorkflowExtras(regime_score=0.10)
    sc = build_scorecard(outs, extras)
    assert sc["regime_adj"] == 10.0  # min(20, 0.10 * 100)
    # base_avg = 70, overall = 70*0.6 + 10 = 52


def test_scorecard_negative_regime_penalty() -> None:
    outs = {"fundamental": _ao(AgentRole.FUNDAMENTAL, 70.0)}
    extras = WorkflowExtras(regime_score=-0.30)
    sc = build_scorecard(outs, extras)
    assert sc["regime_adj"] == -20.0  # max(-20, -0.30 * 100) = -20 (capped)


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
