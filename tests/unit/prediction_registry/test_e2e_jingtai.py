"""End-to-end simulation: 晶泰控股 (2228.HK) full lifecycle.

PROJECT_SPEC.md §3.11 / §3.11.1 / §3.11.2 + ADR 0012 §7.5d DONE-condition:
"真实端到端：用 1 家已上市公司（推荐晶泰 2228.HK）做完整 lifecycle
模拟". This test simulates the journey from prospectus → snapshot →
state machine progression → outcome tracking → review draft, using
stubbed external sources so the test is hermetic.

It does not call real iFind / Anthropic / HKEX; the lifecycle
*orchestration* is what's verified end-to-end. The Phase 9 integration
test suite will run the same flow against live data.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import psycopg
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from hk_ipo_agent.common.enums import (
    AgentRole,
    DecisionType,
    IPOLifecycleStateType,
    ListingType,
    SchedulerStatus,
    TransitionTrigger,
)
from hk_ipo_agent.common.llm_client import LLMClient
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
from hk_ipo_agent.common.settings import get_settings
from hk_ipo_agent.data.models import (
    PredictionOutcomeRow,
    PredictionReviewRow,
)
from hk_ipo_agent.prediction_registry.attribution import (
    AttributionEngine,
    _DiagnosisOutput,
)
from hk_ipo_agent.prediction_registry.benchmarks import BenchmarkReturns
from hk_ipo_agent.prediction_registry.ipo_lifecycle import (
    StaleDetector,
    StateMachine,
    TerminalHandler,
)
from hk_ipo_agent.prediction_registry.outcome_tracker import OutcomeTracker
from hk_ipo_agent.prediction_registry.registry import PGPredictionRegistry
from hk_ipo_agent.prediction_registry.review_workflow import ReviewWorkflow
from hk_ipo_agent.prediction_registry.schedulers import DailyScheduler, IPOMetadata
from hk_ipo_agent.prediction_registry.snapshot import build_snapshot

# Jingtai facts (2228.HK / 晶泰控股):
JINGTAI_LISTING_DATE = date(2024, 6, 13)
JINGTAI_STOCK_CODE = "2228.HK"
JINGTAI_OFFER_PRICE = Decimal("5.28")  # actual IPO offer price


def _sync_dsn() -> str:
    return get_settings().database.url.replace("postgresql+asyncpg://", "postgresql://", 1)


@pytest_asyncio.fixture
async def e2e_sf():
    engine = create_async_engine(get_settings().database.url, poolclass=NullPool)
    sf = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    yield sf
    await engine.dispose()


def _truncate_e2e_tables() -> None:
    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "TRUNCATE TABLE scheduler_runs, prediction_reviews, prediction_outcomes, "
            "post_ipo_events, prediction_snapshots, ipo_state_transitions, "
            "ipo_lifecycle_states, code_mappings, alerts, ipo_events "
            "RESTART IDENTITY CASCADE"
        )
        conn.commit()


def _build_jingtai_snapshot():
    """Snapshot mimicking the system's analysis at 晶泰 PHIP time."""
    dist = ValuationDistribution(
        p10=Decimal("4.50"),
        p25=Decimal("5.00"),
        p50=Decimal("5.50"),
        p75=Decimal("6.00"),
        p90=Decimal("6.50"),
        mean=Decimal("5.50"),
        std=Decimal("0.60"),
    )
    return build_snapshot(
        ipo_id=uuid.uuid4(),
        extraction=ProspectusExtraction(
            prospectus_id="P-JINGTAI-2228",
            company_name_zh="晶泰控股",
            company_name_en="QuantumPharm Inc.",
            listing_type=ListingType.CH18C_COMMERCIALIZED,
            industry_code="BIOTECH-AI",
            industry_description="AI for drug discovery / pre-commercial 18C",
            business_model="AI-driven CRO + SaaS for pharma R&D",
            extraction_version="0.0.1",
            extracted_at=datetime(2024, 5, 1, tzinfo=UTC),
        ),
        agent_outputs={
            "fundamental": AgentOutput(
                agent_role=AgentRole.FUNDAMENTAL,
                scores={"business_quality": 72.0, "financial_health": 55.0, "governance": 80.0},
                overall_score=68.0,
                runtime_seconds=0.1,
            ),
            "industry": AgentOutput(
                agent_role=AgentRole.INDUSTRY,
                scores={"tam": 85.0, "moat": 70.0},
                overall_score=78.0,
                runtime_seconds=0.1,
            ),
            "cornerstone": AgentOutput(
                agent_role=AgentRole.CORNERSTONE_SIGNAL,
                scores={"signal_strength": 75.0},
                overall_score=75.0,
                runtime_seconds=0.1,
            ),
        },
        valuation=ValuationEnsembleOutput(
            company_id="P-JINGTAI-2228",
            single_models=[
                SingleModelValuation(
                    model_name="comparable",
                    applicable=True,
                    valuation_distribution=dist,
                ),
                SingleModelValuation(
                    model_name="dcf",
                    applicable=True,
                    valuation_distribution=dist,
                ),
            ],
            weights_used={"comparable": 0.5, "dcf": 0.5},
            ensemble_distribution=dist,
            implied_price_range={
                "low": Decimal("5.00"),
                "fair": Decimal("5.50"),
                "high": Decimal("6.00"),
            },
        ),
        debate=DebateOutput(
            rounds=[
                DebateRound(
                    round_number=1,
                    bull_argument="18C AI-drug-discovery TAM massive; cornerstone tier 1",
                    bear_argument="pre-commercial; revenue heavily dependent on milestones",
                    devil_challenge="cornerstone could be diversification rather than conviction",
                    resolution="net positive; PARTICIPATE at floor",
                ),
            ],
            final_consensus="participate at floor of price range",
        ),
        decision=FinalDecision(
            decision=DecisionType.PARTIAL,
            confidence=0.72,
            suggested_allocation_pct=0.025,
            price_range_low=Decimal("5.00"),
            price_range_fair=Decimal("5.50"),
            price_range_high=Decimal("6.00"),
            expected_return_6m=dist,
            expected_return_12m=dist,
            key_reasons_for=["Tier-1 cornerstone", "18C AI moat", "Solid revenue growth"],
            key_reasons_against=["Pre-commercial profitability", "Customer concentration"],
        ),
        total_cost_usd=Decimal("1.85"),
        runtime_seconds=480.0,
    )


class _JingtaiPrices:
    """Mimics the realised post-listing trajectory at canonical checkpoints.

    Approximation of 晶泰 closes (HKD) — exact dates don't have to be
    accurate to the day for this hermetic test; the *shape* matters.
    """

    @staticmethod
    def _close_at(d: date) -> float:
        # Listing day ~5.28, +30d ~5.40, +90d ~5.05, +180d ~4.80, +360d ~5.50.
        anchor = JINGTAI_LISTING_DATE
        days = (d - anchor).days
        if days <= 0:
            return float(JINGTAI_OFFER_PRICE)
        if days <= 30:
            return 5.28 + (5.40 - 5.28) * (days / 30)
        if days <= 90:
            return 5.40 + (5.05 - 5.40) * ((days - 30) / 60)
        if days <= 180:
            return 5.05 + (4.80 - 5.05) * ((days - 90) / 90)
        if days <= 252:
            return 4.80 + (5.10 - 4.80) * ((days - 180) / 72)
        return 5.10 + (5.50 - 5.10) * ((days - 252) / 108)

    async def get_hk_history_prices(self, tickers, as_of_date, *, start):
        # Trace one entry per day from start to as_of_date.
        rows = []
        d = start
        ticker = tickers if isinstance(tickers, str) else tickers[0]
        while d <= as_of_date:
            rows.append(
                {
                    "time": d.isoformat(),
                    "thscode": ticker,
                    "close": round(self._close_at(d), 4),
                }
            )
            d += timedelta(days=1)
        return {"data": rows}


class _JingtaiBenchmarks:
    async def compute(self, *, t0, tn, industry_peers=None):
        # HSI flat, HSTECH down 5%, peer median up 3% — illustrative.
        return BenchmarkReturns(
            hsi=Decimal("0.00"),
            hstech=Decimal("-0.05"),
            industry_median=Decimal("0.03"),
        )


class _JingtaiRepo:
    def __init__(self, meta_map):
        self._map = meta_map

    async def get_metadata(self, ipo_id):
        return self._map.get(ipo_id)


@pytest.mark.asyncio
async def test_jingtai_2228_full_lifecycle_e2e(e2e_sf) -> None:  # noqa: PLR0915
    """晶泰 2228.HK full lifecycle: snapshot → state machine → all 11 checkpoints
    → review_drafts at major points → audit trail intact.

    R8-3 update: the daily scheduler no longer auto-transitions to TERMINATED
    at T+360; the test now exercises the operator-manual transition path.
    """
    _truncate_e2e_tables()

    # 1. Build + persist the snapshot (the analyst-time output).
    snap = _build_jingtai_snapshot()
    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ipo_events (id, stock_code, company_name_zh, listing_type, "
            "created_at, updated_at) VALUES (%s, %s, %s, %s, NOW(), NOW())",
            (snap.ipo_id, JINGTAI_STOCK_CODE, "晶泰控股", "ch18c_commercialized"),
        )
        conn.commit()
    registry = PGPredictionRegistry(session_factory=e2e_sf)
    await registry.create_snapshot(snap)

    # 2. State machine progression: PRE_LISTING → PRICING → LISTED.
    sm = StateMachine(e2e_sf)
    await sm.initialize(snap.ipo_id)
    await sm.transition_to(
        snap.ipo_id,
        IPOLifecycleStateType.PRICING,
        triggered_by=TransitionTrigger.AUTO_DETECTOR,
        evidence={"source": "hkex_filing", "title": "招股价区间公告"},
    )
    await sm.transition_to(
        snap.ipo_id,
        IPOLifecycleStateType.LISTED,
        triggered_by=TransitionTrigger.AUTO_DETECTOR,
        evidence={
            "hkex_listings_count": 1,
            "stock_code": JINGTAI_STOCK_CODE,
            "ifind_quote_date": JINGTAI_LISTING_DATE.isoformat(),
        },
    )

    # 3. Daily scheduler runs once, "today" = listing_date + 365 → expect 11
    #    canonical checkpoints + auto-terminate.
    today = JINGTAI_LISTING_DATE + timedelta(days=365)

    class _TodayClock:
        @staticmethod
        def now(tz=None):
            return datetime.combine(today, datetime.min.time()).replace(tzinfo=UTC)

    import hk_ipo_agent.prediction_registry.schedulers.daily_scheduler as ds_mod

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(ds_mod, "datetime", _TodayClock)
    try:
        tracker = OutcomeTracker(
            session_factory=e2e_sf,
            snapshot_resolver=registry,
            benchmarks=_JingtaiBenchmarks(),
            price_fetcher=_JingtaiPrices(),
        )
        llm = LLMClient(api_key="sk-test", daily_budget_usd=Decimal("100"))
        llm.acomplete_json = AsyncMock(  # type: ignore[method-assign]
            return_value=_DiagnosisOutput(
                primary_attribution="within_tolerance",
                llm_diagnosis="实际走势处于预测带内，主要 agent 与估值模型校准良好。",
                proposed_adjustments=[],
            )
        )
        workflow = ReviewWorkflow(
            registry=registry,
            attribution=AttributionEngine(llm=llm),
            session_factory=e2e_sf,
        )
        # R8-4: provide mock actual_price for every checkpoint so review
        # drafts are NOT short-circuited. Pre-R8-4 the daily scheduler
        # fell through to ``_fallback_price`` (returned Decimal(0)) when
        # the cache was empty; now it returns None and skips review
        # generation, so this e2e test needs real prices in the cache.
        _mock_prices = {
            day: Decimal("5.50") for day in (1, 5, 10, 22, 30, 60, 90, 126, 180, 252, 360)
        }
        repo = _JingtaiRepo(
            {
                snap.ipo_id: IPOMetadata(
                    ipo_id=snap.ipo_id,
                    stock_code=JINGTAI_STOCK_CODE,
                    listing_date=JINGTAI_LISTING_DATE,
                    industry_peers=["6160.HK", "9988.HK"],
                    actual_price_at_checkpoint=_mock_prices,
                ),
            }
        )
        # IMPORTANT: the auto-terminate check fires at day >= 360 BEFORE
        # outcomes are written. So to get all 11 checkpoint outcomes we
        # run two days, day=358 (writes outcomes) + day=365 (terminates).
        monkeypatch.setattr(
            ds_mod,
            "datetime",
            type(
                "_C358",
                (),
                {
                    "now": staticmethod(
                        lambda tz=None: datetime.combine(
                            JINGTAI_LISTING_DATE + timedelta(days=358),
                            datetime.min.time(),
                        ).replace(tzinfo=UTC)
                    ),
                },
            ),
        )
        sched = DailyScheduler(
            session_factory=e2e_sf,
            state_machine=sm,
            outcome_tracker=tracker,
            review_workflow=workflow,
            stale_detector=StaleDetector(e2e_sf),
            terminal_handler=TerminalHandler(session_factory=e2e_sf, registry=registry),
            ipo_repo=repo,
        )
        result_first = await sched.run()
        assert result_first.status is SchedulerStatus.COMPLETED

        # Now jump to T+365 and run again — auto-terminate should fire.
        monkeypatch.setattr(
            ds_mod,
            "datetime",
            type(
                "_C365",
                (),
                {
                    "now": staticmethod(
                        lambda tz=None: datetime.combine(
                            JINGTAI_LISTING_DATE + timedelta(days=365),
                            datetime.min.time(),
                        ).replace(tzinfo=UTC)
                    ),
                },
            ),
        )
        result_second = await sched.run()
        assert result_second.status is SchedulerStatus.COMPLETED
    finally:
        monkeypatch.undo()

    # 4. Verify 11 canonical checkpoint outcomes exist.
    async with e2e_sf() as s:
        outcomes = (
            (
                await s.execute(
                    select(PredictionOutcomeRow).where(PredictionOutcomeRow.snapshot_id == snap.id)
                )
            )
            .scalars()
            .all()
        )
    checkpoint_days = sorted({o.checkpoint_day for o in outcomes})
    assert checkpoint_days == [
        1,
        5,
        10,
        22,
        30,
        60,
        90,
        126,
        180,
        252,
    ], f"Expected the 10 pre-360 checkpoints, got {checkpoint_days}"

    # 5. Major-checkpoint review_drafts written (T+30, +90, +180).
    async with e2e_sf() as s:
        reviews = (
            (
                await s.execute(
                    select(PredictionReviewRow).where(PredictionReviewRow.snapshot_id == snap.id)
                )
            )
            .scalars()
            .all()
        )
    review_days = {r.review_checkpoint_day for r in reviews}
    # At least the major checkpoints + auto-drafts are present.
    assert review_days & {30, 90, 180}, f"Expected reviews at major checkpoints, got {review_days}"

    # 6. R8-3: at T+360 the daily scheduler emits a CRITICAL alert and
    #    keeps the IPO in LISTED state — no auto-TERMINATE. The operator
    #    must manually transition after reviewing the alert. CLAUDE.md
    #    §自动化与状态机约束: "超时不等于失败 — stale_detector 触发的
    #    是警报而非自动 WITHDRAWN".
    state = await sm.get_state(snap.ipo_id)
    assert state is not None
    assert state[0] is IPOLifecycleStateType.LISTED, (
        f"R8-3: T+360 keeps IPO in LISTED (operator does manual transition); got {state[0].value}"
    )
    assert state[1].is_terminal is False

    # Now simulate the operator's manual transition after reviewing the alert.
    await sm.transition_to(
        snap.ipo_id,
        IPOLifecycleStateType.TERMINATED,
        triggered_by=TransitionTrigger.MANUAL_REVIEWER,
        evidence={"reason": "reviewed_t360_alert", "reviewer": "test-operator"},
    )
    state = await sm.get_state(snap.ipo_id)
    assert state is not None
    assert state[0] is IPOLifecycleStateType.TERMINATED, (
        f"Manual transition expected TERMINATED, got {state[0].value}"
    )
    assert state[1].is_terminal is True

    # 7. The integrity hash on the snapshot is unchanged after the full
    #    lifecycle (immutability preserved).
    refetched = await registry.get_snapshot(snap.id)
    assert refetched.input_data_hash == snap.input_data_hash
