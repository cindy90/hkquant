"""Tests for `hk_ipo_agent.common.schemas` — core Pydantic models."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from hk_ipo_agent.common.enums import (
    AdjustmentStatus,
    AgentRole,
    AlertLevel,
    Confidence,
    DecisionType,
    EarningsAssessment,
    EventSeverity,
    IPOLifecycleStateType,
    ListingType,
    PostIPOEventType,
    TransitionTrigger,
)
from hk_ipo_agent.common.schemas import (
    AgentOutput,
    Alert,
    Citation,
    DebateOutput,
    EarningsComparison,
    FinalDecision,
    Finding,
    IPOLifecycleState,
    PostIPOEvent,
    PredictionSnapshot,
    ProspectusExtraction,
    StateTransition,
    ValuationDistribution,
    ValuationEnsembleOutput,
)

# ---------------------------------------------------------------------------
# Citation / Finding (constraints around mandatory citations)
# ---------------------------------------------------------------------------


def test_citation_requires_page_ge_1() -> None:
    Citation(page=1)  # OK
    with pytest.raises(ValidationError):
        Citation(page=0)
    with pytest.raises(ValidationError):
        Citation(page=-5)


def test_finding_requires_at_least_one_citation() -> None:
    """Per CLAUDE.md, every Finding must carry >= 1 citation."""
    c = Citation(page=42)
    Finding(statement="x", evidence="y", citations=[c], confidence=Confidence.HIGH)  # OK
    with pytest.raises(ValidationError):
        Finding(statement="x", evidence="y", citations=[], confidence=Confidence.HIGH)


def test_agent_output_strict_overall_score_bounds() -> None:
    with pytest.raises(ValidationError):
        AgentOutput(
            agent_role=AgentRole.FUNDAMENTAL,
            scores={},
            overall_score=120.0,  # over 100
            cost_usd=Decimal("0"),
            runtime_seconds=0.1,
        )


# ---------------------------------------------------------------------------
# Extra="forbid" — typos cause hard errors
# ---------------------------------------------------------------------------


def test_extra_forbid_blocks_typos() -> None:
    with pytest.raises(ValidationError):
        Citation(page=1, pgae=99)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# PredictionSnapshot is frozen
# ---------------------------------------------------------------------------


def _stub_prospectus_extraction() -> ProspectusExtraction:
    return ProspectusExtraction(
        prospectus_id="P-1",
        company_name_zh="测试公司",
        listing_type=ListingType.MAINBOARD_TECH,
        industry_code="TECH",
        industry_description="x",
        business_model="x",
        extraction_version="0.0.1",
        extracted_at=datetime.now(UTC),
    )


def _stub_valuation_distribution() -> ValuationDistribution:
    return ValuationDistribution(
        p10=Decimal("10"),
        p25=Decimal("11"),
        p50=Decimal("12"),
        p75=Decimal("13"),
        p90=Decimal("14"),
        mean=Decimal("12"),
        std=Decimal("1"),
    )


def _stub_final_decision() -> FinalDecision:
    d = _stub_valuation_distribution()
    return FinalDecision(
        decision=DecisionType.SKIP,
        confidence=0.5,
        price_range_low=Decimal("10"),
        price_range_fair=Decimal("12"),
        price_range_high=Decimal("14"),
        expected_return_6m=d,
        expected_return_12m=d,
    )


def test_prediction_snapshot_is_frozen() -> None:
    snap = PredictionSnapshot(
        id=uuid4(),
        ipo_id=uuid4(),
        as_of_date=datetime.now(UTC).date(),
        prospectus_version="PHIP",
        input_data_hash="0" * 64,
        input_data_snapshot={},
        agent_outputs={},
        valuation_output=ValuationEnsembleOutput(
            company_id="C-1",
            single_models=[],
            weights_used={},
            ensemble_distribution=_stub_valuation_distribution(),
            implied_price_range={
                "low": Decimal("10"),
                "fair": Decimal("12"),
                "high": Decimal("14"),
            },
        ),
        debate_output=DebateOutput(final_consensus="ok"),
        decision=_stub_final_decision(),
        system_version="v0.0.1",
        model_versions={},
        config_snapshot={},
        total_cost_usd=Decimal("0"),
        runtime_seconds=1.0,
        created_at=datetime.now(UTC),
    )
    with pytest.raises(ValidationError):
        snap.system_version = "tampered"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Alert MUST have actionable_info (CLAUDE.md v1.2 constraint)
# ---------------------------------------------------------------------------


def test_alert_requires_actionable_info() -> None:
    with pytest.raises(ValidationError):
        Alert(  # type: ignore[call-arg]
            level=AlertLevel.CRITICAL,
            category="datasource.degraded",
            message="iFind timing out",
            detected_at=datetime.now(UTC),
            # actionable_info intentionally omitted
        )


def test_alert_valid_with_actionable_info() -> None:
    a = Alert(
        level=AlertLevel.CRITICAL,
        category="datasource.degraded",
        message="iFind timing out",
        actionable_info="Check VPN; restart ifind_client; failover to manual_pending.",
        detected_at=datetime.now(UTC),
    )
    assert a.actionable_info.startswith("Check")


# ---------------------------------------------------------------------------
# State transition + lifecycle
# ---------------------------------------------------------------------------


def test_state_transition_minimal() -> None:
    t = StateTransition(
        ipo_id=uuid4(),
        from_state=IPOLifecycleStateType.PRE_LISTING,
        to_state=IPOLifecycleStateType.PRICING,
        transition_at=datetime.now(UTC),
        triggered_by=TransitionTrigger.AUTO_DETECTOR,
    )
    assert t.to_state == IPOLifecycleStateType.PRICING


def test_lifecycle_state_terminal_flag_typed() -> None:
    s = IPOLifecycleState(
        ipo_id=uuid4(),
        current_state=IPOLifecycleStateType.TERMINATED,
        state_entered_at=datetime.now(UTC),
        last_checked_at=datetime.now(UTC),
        is_terminal=True,
    )
    assert s.is_terminal


# ---------------------------------------------------------------------------
# Earnings comparison enum
# ---------------------------------------------------------------------------


def test_earnings_comparison_enum_valid() -> None:
    ec = EarningsComparison(
        snapshot_id=uuid4(),
        report_period="FY2025",
        filing_date=datetime.now(UTC).date(),
        overall_assessment=EarningsAssessment.BEAT,
        confidence=Confidence.HIGH,
    )
    assert ec.overall_assessment == EarningsAssessment.BEAT


def test_post_ipo_event_severity() -> None:
    e = PostIPOEvent(
        event_date=datetime.now(UTC).date(),
        event_type=PostIPOEventType.EARNINGS,
        severity=EventSeverity.CRITICAL,
        description="Profit warning",
    )
    assert e.severity == EventSeverity.CRITICAL


# ---------------------------------------------------------------------------
# Adjustment status workflow
# ---------------------------------------------------------------------------


def test_adjustment_status_values() -> None:
    assert AdjustmentStatus.PROPOSED.value == "proposed"
    assert AdjustmentStatus.IMPLEMENTED.value == "implemented"
