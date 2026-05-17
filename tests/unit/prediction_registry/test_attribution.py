"""AttributionEngine tests — Phase 7.5b per ADR 0012."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from hk_ipo_agent.common.enums import (
    AdjustmentType,
    AgentRole,
    Confidence,
    DecisionType,
    ListingType,
)
from hk_ipo_agent.common.llm_client import LLMClient
from hk_ipo_agent.common.schemas import (
    AgentOutput,
    DebateOutput,
    DebateRound,
    FinalDecision,
    PredictionOutcome,
    ProspectusExtraction,
    SingleModelValuation,
    ValuationDistribution,
    ValuationEnsembleOutput,
)
from hk_ipo_agent.prediction_registry.attribution import (
    AttributionEngine,
    _DiagnosisOutput,
    _ProposedAdjustmentLLM,
)
from hk_ipo_agent.prediction_registry.snapshot import build_snapshot


def _dist(p50: Decimal) -> ValuationDistribution:
    return ValuationDistribution(
        p10=p50 - Decimal("2"),
        p25=p50 - Decimal("1"),
        p50=p50,
        p75=p50 + Decimal("1"),
        p90=p50 + Decimal("2"),
        mean=p50,
        std=Decimal("1"),
    )


def _snapshot():
    d = _dist(Decimal("10"))
    return build_snapshot(
        ipo_id=uuid4(),
        extraction=ProspectusExtraction(
            prospectus_id="P-ATT-1",
            company_name_zh="测试 ATT",
            listing_type=ListingType.MAINBOARD_TECH,
            industry_code="TECH",
            industry_description="AI",
            business_model="B2B",
            extraction_version="0.0.1",
            extracted_at=datetime.now(UTC),
        ),
        agent_outputs={
            "fundamental": AgentOutput(
                agent_role=AgentRole.FUNDAMENTAL,
                scores={"x": 80.0},
                overall_score=80.0,
                runtime_seconds=0.1,
            ),
            "cornerstone": AgentOutput(
                agent_role=AgentRole.CORNERSTONE_SIGNAL,
                scores={"x": 60.0},
                overall_score=60.0,
                runtime_seconds=0.1,
            ),
        },
        valuation=ValuationEnsembleOutput(
            company_id="P-ATT-1",
            single_models=[
                SingleModelValuation(model_name="dcf", applicable=True, valuation_distribution=d),
                SingleModelValuation(
                    model_name="comparable",
                    applicable=True,
                    valuation_distribution=_dist(Decimal("9")),
                ),
            ],
            weights_used={"dcf": 0.5, "comparable": 0.5},
            ensemble_distribution=d,
            implied_price_range={"low": Decimal("8"), "fair": Decimal("10"), "high": Decimal("12")},
        ),
        debate=DebateOutput(
            rounds=[
                DebateRound(
                    round_number=1,
                    bull_argument="growth strong",
                    bear_argument="customer concentration",
                    devil_challenge="margin compression?",
                    resolution="保留 bull",
                ),
            ],
            final_consensus="positive overall",
        ),
        decision=FinalDecision(
            decision=DecisionType.PARTICIPATE,
            confidence=0.7,
            suggested_allocation_pct=0.02,
            price_range_low=Decimal("8"),
            price_range_fair=Decimal("10"),
            price_range_high=Decimal("12"),
            expected_return_6m=d,
            expected_return_12m=d,
        ),
        total_cost_usd=Decimal("0.1"),
        runtime_seconds=10.0,
    )


def _outcome(snapshot_id, *, ret: float) -> PredictionOutcome:
    return PredictionOutcome(
        snapshot_id=snapshot_id,
        checkpoint_day=30,
        return_since_ipo=ret,
        return_since_listing=ret,
        max_drawdown=-0.1,
        relative_return_hsi=ret - 0.02,
        relative_return_hstech=ret - 0.04,
        relative_return_industry=ret - 0.05,
        price_in_predicted_range=False,
        decision_correct=ret > 0.05,
        recorded_at=datetime.now(UTC),
    )


def test_prediction_outcome_cornerstone_tracking_unreliable_default_false() -> None:
    """R2-5 — new field exists with backwards-compatible default of False.

    Pre-fix CLAUDE.md «基石减持检测的不确定性必须显式标注» had no
    schema surface; this test pins the field's existence and default so the
    learning loop / drift detector can rely on the contract.
    """
    from uuid import uuid4

    outcome = _outcome(uuid4(), ret=0.10)
    # Default-constructed outcomes are tracking-reliable. Reviewers and
    # the learning loop may trust cornerstone_reduced when this is False.
    assert outcome.cornerstone_tracking_unreliable is False


def test_prediction_outcome_cornerstone_tracking_unreliable_explicit_true() -> None:
    """R2-5 — explicit True must round-trip through Pydantic + model_dump."""
    from uuid import uuid4

    outcome = PredictionOutcome(
        snapshot_id=uuid4(),
        checkpoint_day=30,
        return_since_ipo=0.05,
        return_since_listing=0.05,
        max_drawdown=-0.1,
        relative_return_hsi=0.03,
        relative_return_hstech=0.01,
        relative_return_industry=0.00,
        price_in_predicted_range=True,
        decision_correct=True,
        cornerstone_tracking_unreliable=True,
        recorded_at=datetime.now(UTC),
    )
    assert outcome.cornerstone_tracking_unreliable is True
    # Round-trip through JSON: the flag must survive serialize/parse.
    dumped = outcome.model_dump(mode="json")
    assert dumped["cornerstone_tracking_unreliable"] is True
    restored = PredictionOutcome.model_validate(dumped)
    assert restored.cornerstone_tracking_unreliable is True


@pytest.fixture
def llm_mock(monkeypatch) -> LLMClient:
    monkeypatch.setenv("KIMI_API_KEY", "sk-test")
    client = LLMClient(daily_budget_usd=Decimal("100"))
    client.acomplete_json = AsyncMock(  # type: ignore[method-assign]
        return_value=_DiagnosisOutput(
            primary_attribution="valuation_model",
            llm_diagnosis="估值模型对 customer concentration 反应不足。",
            proposed_adjustments=[
                _ProposedAdjustmentLLM(
                    target_path="config/valuation_weights.yaml",
                    adjustment_type=AdjustmentType.WEIGHT_CHANGE,
                    current_value=0.5,
                    proposed_value=0.4,
                    rationale="降 DCF 权重",
                    expected_impact="提高 P50 准确率",
                    confidence=Confidence.MEDIUM,
                )
            ],
        )
    )
    return client


@pytest.mark.asyncio
async def test_attribute_builds_full_three_layer_blob(llm_mock) -> None:
    engine = AttributionEngine(llm=llm_mock)
    snap = _snapshot()
    outcome = _outcome(snap.id, ret=-0.15)  # loss → bear validated
    attribution = await engine.attribute(
        snapshot=snap,
        outcome=outcome,
        actual_price=Decimal("8.5"),
    )
    assert attribution.snapshot_id == snap.id
    assert attribution.checkpoint_day == 30
    assert len(attribution.agent_errors) == 2  # fundamental + cornerstone
    assert len(attribution.valuation_errors) == 2  # dcf + comparable
    # Bear validated 1/1 when ret < -5%.
    assert attribution.debate_quality.bear_predictions_validated == 1
    assert attribution.debate_quality.bull_predictions_validated == 0
    assert attribution.primary_attribution == "valuation_model"
    assert len(attribution.proposed_adjustments) == 1
    assert attribution.proposed_adjustments[0].evidence_snapshot_ids == [snap.id]


@pytest.mark.asyncio
async def test_attribute_handles_llm_failure_gracefully(monkeypatch, llm_mock) -> None:
    """LLM diagnose failure shouldn't raise — degrade to numeric-only summary."""
    llm_mock.acomplete_json = AsyncMock(side_effect=RuntimeError("Opus down"))  # type: ignore[method-assign]
    engine = AttributionEngine(llm=llm_mock)
    snap = _snapshot()
    outcome = _outcome(snap.id, ret=0.10)
    attribution = await engine.attribute(
        snapshot=snap,
        outcome=outcome,
        actual_price=Decimal("11"),
    )
    assert attribution.primary_attribution == "diagnosis_unavailable"
    assert "Opus down" in attribution.llm_diagnosis
    assert attribution.proposed_adjustments == []


def test_build_agent_errors_high_score_loss_is_miscalibrated() -> None:
    snap = _snapshot()
    outcome = _outcome(snap.id, ret=-0.20)
    errors = AttributionEngine._build_agent_errors(snap, outcome)
    fundamental = next(e for e in errors if e.agent_role == AgentRole.FUNDAMENTAL)
    # Score 0.8, but realised return very negative → strong positive miscalibration.
    assert fundamental.score_calibration > 0.2


def test_build_valuation_errors_flags_p10_p90_misses() -> None:
    snap = _snapshot()
    # actual_price 15 is above P90=12 → out of range.
    errors = AttributionEngine._build_valuation_errors(snap, Decimal("15"))
    assert all(not e.in_p10_p90_range for e in errors)
    assert all(e.pct_error > 0 for e in errors)


def test_debate_quality_no_validation_when_within_noise() -> None:
    snap = _snapshot()
    outcome = _outcome(snap.id, ret=0.02)  # within ±5% — neither bear nor bull wins
    quality = AttributionEngine._build_debate_quality(snap, outcome)
    assert quality.bear_predictions_validated == 0
    assert quality.bull_predictions_validated == 0
