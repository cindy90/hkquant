"""Tests for prediction_registry/snapshot.py — hash integrity + immutability."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from hk_ipo_agent.common.enums import AgentRole, DecisionType, ListingType
from hk_ipo_agent.common.schemas import (
    AgentOutput,
    DebateOutput,
    DebateRound,
    FinalDecision,
    ProspectusExtraction,
    SingleModelValuation,
    ValuationDistribution,
    ValuationEnsembleOutput,
)
from hk_ipo_agent.prediction_registry.snapshot import (
    SnapshotIntegrityError,
    build_snapshot,
    compute_input_hash,
    verify_snapshot,
)


def _ext() -> ProspectusExtraction:
    return ProspectusExtraction(
        prospectus_id="P-SNAP-1",
        company_name_zh="测试",
        listing_type=ListingType.MAINBOARD_TECH,
        industry_code="TECH",
        industry_description="AI",
        business_model="B2B",
        extraction_version="0.0.1",
        extracted_at=datetime.now(UTC),
    )


def _dist(p50: Decimal) -> ValuationDistribution:
    return ValuationDistribution(
        p10=p50 - Decimal("10"),
        p25=p50 - Decimal("5"),
        p50=p50,
        p75=p50 + Decimal("5"),
        p90=p50 + Decimal("10"),
        mean=p50,
        std=Decimal("5"),
    )


def _valuation() -> ValuationEnsembleOutput:
    d = _dist(Decimal("100"))
    return ValuationEnsembleOutput(
        company_id="P-SNAP-1",
        single_models=[
            SingleModelValuation(model_name="x", applicable=True, valuation_distribution=d)
        ],
        weights_used={"x": 1.0},
        ensemble_distribution=d,
        implied_price_range={
            "low": Decimal("95"),
            "fair": Decimal("100"),
            "high": Decimal("105"),
        },
    )


def _debate() -> DebateOutput:
    return DebateOutput(
        rounds=[
            DebateRound(
                round_number=1,
                bull_argument="positive",
                bear_argument="negative",
                devil_challenge="meta",
                resolution="see both sides",
            )
        ],
        final_consensus="balanced",
    )


def _decision() -> FinalDecision:
    return FinalDecision(
        decision=DecisionType.PARTIAL,
        confidence=0.7,
        suggested_allocation_pct=0.02,
        price_range_low=Decimal("95"),
        price_range_fair=Decimal("100"),
        price_range_high=Decimal("105"),
        expected_return_6m=_dist(Decimal("100")),
        expected_return_12m=_dist(Decimal("110")),
        scorecard={"overall": 65.0},
        key_reasons_for=["growth"],
        key_reasons_against=["concentration"],
    )


def _build():
    return build_snapshot(
        ipo_id=uuid4(),
        extraction=_ext(),
        agent_outputs={
            "fundamental": AgentOutput(
                agent_role=AgentRole.FUNDAMENTAL,
                scores={"x": 70.0},
                overall_score=70.0,
                runtime_seconds=0.1,
            )
        },
        valuation=_valuation(),
        debate=_debate(),
        decision=_decision(),
        total_cost_usd=Decimal("0.05"),
        runtime_seconds=12.3,
    )


def test_build_snapshot_contains_hash() -> None:
    snap = _build()
    assert len(snap.input_data_hash) == 64  # sha256 hex
    assert snap.system_version == "0.6.0"
    assert snap.runtime_seconds == 12.3


def test_compute_input_hash_deterministic() -> None:
    snap = _build()
    h2 = compute_input_hash(
        extraction=ProspectusExtraction.model_validate(snap.input_data_snapshot["extraction"]),
        agent_outputs=snap.agent_outputs,
        valuation=snap.valuation_output,
        debate=snap.debate_output,
        decision=snap.decision,
    )
    assert snap.input_data_hash == h2


def test_verify_snapshot_passes() -> None:
    snap = _build()
    verify_snapshot(snap)  # no raise


def test_verify_snapshot_fails_on_tamper() -> None:
    snap = _build()
    # Tamper hash directly via model_copy (allowed at construct time only).
    tampered = snap.model_copy(update={"input_data_hash": "0" * 64})
    with pytest.raises(SnapshotIntegrityError):
        verify_snapshot(tampered)


def test_snapshot_is_frozen() -> None:
    from pydantic import ValidationError

    snap = _build()
    with pytest.raises(ValidationError):
        snap.input_data_hash = "anything"
    assert snap.as_of_date == date.today() or snap.as_of_date == snap.created_at.date()
