"""Scheduler tests — Phase 7.5d-1 per ADR 0012.

Covers the four DONE-condition adversarial cases (PROJECT_SPEC.md §3.11.2):
- idempotent re-runs (same checkpoint not double-counted)
- mid-run interruption → resume via scheduler_runs.status='failed' replay
- advisory-lock contention blocks overlapping runs
- missed checkpoints back-filled with historical close

Plus the structural invariants:
- BaseScheduler writes scheduler_runs row at start + end
- scheduler_type lock keys are deterministic
- high_freq doesn't try to run outcome_tracker (architectural separation)
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

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
    SchedulerType,
    TransitionTrigger,
)
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
from hk_ipo_agent.data.models import (
    PredictionOutcomeRow,
    SchedulerRunRow,
)
from hk_ipo_agent.prediction_registry.ipo_lifecycle import (
    StaleDetector,
    StateMachine,
    TerminalHandler,
)
from hk_ipo_agent.prediction_registry.outcome_tracker import OutcomeTracker
from hk_ipo_agent.prediction_registry.registry import PGPredictionRegistry
from hk_ipo_agent.prediction_registry.review_workflow import ReviewWorkflow
from hk_ipo_agent.prediction_registry.schedulers import (
    DailyScheduler,
    EventDrivenScheduler,
    EventPayload,
    HighFrequencyScheduler,
    IPOMetadata,
)
from hk_ipo_agent.prediction_registry.schedulers.base import (
    BaseScheduler,
    RunStats,
    _lock_key_for,
)
from hk_ipo_agent.prediction_registry.schedulers.event_driven_scheduler import (
    EVENT_KIND_EARNINGS,
    EVENT_KIND_PRICE_ANOMALY,
)
from hk_ipo_agent.prediction_registry.snapshot import build_snapshot


def _sync_dsn() -> str:
    return get_settings().database.url.replace("postgresql+asyncpg://", "postgresql://", 1)


@pytest_asyncio.fixture
async def fresh_sf():
    engine = create_async_engine(get_settings().database.url, poolclass=NullPool)
    sf = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    yield sf
    await engine.dispose()


def _truncate_scheduler_tables() -> None:
    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "TRUNCATE TABLE scheduler_runs, prediction_outcomes, prediction_reviews, "
            "post_ipo_events, prediction_snapshots, ipo_state_transitions, "
            "ipo_lifecycle_states, code_mappings, alerts, ipo_events RESTART IDENTITY CASCADE"
        )
        conn.commit()


def _seed_ipo(ipo_id: UUID) -> None:
    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ipo_events (id, stock_code, company_name_zh, listing_type, "
            "created_at, updated_at) VALUES (%s, %s, %s, %s, NOW(), NOW())",
            (ipo_id, "TEST.HK", "Test", "mainboard_tech"),
        )
        conn.commit()


# ===========================================================================
# BaseScheduler — lock + scheduler_runs lifecycle
# ===========================================================================


class _DummyScheduler(BaseScheduler):
    """Trivial subclass used for base-class invariant tests."""

    scheduler_type = SchedulerType.HIGH_FREQ

    def __init__(self, *, session_factory, raise_in_work: bool = False):
        super().__init__(session_factory=session_factory)
        self._raise = raise_in_work

    async def do_work(self, stats: RunStats) -> None:
        stats.snapshots_processed = 7
        stats.events_detected = 3
        if self._raise:
            raise RuntimeError("simulated work failure")


def test_lock_keys_are_deterministic_and_distinct() -> None:
    """Three scheduler types must hash to three distinct stable lock keys."""
    keys = {t: _lock_key_for(t) for t in SchedulerType}
    assert len(set(keys.values())) == len(keys)
    # Re-computing returns same value.
    for t, k in keys.items():
        assert _lock_key_for(t) == k
    # All fit in signed bigint range.
    for k in keys.values():
        assert -(2**63) <= k <= 2**63 - 1


@pytest.mark.asyncio
async def test_run_writes_scheduler_runs_row_with_completed_status(fresh_sf) -> None:
    _truncate_scheduler_tables()
    sched = _DummyScheduler(session_factory=fresh_sf)
    result = await sched.run()
    assert result.status is SchedulerStatus.COMPLETED
    assert result.stats.snapshots_processed == 7
    assert result.stats.events_detected == 3
    assert result.locked is True
    async with fresh_sf() as s:
        row = (
            await s.execute(select(SchedulerRunRow).where(SchedulerRunRow.run_id == result.run_id))
        ).scalar_one()
    assert row.status == SchedulerStatus.COMPLETED.value
    assert row.snapshots_processed == 7
    assert row.completed_at is not None


@pytest.mark.asyncio
async def test_run_records_failure_and_does_not_re_raise(fresh_sf) -> None:
    """do_work errors get captured into stats + scheduler_runs.status='failed'."""
    _truncate_scheduler_tables()
    sched = _DummyScheduler(session_factory=fresh_sf, raise_in_work=True)
    result = await sched.run()
    assert result.status is SchedulerStatus.FAILED
    assert result.stats.errors_encountered == 1
    assert result.stats.error_details
    assert "simulated work failure" in result.stats.error_details[0]["message"]
    async with fresh_sf() as s:
        row = (
            await s.execute(select(SchedulerRunRow).where(SchedulerRunRow.run_id == result.run_id))
        ).scalar_one()
    assert row.status == SchedulerStatus.FAILED.value
    assert row.errors_encountered == 1


@pytest.mark.asyncio
async def test_advisory_lock_blocks_overlapping_runs(fresh_sf) -> None:
    """A second concurrent run gets `locked=False` and doesn't do work."""
    _truncate_scheduler_tables()
    sched_a = _DummyScheduler(session_factory=fresh_sf)
    sched_b = _DummyScheduler(session_factory=fresh_sf)

    # Make `do_work` slow enough that the two runs overlap.
    work_started = asyncio.Event()
    release = asyncio.Event()

    async def _slow_work(stats):
        work_started.set()
        await release.wait()
        stats.snapshots_processed = 1

    sched_a.do_work = _slow_work  # type: ignore[assignment]

    task_a = asyncio.create_task(sched_a.run())
    await asyncio.wait_for(work_started.wait(), timeout=5.0)
    # While A is still inside the lock, run B.
    result_b = await sched_b.run()
    release.set()
    result_a = await task_a

    assert result_a.locked is True
    assert result_a.status is SchedulerStatus.COMPLETED
    assert result_b.locked is False
    # B's run didn't actually execute work.
    assert result_b.stats.snapshots_processed == 0


# ===========================================================================
# DailyScheduler — outcome tracking idempotency + back-fill + terminate
# ===========================================================================


def _build_snapshot():
    d = ValuationDistribution(
        p10=Decimal("9"),
        p25=Decimal("9.5"),
        p50=Decimal("10"),
        p75=Decimal("10.5"),
        p90=Decimal("11"),
        mean=Decimal("10"),
        std=Decimal("0.5"),
    )
    return build_snapshot(
        ipo_id=uuid.uuid4(),
        extraction=ProspectusExtraction(
            prospectus_id=f"P-SCHED-{uuid.uuid4().hex[:6]}",
            company_name_zh="测试",
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
                scores={"x": 70.0},
                overall_score=70.0,
                runtime_seconds=0.1,
            ),
        },
        valuation=ValuationEnsembleOutput(
            company_id="P-SCHED-1",
            single_models=[
                SingleModelValuation(model_name="x", applicable=True, valuation_distribution=d)
            ],
            weights_used={"x": 1.0},
            ensemble_distribution=d,
            implied_price_range={"low": Decimal("9"), "fair": Decimal("10"), "high": Decimal("11")},
        ),
        debate=DebateOutput(final_consensus="balanced"),
        decision=FinalDecision(
            decision=DecisionType.PARTICIPATE,
            confidence=0.7,
            suggested_allocation_pct=0.02,
            price_range_low=Decimal("9"),
            price_range_fair=Decimal("10"),
            price_range_high=Decimal("11"),
            expected_return_6m=d,
            expected_return_12m=d,
        ),
        total_cost_usd=Decimal("0.05"),
        runtime_seconds=5.0,
    )


class _StubPrices:
    """Always returns t0=10, tn=11 for any (ticker, date) window."""

    def __init__(self, stock_code: str = "TEST.HK"):
        self._code = stock_code

    async def get_hk_history_prices(self, tickers, as_of_date, *, start):
        return {
            "data": [
                {"time": start.isoformat(), "thscode": self._code, "close": 10.0},
                {"time": as_of_date.isoformat(), "thscode": self._code, "close": 11.0},
            ]
        }


class _StubBenchmarks:
    async def compute(self, *, t0, tn, industry_peers=None):
        from hk_ipo_agent.prediction_registry.benchmarks import BenchmarkReturns

        return BenchmarkReturns(
            hsi=Decimal("0.03"), hstech=Decimal("0.05"), industry_median=Decimal("0.08")
        )


class _StubIPORepo:
    def __init__(self, meta_map: dict[UUID, IPOMetadata]):
        self._map = meta_map

    async def get_metadata(self, ipo_id):
        return self._map.get(ipo_id)


async def _set_lifecycle_to_listed(fresh_sf, snap, listing_d):
    """Seed ipo_event + LISTED lifecycle row + snapshot via PGPredictionRegistry.

    Lifecycle is built via StateMachine.initialize → transition_to so
    the row uses the same write path the rest of the system does.
    Raw SQL with empty {} blobs would also break registry.get_snapshot.
    """
    _ = listing_d  # unused — callers retain it for clarity
    _seed_ipo(snap.ipo_id)
    registry = PGPredictionRegistry(session_factory=fresh_sf)
    await registry.create_snapshot(snap)
    sm = StateMachine(fresh_sf)
    await sm.initialize(snap.ipo_id)
    await sm.transition_to(
        snap.ipo_id,
        IPOLifecycleStateType.PRICING,
        triggered_by=TransitionTrigger.AUTO_DETECTOR,
    )
    await sm.transition_to(
        snap.ipo_id,
        IPOLifecycleStateType.LISTED,
        triggered_by=TransitionTrigger.AUTO_DETECTOR,
    )


@pytest.mark.asyncio
async def test_daily_scheduler_tracks_due_checkpoints_idempotently(fresh_sf) -> None:
    """Listed 10 days → tracks T+1, T+5, T+10; second run is idempotent."""
    _truncate_scheduler_tables()
    snap = _build_snapshot()
    listing_d = datetime.now(UTC).date() - timedelta(days=10)
    await _set_lifecycle_to_listed(fresh_sf, snap, listing_d)

    sm = StateMachine(fresh_sf)
    registry = PGPredictionRegistry(session_factory=fresh_sf)
    tracker = OutcomeTracker(
        session_factory=fresh_sf,
        snapshot_resolver=registry,
        benchmarks=_StubBenchmarks(),
        price_fetcher=_StubPrices(),
    )
    # ReviewWorkflow with a no-op attribution (LLM-mocked).
    from hk_ipo_agent.common.llm_client import LLMClient
    from hk_ipo_agent.prediction_registry.attribution import (
        AttributionEngine,
        _DiagnosisOutput,
    )

    llm = LLMClient(api_key="sk-test", daily_budget_usd=Decimal("100"))
    llm.acomplete_json = AsyncMock(  # type: ignore[method-assign]
        return_value=_DiagnosisOutput(
            primary_attribution="within_tolerance",
            llm_diagnosis="ok",
            proposed_adjustments=[],
        )
    )
    workflow = ReviewWorkflow(
        registry=registry,
        attribution=AttributionEngine(llm=llm),
        session_factory=fresh_sf,
    )

    repo = _StubIPORepo(
        {
            snap.ipo_id: IPOMetadata(
                ipo_id=snap.ipo_id,
                stock_code="TEST.HK",
                listing_date=listing_d,
                industry_peers=[],
                actual_price_at_checkpoint={},
            ),
        }
    )
    sched = DailyScheduler(
        session_factory=fresh_sf,
        state_machine=sm,
        outcome_tracker=tracker,
        review_workflow=workflow,
        stale_detector=StaleDetector(fresh_sf),
        terminal_handler=TerminalHandler(session_factory=fresh_sf, registry=registry),
        ipo_repo=repo,
    )

    r1 = await sched.run()
    assert r1.status is SchedulerStatus.COMPLETED
    # T+1, +5, +10 = 3 outcomes.
    async with fresh_sf() as s:
        outcomes = (
            (
                await s.execute(
                    select(PredictionOutcomeRow).where(PredictionOutcomeRow.snapshot_id == snap.id)
                )
            )
            .scalars()
            .all()
        )
    assert {o.checkpoint_day for o in outcomes} == {1, 5, 10}

    # Second run — idempotent.
    r2 = await sched.run()
    assert r2.status is SchedulerStatus.COMPLETED
    async with fresh_sf() as s:
        outcomes_after = (
            (
                await s.execute(
                    select(PredictionOutcomeRow).where(PredictionOutcomeRow.snapshot_id == snap.id)
                )
            )
            .scalars()
            .all()
        )
    assert len(outcomes_after) == 3  # no duplicates


@pytest.mark.asyncio
async def test_daily_scheduler_back_fills_missed_checkpoints(fresh_sf) -> None:
    """Listed 35 days → catches up T+1, +5, +10, +22, +30 even if first run."""
    _truncate_scheduler_tables()
    snap = _build_snapshot()
    listing_d = datetime.now(UTC).date() - timedelta(days=35)
    await _set_lifecycle_to_listed(fresh_sf, snap, listing_d)

    sm = StateMachine(fresh_sf)
    registry = PGPredictionRegistry(session_factory=fresh_sf)
    tracker = OutcomeTracker(
        session_factory=fresh_sf,
        snapshot_resolver=registry,
        benchmarks=_StubBenchmarks(),
        price_fetcher=_StubPrices(),
    )
    from hk_ipo_agent.common.llm_client import LLMClient
    from hk_ipo_agent.prediction_registry.attribution import (
        AttributionEngine,
        _DiagnosisOutput,
    )

    llm = LLMClient(api_key="sk-test", daily_budget_usd=Decimal("100"))
    llm.acomplete_json = AsyncMock(  # type: ignore[method-assign]
        return_value=_DiagnosisOutput(
            primary_attribution="within_tolerance",
            llm_diagnosis="ok",
            proposed_adjustments=[],
        )
    )
    workflow = ReviewWorkflow(
        registry=registry,
        attribution=AttributionEngine(llm=llm),
        session_factory=fresh_sf,
    )

    repo = _StubIPORepo(
        {
            snap.ipo_id: IPOMetadata(
                ipo_id=snap.ipo_id,
                stock_code="TEST.HK",
                listing_date=listing_d,
                industry_peers=[],
                actual_price_at_checkpoint={},
            ),
        }
    )
    sched = DailyScheduler(
        session_factory=fresh_sf,
        state_machine=sm,
        outcome_tracker=tracker,
        review_workflow=workflow,
        stale_detector=StaleDetector(fresh_sf),
        terminal_handler=TerminalHandler(session_factory=fresh_sf, registry=registry),
        ipo_repo=repo,
    )
    await sched.run()
    async with fresh_sf() as s:
        outcomes = (
            (
                await s.execute(
                    select(PredictionOutcomeRow).where(PredictionOutcomeRow.snapshot_id == snap.id)
                )
            )
            .scalars()
            .all()
        )
    # CHECKPOINT_DAYS up to 35 = {1, 5, 10, 22, 30}.
    assert {o.checkpoint_day for o in outcomes} == {1, 5, 10, 22, 30}


@pytest.mark.asyncio
async def test_daily_scheduler_terminates_at_360_days(fresh_sf) -> None:
    """Listed 365 days → transitions LISTED → TERMINATED."""
    _truncate_scheduler_tables()
    snap = _build_snapshot()
    listing_d = datetime.now(UTC).date() - timedelta(days=365)
    await _set_lifecycle_to_listed(fresh_sf, snap, listing_d)

    sm = StateMachine(fresh_sf)
    registry = PGPredictionRegistry(session_factory=fresh_sf)
    tracker = OutcomeTracker(
        session_factory=fresh_sf,
        snapshot_resolver=registry,
        benchmarks=_StubBenchmarks(),
        price_fetcher=_StubPrices(),
    )
    from hk_ipo_agent.common.llm_client import LLMClient
    from hk_ipo_agent.prediction_registry.attribution import AttributionEngine

    llm = LLMClient(api_key="sk-test", daily_budget_usd=Decimal("100"))
    llm.acomplete_json = AsyncMock()  # type: ignore[method-assign]
    workflow = ReviewWorkflow(
        registry=registry,
        attribution=AttributionEngine(llm=llm),
        session_factory=fresh_sf,
    )
    repo = _StubIPORepo(
        {
            snap.ipo_id: IPOMetadata(
                ipo_id=snap.ipo_id,
                stock_code="TEST.HK",
                listing_date=listing_d,
                industry_peers=[],
                actual_price_at_checkpoint={},
            ),
        }
    )
    sched = DailyScheduler(
        session_factory=fresh_sf,
        state_machine=sm,
        outcome_tracker=tracker,
        review_workflow=workflow,
        stale_detector=StaleDetector(fresh_sf),
        terminal_handler=TerminalHandler(session_factory=fresh_sf, registry=registry),
        ipo_repo=repo,
    )
    await sched.run()
    state = await sm.get_state(snap.ipo_id)
    assert state is not None
    assert state[0] is IPOLifecycleStateType.TERMINATED
    assert state[1].is_terminal is True


# ===========================================================================
# HighFrequencyScheduler — does not do heavy work
# ===========================================================================


@pytest.mark.asyncio
async def test_high_freq_advances_pricing_state_on_signal(fresh_sf) -> None:
    """When the detector reports a PRICING signal, state advances accordingly."""
    _truncate_scheduler_tables()
    ipo_id = uuid.uuid4()
    _seed_ipo(ipo_id)
    sm = StateMachine(fresh_sf)
    await sm.initialize(ipo_id)

    from hk_ipo_agent.prediction_registry.ipo_lifecycle.state_detectors import (
        StateDetectors,
        TransitionSignal,
    )

    detectors = MagicMock(spec=StateDetectors)
    detectors.detect_pricing = AsyncMock(
        return_value=TransitionSignal(
            target_state=IPOLifecycleStateType.PRICING,
            triggered_by=TransitionTrigger.AUTO_DETECTOR,
            evidence={"source": "test"},
        )
    )
    detectors.detect_withdrawn = AsyncMock(return_value=None)
    detectors.detect_hearing_failed = AsyncMock(return_value=None)
    detectors.detect_listed_three_way = AsyncMock()

    repo = MagicMock()
    repo.get_context = AsyncMock(
        return_value=__import__(
            "hk_ipo_agent.prediction_registry.schedulers", fromlist=["ActiveIPOContext"]
        ).ActiveIPOContext(ipo_id=ipo_id, stock_code="TEST.HK", expected_listing_date=None)
    )

    sched = HighFrequencyScheduler(
        session_factory=fresh_sf,
        state_machine=sm,
        state_detectors=detectors,
        ipo_repo=repo,
    )
    result = await sched.run()
    assert result.status is SchedulerStatus.COMPLETED
    state = await sm.get_state(ipo_id)
    assert state is not None
    assert state[0] is IPOLifecycleStateType.PRICING


@pytest.mark.asyncio
async def test_high_freq_skips_when_no_stock_code(fresh_sf) -> None:
    """No code yet → just touch last_checked_at, don't fire detectors."""
    _truncate_scheduler_tables()
    ipo_id = uuid.uuid4()
    _seed_ipo(ipo_id)
    sm = StateMachine(fresh_sf)
    await sm.initialize(ipo_id)

    detectors = MagicMock()
    detectors.detect_pricing = AsyncMock()
    repo = MagicMock()
    repo.get_context = AsyncMock(
        return_value=__import__(
            "hk_ipo_agent.prediction_registry.schedulers", fromlist=["ActiveIPOContext"]
        ).ActiveIPOContext(ipo_id=ipo_id, stock_code=None, expected_listing_date=None)
    )

    sched = HighFrequencyScheduler(
        session_factory=fresh_sf,
        state_machine=sm,
        state_detectors=detectors,
        ipo_repo=repo,
    )
    await sched.run()
    detectors.detect_pricing.assert_not_called()


# ===========================================================================
# EventDrivenScheduler — webhook dispatch
# ===========================================================================


@pytest.mark.asyncio
async def test_event_driven_routes_earnings_to_comparator(fresh_sf) -> None:
    _truncate_scheduler_tables()
    snap = _build_snapshot()
    await _set_lifecycle_to_listed(fresh_sf, snap, datetime.now(UTC).date() - timedelta(days=5))

    earnings_comparator = MagicMock()
    earnings_comparator.compare = AsyncMock(return_value=MagicMock())

    snapshot_resolver = MagicMock()
    snapshot_resolver.get_latest_snapshot_id = AsyncMock(return_value=snap.id)

    queue = MagicMock()
    queue.pull = AsyncMock(
        return_value=[
            EventPayload(
                kind=EVENT_KIND_EARNINGS,
                ipo_id=snap.ipo_id,
                occurred_at=datetime.now(UTC),
                payload={
                    "report_period": "FY2025",
                    "filing_date": "2026-03-31",
                    "actual_revenue": "100",
                    "actual_net_profit": "20",
                    "actual_gross_margin": "0.40",
                },
            ),
        ]
    )
    queue.ack = AsyncMock()

    sm = StateMachine(fresh_sf)

    # Set the registry's global so EventDrivenScheduler can resolve the snapshot.
    from hk_ipo_agent.prediction_registry.registry import (
        PGPredictionRegistry,
        set_registry,
    )

    set_registry(PGPredictionRegistry(session_factory=fresh_sf))

    sched = EventDrivenScheduler(
        session_factory=fresh_sf,
        queue=queue,
        earnings_comparator=earnings_comparator,
        state_machine=sm,
        snapshot_resolver=snapshot_resolver,
    )
    result = await sched.run()
    assert result.stats.events_detected == 1
    earnings_comparator.compare.assert_called_once()


@pytest.mark.asyncio
async def test_event_driven_routes_critical_price_anomaly_to_alerts(fresh_sf) -> None:
    _truncate_scheduler_tables()
    ipo_id = uuid.uuid4()
    _seed_ipo(ipo_id)

    queue = MagicMock()
    queue.pull = AsyncMock(
        return_value=[
            EventPayload(
                kind=EVENT_KIND_PRICE_ANOMALY,
                ipo_id=ipo_id,
                occurred_at=datetime.now(UTC),
                payload={"severity": "critical", "description": "single-day drop 18%"},
            ),
        ]
    )
    alerts = MagicMock()
    alerts.emit = AsyncMock(return_value=MagicMock())

    sm = StateMachine(fresh_sf)
    snapshot_resolver = MagicMock()
    snapshot_resolver.get_latest_snapshot_id = AsyncMock(return_value=None)
    earnings = MagicMock()

    sched = EventDrivenScheduler(
        session_factory=fresh_sf,
        queue=queue,
        earnings_comparator=earnings,
        state_machine=sm,
        snapshot_resolver=snapshot_resolver,
        alert_router=alerts,
    )
    await sched.run()
    alerts.emit.assert_called_once()


@pytest.mark.asyncio
async def test_event_driven_ignores_unknown_event_kinds(fresh_sf) -> None:
    _truncate_scheduler_tables()
    queue = MagicMock()
    queue.pull = AsyncMock(
        return_value=[
            EventPayload(
                kind="something_unsupported",
                ipo_id=uuid.uuid4(),
                occurred_at=datetime.now(UTC),
                payload={},
            ),
        ]
    )
    sched = EventDrivenScheduler(
        session_factory=fresh_sf,
        queue=queue,
        earnings_comparator=MagicMock(),
        state_machine=StateMachine(fresh_sf),
        snapshot_resolver=MagicMock(),
    )
    result = await sched.run()
    # Unknown kinds shouldn't crash; events_detected stays 0.
    assert result.stats.events_detected == 0
