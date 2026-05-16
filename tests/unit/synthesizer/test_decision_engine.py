"""Tests for synthesizer/decision_engine.py — hard rules + soft thresholds."""

from __future__ import annotations

from decimal import Decimal

from hk_ipo_agent.agents.workflow_extras import WorkflowExtras
from hk_ipo_agent.common.enums import AgentRole, DecisionType
from hk_ipo_agent.common.schemas import (
    AgentOutput,
    SingleModelValuation,
    ValuationDistribution,
    ValuationEnsembleOutput,
)
from hk_ipo_agent.synthesizer.decision_engine import decide


def _stub_dist(p50: Decimal) -> ValuationDistribution:
    return ValuationDistribution(
        p10=p50 - Decimal("10"),
        p25=p50 - Decimal("5"),
        p50=p50,
        p75=p50 + Decimal("5"),
        p90=p50 + Decimal("10"),
        mean=p50,
        std=Decimal("5"),
    )


def _ensemble(applicable: bool = True) -> ValuationEnsembleOutput:
    dist = _stub_dist(Decimal("100"))
    return ValuationEnsembleOutput(
        company_id="C-T",
        single_models=[
            SingleModelValuation(
                model_name="comparable",
                applicable=applicable,
                valuation_distribution=dist,
            )
        ],
        weights_used={"comparable": 1.0} if applicable else {},
        ensemble_distribution=dist,
        implied_price_range={
            "low": Decimal("95"),
            "fair": Decimal("100"),
            "high": Decimal("105"),
        },
    )


def _ao(role: AgentRole, overall: float, narrative_risk: float = 0.0) -> AgentOutput:
    return AgentOutput(
        agent_role=role,
        scores={"narrative_risk": narrative_risk} if narrative_risk else {"x": overall},
        overall_score=overall,
        runtime_seconds=0.1,
    )


def test_regime_gate_forces_skip() -> None:
    gate = decide(
        overall_score=80.0,
        extras=WorkflowExtras(regime_score=-0.05),
        ensemble=_ensemble(),
        agent_outputs={"fundamental": _ao(AgentRole.FUNDAMENTAL, 80.0)},
    )
    assert gate.decision == DecisionType.SKIP
    assert gate.hard_reason is not None
    assert "regime_score" in gate.hard_reason


def test_no_applicable_models_forces_wait() -> None:
    gate = decide(
        overall_score=80.0,
        extras=WorkflowExtras(),
        ensemble=_ensemble(applicable=False),
        agent_outputs={},
    )
    assert gate.decision == DecisionType.WAIT_FOR_SIGNAL
    assert gate.hard_reason is not None


def test_ai_gilding_with_high_narrative_risk_forces_skip() -> None:
    gate = decide(
        overall_score=80.0,
        extras=WorkflowExtras(ai_gilding_flag=True),
        ensemble=_ensemble(),
        agent_outputs={"sentiment": _ao(AgentRole.SENTIMENT, 50.0, narrative_risk=85.0)},
    )
    assert gate.decision == DecisionType.SKIP
    assert "ai_gilding" in (gate.hard_reason or "")


def test_high_score_participate() -> None:
    gate = decide(
        overall_score=80.0,
        extras=WorkflowExtras(),
        ensemble=_ensemble(),
        agent_outputs={},
    )
    assert gate.decision == DecisionType.PARTICIPATE
    assert gate.suggested_allocation_pct is not None
    assert 0.04 <= gate.suggested_allocation_pct <= 0.07


def test_mid_score_partial() -> None:
    gate = decide(
        overall_score=65.0,
        extras=WorkflowExtras(),
        ensemble=_ensemble(),
        agent_outputs={},
    )
    assert gate.decision == DecisionType.PARTIAL
    assert gate.suggested_allocation_pct is not None
    assert 0.01 <= gate.suggested_allocation_pct <= 0.03


def test_low_score_wait() -> None:
    gate = decide(
        overall_score=50.0,
        extras=WorkflowExtras(),
        ensemble=_ensemble(),
        agent_outputs={},
    )
    assert gate.decision == DecisionType.WAIT_FOR_SIGNAL
    assert gate.suggested_allocation_pct is None


def test_very_low_score_skip() -> None:
    gate = decide(
        overall_score=20.0,
        extras=WorkflowExtras(),
        ensemble=_ensemble(),
        agent_outputs={},
    )
    assert gate.decision == DecisionType.SKIP
    assert gate.hard_reason is None  # soft skip, no hard reason
