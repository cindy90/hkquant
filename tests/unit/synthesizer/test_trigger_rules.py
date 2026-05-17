"""Tests for synthesizer/trigger_rules.py."""

from __future__ import annotations

from decimal import Decimal

from hk_ipo_agent.agents.workflow_extras import WorkflowExtras
from hk_ipo_agent.common.enums import AlertLevel, DecisionType
from hk_ipo_agent.common.schemas import (
    SingleModelValuation,
    ValuationDistribution,
    ValuationEnsembleOutput,
)
from hk_ipo_agent.synthesizer.trigger_rules import build_trigger_rules


def _ensemble() -> ValuationEnsembleOutput:
    dist = ValuationDistribution(
        p10=Decimal("90"),
        p25=Decimal("95"),
        p50=Decimal("100"),
        p75=Decimal("105"),
        p90=Decimal("110"),
        mean=Decimal("100"),
        std=Decimal("5"),
    )
    return ValuationEnsembleOutput(
        company_id="C",
        single_models=[
            SingleModelValuation(model_name="x", applicable=True, valuation_distribution=dist)
        ],
        weights_used={"x": 1.0},
        ensemble_distribution=dist,
        implied_price_range={"low": Decimal("95"), "fair": Decimal("100"), "high": Decimal("105")},
    )


def test_baseline_rules_present() -> None:
    rules = build_trigger_rules(
        decision_type=DecisionType.PARTICIPATE,
        ensemble=_ensemble(),
        extras=WorkflowExtras(),
    )
    actions = " ".join(r.action + " " + r.condition for r in rules)
    assert "price_drop_pct_30d" in actions
    assert "earnings_release" in actions
    assert "cornerstone_disclosure" in actions
    # Critical-level rule must exist for severe drops
    assert any(r.severity == AlertLevel.CRITICAL for r in rules)


def test_gilding_adds_quarterly_rule() -> None:
    rules = build_trigger_rules(
        decision_type=DecisionType.PARTICIPATE,
        ensemble=_ensemble(),
        extras=WorkflowExtras(ai_gilding_flag=True),
    )
    assert any("AI revenue share < 10%" in r.condition for r in rules)


def test_regime_skip_adds_revert_rule() -> None:
    rules = build_trigger_rules(
        decision_type=DecisionType.SKIP,
        ensemble=_ensemble(),
        extras=WorkflowExtras(regime_score=-0.10),
    )
    assert any("regime_score turns" in r.condition for r in rules)


def test_no_gilding_no_extra_rule() -> None:
    rules_with = build_trigger_rules(
        decision_type=DecisionType.PARTICIPATE,
        ensemble=_ensemble(),
        extras=WorkflowExtras(ai_gilding_flag=True),
    )
    rules_without = build_trigger_rules(
        decision_type=DecisionType.PARTICIPATE,
        ensemble=_ensemble(),
        extras=WorkflowExtras(ai_gilding_flag=False),
    )
    assert len(rules_with) == len(rules_without) + 1
