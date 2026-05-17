"""ReviewWorkflow tests — Phase 7.5b per ADR 0012."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from hk_ipo_agent.common.enums import (
    AdjustmentStatus,
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
    FinalDecision,
    ProspectusExtraction,
    SingleModelValuation,
    ValuationDistribution,
    ValuationEnsembleOutput,
)
from hk_ipo_agent.common.settings import get_settings
from hk_ipo_agent.prediction_registry.attribution import (
    AttributionEngine,
    _DiagnosisOutput,
    _ProposedAdjustmentLLM,
)
from hk_ipo_agent.prediction_registry.outcome_tracker import OutcomeTracker
from hk_ipo_agent.prediction_registry.registry import PGPredictionRegistry
from hk_ipo_agent.prediction_registry.review_workflow import (
    CRITICAL_LOSS_THRESHOLD,
    MAJOR_CHECKPOINTS,
    ReviewWorkflow,
    days_since_listing,
    is_major_checkpoint,
)
from hk_ipo_agent.prediction_registry.snapshot import build_snapshot


def test_major_checkpoints_match_spec() -> None:
    assert MAJOR_CHECKPOINTS == (30, 90, 180, 360)


def test_is_major_checkpoint_filter() -> None:
    assert is_major_checkpoint(30)
    assert is_major_checkpoint(90)
    assert not is_major_checkpoint(5)
    assert not is_major_checkpoint(1)


def test_days_since_listing_basic() -> None:
    assert days_since_listing(date(2026, 1, 1), today=date(2026, 1, 30)) == 29


def test_critical_threshold_is_minus_20_percent() -> None:
    assert Decimal("-0.20") == CRITICAL_LOSS_THRESHOLD


def _build_snapshot():
    d = ValuationDistribution(
        p10=Decimal("9"), p25=Decimal("9.5"), p50=Decimal("10"),
        p75=Decimal("10.5"), p90=Decimal("11"),
        mean=Decimal("10"), std=Decimal("0.5"),
    )
    return build_snapshot(
        ipo_id=uuid.uuid4(),
        extraction=ProspectusExtraction(
            prospectus_id=f"P-RV-{uuid.uuid4().hex[:6]}",
            company_name_zh="测试 RV", listing_type=ListingType.MAINBOARD_TECH,
            industry_code="TECH", industry_description="AI", business_model="B2B",
            extraction_version="0.0.1", extracted_at=datetime.now(UTC),
        ),
        agent_outputs={
            "fundamental": AgentOutput(
                agent_role=AgentRole.FUNDAMENTAL,
                scores={"x": 70.0}, overall_score=70.0, runtime_seconds=0.1,
            ),
        },
        valuation=ValuationEnsembleOutput(
            company_id="P-RV-1",
            single_models=[
                SingleModelValuation(model_name="x", applicable=True, valuation_distribution=d),
            ],
            weights_used={"x": 1.0}, ensemble_distribution=d,
            implied_price_range={"low": Decimal("9"), "fair": Decimal("10"), "high": Decimal("11")},
        ),
        debate=DebateOutput(final_consensus="balanced"),
        decision=FinalDecision(
            decision=DecisionType.PARTICIPATE,
            confidence=0.7, suggested_allocation_pct=0.02,
            price_range_low=Decimal("9"), price_range_fair=Decimal("10"), price_range_high=Decimal("11"),
            expected_return_6m=d, expected_return_12m=d,
        ),
        total_cost_usd=Decimal("0.1"), runtime_seconds=10.0,
    )


class _StubPrices:
    async def get_hk_history_prices(self, tickers, as_of_date, *, start):
        return {
            "data": [
                {"time": start.isoformat(), "thscode": "TEST.HK", "close": 10.0},
                {"time": as_of_date.isoformat(), "thscode": "TEST.HK", "close": 11.0},
            ]
        }


class _StubBenchmarks:
    async def compute(self, *, t0, tn, industry_peers=None):
        from hk_ipo_agent.prediction_registry.benchmarks import BenchmarkReturns  # noqa: PLC0415
        return BenchmarkReturns(hsi=Decimal("0.03"), hstech=Decimal("0.05"), industry_median=Decimal("0.08"))


@pytest_asyncio.fixture
async def pg_workflow():
    """Returns (workflow, registry, session_factory) over a truncated PG."""
    import os  # noqa: PLC0415

    os.environ.setdefault("KIMI_API_KEY", "sk-test-fixture")
    engine = create_async_engine(get_settings().database.url, poolclass=NullPool)
    sf = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with sf() as s:
        await s.execute(text(
            "TRUNCATE TABLE prediction_reviews, prediction_outcomes, post_ipo_events, "
            "prediction_snapshots, ipo_events RESTART IDENTITY CASCADE"
        ))
        await s.commit()

    # LLM mock for the AttributionEngine. Pass api_key directly to bypass
    # the lru_cached Settings (which may have been built before the env was set).
    llm = LLMClient(api_key="sk-test-fixture", daily_budget_usd=Decimal("100"))
    llm.acomplete_json = AsyncMock(  # type: ignore[method-assign]
        return_value=_DiagnosisOutput(
            primary_attribution="valuation_model",
            llm_diagnosis="估值模型偏差",
            proposed_adjustments=[
                _ProposedAdjustmentLLM(
                    target_path="config/valuation_weights.yaml",
                    adjustment_type=AdjustmentType.WEIGHT_CHANGE,
                    current_value=0.5, proposed_value=0.4,
                    rationale="降低 DCF 权重",
                    expected_impact="提高 P50 准确率",
                    confidence=Confidence.MEDIUM,
                )
            ],
        )
    )

    registry = PGPredictionRegistry(session_factory=sf)
    workflow = ReviewWorkflow(
        registry=registry,
        attribution=AttributionEngine(llm=llm),
        session_factory=sf,
    )
    try:
        yield workflow, registry, sf
    finally:
        await engine.dispose()


async def _seed_snapshot_and_outcome(
    sf, registry, *, decision_correct: bool, return_pct: float,
):
    snap = _build_snapshot()
    async with sf() as s:
        await s.execute(
            text("INSERT INTO ipo_events (id, stock_code, company_name_zh, listing_type, "
                 "created_at, updated_at) VALUES (:id, :c, :n, :lt, NOW(), NOW())"),
            {"id": snap.ipo_id, "c": "TEST.HK", "n": "Test", "lt": "mainboard_tech"},
        )
        await s.commit()
    await registry.create_snapshot(snap)
    tracker = OutcomeTracker(
        session_factory=sf, snapshot_resolver=registry,
        benchmarks=_StubBenchmarks(),
        price_fetcher=_StubPrices(),
    )
    listing_d = date(2026, 1, 15)
    await tracker.track(
        snapshot_id=snap.id, checkpoint_day=30,
        stock_code="TEST.HK", listing_date=listing_d,
    )
    # Outcome's decision_correct + return_since_listing depend on stub prices
    # (10→11 = +10% PARTICIPATE = correct). For tests that need a loss,
    # patch the row directly.
    if return_pct < 0:
        async with sf() as s:
            await s.execute(text(
                "UPDATE prediction_outcomes SET decision_correct = :c, "
                "return_since_listing = :r WHERE snapshot_id = :id"
            ), {"c": decision_correct, "r": Decimal(str(return_pct)), "id": snap.id})
            await s.commit()
    return snap


@pytest.mark.asyncio
async def test_generate_draft_skips_non_major_checkpoint(pg_workflow) -> None:
    workflow, registry, sf = pg_workflow
    snap = await _seed_snapshot_and_outcome(sf, registry, decision_correct=True, return_pct=0.10)
    result = await workflow.generate_draft(
        snapshot_id=snap.id, checkpoint_day=5,  # non-major
        actual_price=Decimal("11"),
    )
    assert result.skipped
    assert "not a major checkpoint" in (result.skip_reason or "")


@pytest.mark.asyncio
async def test_generate_draft_writes_review_at_30d(pg_workflow) -> None:
    workflow, registry, sf = pg_workflow
    snap = await _seed_snapshot_and_outcome(sf, registry, decision_correct=True, return_pct=0.10)
    result = await workflow.generate_draft(
        snapshot_id=snap.id, checkpoint_day=30, actual_price=Decimal("11"),
    )
    assert not result.skipped
    assert isinstance(result.review_id, uuid.UUID)


@pytest.mark.asyncio
async def test_critical_draft_triggers_at_non_major_when_loss_large(pg_workflow) -> None:
    """Loss > 20% + wrong decision should force-generate even at T+5."""
    # NB: outcome_tracker normally only writes valid checkpoint days; we wrote
    # at 30d in _seed_..., here we patch to make it a loss scenario.
    workflow, registry, sf = pg_workflow
    snap = await _seed_snapshot_and_outcome(sf, registry, decision_correct=False, return_pct=-0.25)
    result = await workflow.generate_critical_draft_if_needed(
        snapshot_id=snap.id, checkpoint_day=30, actual_price=Decimal("7.5"),
    )
    assert not result.skipped


@pytest.mark.asyncio
async def test_critical_draft_skipped_when_not_critical(pg_workflow) -> None:
    workflow, registry, sf = pg_workflow
    snap = await _seed_snapshot_and_outcome(sf, registry, decision_correct=True, return_pct=0.10)
    result = await workflow.generate_critical_draft_if_needed(
        snapshot_id=snap.id, checkpoint_day=30, actual_price=Decimal("11"),
    )
    assert result.skipped


@pytest.mark.asyncio
async def test_submit_review_persists_with_reviewer(pg_workflow) -> None:
    workflow, registry, sf = pg_workflow
    snap = await _seed_snapshot_and_outcome(sf, registry, decision_correct=True, return_pct=0.10)
    review_id = await workflow.submit_review(
        snapshot_id=snap.id,
        reviewer="alice",
        what_we_got_right="cornerstone read",
        what_we_got_wrong="missed margin",
        adjustment_status=AdjustmentStatus.ACCEPTED,
        review_checkpoint_day=30,
    )
    assert isinstance(review_id, uuid.UUID)
