"""IPO lifecycle state machine + detectors tests — Phase 7.5c per ADR 0012.

Three required state-machine simulations (per PROJECT_SPEC.md §3.11.1 DONE):
- "normal listing":  PRE_LISTING → PRICING → LISTED → TERMINATED
- "withdrawn":       PRE_LISTING → WITHDRAWN + terminal_review_draft
- "silent expiry":   PRE_LISTING 181 days → stale_detector CRITICAL

Plus invariant tests:
- VALID_TRANSITIONS rejects any backwards / sideways transition
- LISTED three-way validation requires all three sub-checks
- terminal_handler is idempotent on (snapshot_id, checkpoint_day=-1)
- AH context returns the H-share listing date for checkpoint anchoring
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

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
    TransitionTrigger,
)
from hk_ipo_agent.common.exceptions import InvalidStateTransition
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
from hk_ipo_agent.data.models import IPOLifecycleStateRow, IPOStateTransitionRow
from hk_ipo_agent.prediction_registry.ipo_lifecycle import (
    PRE_LISTING_STALE_DAYS,
    PRICING_STALE_DAYS,
    TERMINAL_CHECKPOINT_DAY,
    AHContext,
    AHSpecialHandler,
    StaleDetector,
    StateDetectors,
    StateMachine,
    StateMachineError,
    TerminalHandler,
    ThreeWayValidation,
    assert_valid_transition,
    can_transition,
    days_in_state,
    initial_state,
    is_terminal,
)
from hk_ipo_agent.prediction_registry.registry import PGPredictionRegistry
from hk_ipo_agent.prediction_registry.snapshot import build_snapshot


def _sync_dsn() -> str:
    return get_settings().database.url.replace("postgresql+asyncpg://", "postgresql://", 1)


@pytest_asyncio.fixture
async def fresh_sf():
    """NullPool engine + sessionmaker bound to per-test event loop."""
    engine = create_async_engine(get_settings().database.url, poolclass=NullPool)
    sf = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    yield sf
    await engine.dispose()


def _truncate_lifecycle() -> None:
    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "TRUNCATE TABLE prediction_reviews, prediction_outcomes, post_ipo_events, "
            "prediction_snapshots, ipo_state_transitions, ipo_lifecycle_states, "
            "ipo_events RESTART IDENTITY CASCADE"
        )
        conn.commit()


def _seed_ipo(ipo_id: uuid.UUID) -> None:
    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ipo_events (id, stock_code, company_name_zh, listing_type, "
            "created_at, updated_at) VALUES (%s, %s, %s, %s, NOW(), NOW())",
            (ipo_id, "TEST.HK", "Test", "mainboard_tech"),
        )
        conn.commit()


# ===========================================================================
# states.py — pure-function predicate tests
# ===========================================================================


def test_initial_state_is_pre_listing() -> None:
    assert initial_state() is IPOLifecycleStateType.PRE_LISTING


def test_can_transition_pre_to_pricing() -> None:
    assert can_transition(IPOLifecycleStateType.PRE_LISTING, IPOLifecycleStateType.PRICING)
    assert can_transition(IPOLifecycleStateType.PRICING, IPOLifecycleStateType.LISTED)
    assert can_transition(IPOLifecycleStateType.LISTED, IPOLifecycleStateType.TERMINATED)


def test_no_backwards_transitions_allowed() -> None:
    # Backwards transitions are explicitly disallowed (CLAUDE.md v1.2).
    assert not can_transition(IPOLifecycleStateType.LISTED, IPOLifecycleStateType.PRE_LISTING)
    assert not can_transition(IPOLifecycleStateType.PRICING, IPOLifecycleStateType.PRE_LISTING)
    assert not can_transition(IPOLifecycleStateType.TERMINATED, IPOLifecycleStateType.LISTED)


def test_terminal_states_have_no_outgoing_transitions() -> None:
    for s in (
        IPOLifecycleStateType.WITHDRAWN,
        IPOLifecycleStateType.HEARING_FAILED,
        IPOLifecycleStateType.PRICING_PULLED,
        IPOLifecycleStateType.TERMINATED,
    ):
        assert is_terminal(s)
        # No state should be reachable from a terminal one.
        for target in IPOLifecycleStateType:
            assert not can_transition(s, target), (
                f"terminal state {s.value} should not transition to {target.value}"
            )


def test_assert_valid_transition_raises_with_helpful_message() -> None:
    with pytest.raises(InvalidStateTransition) as exc:
        assert_valid_transition(
            IPOLifecycleStateType.LISTED, IPOLifecycleStateType.PRICING
        )
    msg = str(exc.value)
    assert "listed" in msg.lower() and "pricing" in msg.lower()
    assert "allowed" in msg.lower()


# ===========================================================================
# state_machine.py — read + write transitions with audit trail
# ===========================================================================


@pytest.mark.asyncio
async def test_initialize_writes_pre_listing_state_and_audit_row(fresh_sf) -> None:
    _truncate_lifecycle()
    ipo_id = uuid.uuid4()
    _seed_ipo(ipo_id)
    sm = StateMachine(fresh_sf)
    row = await sm.initialize(ipo_id)
    assert row.current_state == "pre_listing"
    state = await sm.get_state(ipo_id)
    assert state is not None
    assert state[0] is IPOLifecycleStateType.PRE_LISTING
    # Audit trail row exists.
    async with fresh_sf() as s:
        transitions = (
            await s.execute(select(IPOStateTransitionRow).where(IPOStateTransitionRow.ipo_id == ipo_id))
        ).scalars().all()
    assert len(transitions) == 1
    assert transitions[0].from_state is None
    assert transitions[0].to_state == "pre_listing"


@pytest.mark.asyncio
async def test_transition_to_writes_state_and_audit(fresh_sf) -> None:
    _truncate_lifecycle()
    ipo_id = uuid.uuid4()
    _seed_ipo(ipo_id)
    sm = StateMachine(fresh_sf)
    await sm.initialize(ipo_id)
    await sm.transition_to(
        ipo_id, IPOLifecycleStateType.PRICING,
        triggered_by=TransitionTrigger.AUTO_DETECTOR,
        evidence={"source": "test"},
    )
    state = await sm.get_state(ipo_id)
    assert state is not None and state[0] is IPOLifecycleStateType.PRICING
    async with fresh_sf() as s:
        transitions = (
            await s.execute(select(IPOStateTransitionRow).where(IPOStateTransitionRow.ipo_id == ipo_id))
        ).scalars().all()
    assert len(transitions) == 2  # init + pricing


@pytest.mark.asyncio
async def test_transition_rejects_invalid_target(fresh_sf) -> None:
    _truncate_lifecycle()
    ipo_id = uuid.uuid4()
    _seed_ipo(ipo_id)
    sm = StateMachine(fresh_sf)
    await sm.initialize(ipo_id)
    with pytest.raises(InvalidStateTransition):
        await sm.transition_to(
            ipo_id, IPOLifecycleStateType.LISTED,  # skipping PRICING is illegal
            triggered_by=TransitionTrigger.AUTO_DETECTOR,
        )


@pytest.mark.asyncio
async def test_transition_without_initialize_raises(fresh_sf) -> None:
    _truncate_lifecycle()
    ipo_id = uuid.uuid4()
    _seed_ipo(ipo_id)
    sm = StateMachine(fresh_sf)
    with pytest.raises(StateMachineError, match="no lifecycle row"):
        await sm.transition_to(
            ipo_id, IPOLifecycleStateType.PRICING,
            triggered_by=TransitionTrigger.AUTO_DETECTOR,
        )


# ===========================================================================
# state_detectors.py — LISTED three-way validation
# ===========================================================================


def _stub_announcement_source(*, listings: list | None = None, filings: list | None = None):
    src = MagicMock()
    src.get_listing_documents = AsyncMock(return_value=listings or [])
    src.get_disclosure_filings = AsyncMock(return_value=filings or [])
    return src


def _stub_ifind(stock_close: dict | None = None):
    src = MagicMock()
    src.get_hk_history_prices = AsyncMock(
        return_value={"data": [stock_close] if stock_close else []}
    )
    return src


def _stub_code_resolver(active: bool):
    src = MagicMock()
    src.is_code_active = AsyncMock(return_value=active)
    return src


@pytest.mark.asyncio
async def test_three_way_validation_all_three_pass() -> None:
    detectors = StateDetectors(
        announcements=_stub_announcement_source(
            listings=[{"title": "Listing announcement"}]
        ),
        ifind=_stub_ifind({"time": "2026-06-01", "thscode": "TEST.HK", "close": 12.5}),
        code_resolver=_stub_code_resolver(active=True),
    )
    result = await detectors.detect_listed_three_way(
        uuid.uuid4(), stock_code="TEST.HK", expected_listing_date=date(2026, 6, 1),
    )
    assert isinstance(result, ThreeWayValidation)
    assert result.passed
    assert result.hkex_listing_announcement
    assert result.ifind_first_day_quote
    assert result.stock_code_active


@pytest.mark.asyncio
async def test_three_way_validation_blocks_when_hkex_missing() -> None:
    detectors = StateDetectors(
        announcements=_stub_announcement_source(listings=[]),  # no HKEX announcement
        ifind=_stub_ifind({"time": "2026-06-01", "thscode": "TEST.HK", "close": 12.5}),
        code_resolver=_stub_code_resolver(active=True),
    )
    result = await detectors.detect_listed_three_way(
        uuid.uuid4(), stock_code="TEST.HK", expected_listing_date=date(2026, 6, 1),
    )
    assert not result.passed
    assert not result.hkex_listing_announcement


@pytest.mark.asyncio
async def test_three_way_validation_blocks_when_ifind_missing() -> None:
    detectors = StateDetectors(
        announcements=_stub_announcement_source(listings=[{"title": "ann"}]),
        ifind=_stub_ifind(None),  # no quote
        code_resolver=_stub_code_resolver(active=True),
    )
    result = await detectors.detect_listed_three_way(
        uuid.uuid4(), stock_code="TEST.HK", expected_listing_date=date(2026, 6, 1),
    )
    assert not result.passed
    assert not result.ifind_first_day_quote


@pytest.mark.asyncio
async def test_three_way_validation_blocks_when_code_inactive() -> None:
    detectors = StateDetectors(
        announcements=_stub_announcement_source(listings=[{"title": "ann"}]),
        ifind=_stub_ifind({"time": "2026-06-01", "thscode": "TEST.HK", "close": 12.5}),
        code_resolver=_stub_code_resolver(active=False),
    )
    result = await detectors.detect_listed_three_way(
        uuid.uuid4(), stock_code="TEST.HK", expected_listing_date=date(2026, 6, 1),
    )
    assert not result.passed
    assert not result.stock_code_active


@pytest.mark.asyncio
async def test_detect_withdrawn_picks_up_filing_keyword() -> None:
    detectors = StateDetectors(
        announcements=_stub_announcement_source(
            filings=[{"title": "撤回上市申请", "filing_date": "2026-04-01"}]
        ),
        ifind=_stub_ifind(),
        code_resolver=_stub_code_resolver(active=False),
    )
    sig = await detectors.detect_withdrawn(uuid.uuid4(), stock_code="TEST.HK")
    assert sig is not None
    assert sig.target_state is IPOLifecycleStateType.WITHDRAWN


@pytest.mark.asyncio
async def test_detect_hearing_failed_picks_up_keyword() -> None:
    detectors = StateDetectors(
        announcements=_stub_announcement_source(
            filings=[{"title": "聆讯失败", "filing_date": "2026-04-01"}]
        ),
        ifind=_stub_ifind(),
        code_resolver=_stub_code_resolver(active=False),
    )
    sig = await detectors.detect_hearing_failed(uuid.uuid4(), stock_code="TEST.HK")
    assert sig is not None
    assert sig.target_state is IPOLifecycleStateType.HEARING_FAILED


# ===========================================================================
# stale_detector.py — silent expiry detection
# ===========================================================================


@pytest.mark.asyncio
async def test_stale_detector_flags_pre_listing_over_180d(fresh_sf) -> None:
    _truncate_lifecycle()
    ipo_id = uuid.uuid4()
    _seed_ipo(ipo_id)
    old = datetime.now(UTC) - timedelta(days=PRE_LISTING_STALE_DAYS + 5)
    async with fresh_sf() as s:
        s.add(IPOLifecycleStateRow(
            ipo_id=ipo_id,
            current_state=IPOLifecycleStateType.PRE_LISTING.value,
            state_entered_at=old,
            last_checked_at=old,
            is_terminal=False,
        ))
        await s.commit()
    detector = StaleDetector(fresh_sf)
    signals = await detector.scan()
    assert len(signals) == 1
    sig = signals[0]
    assert sig.state is IPOLifecycleStateType.PRE_LISTING
    assert sig.days_in_state > PRE_LISTING_STALE_DAYS
    assert sig.severity.value == "critical"
    assert sig.actionable_info


@pytest.mark.asyncio
async def test_stale_detector_flags_pricing_over_21d(fresh_sf) -> None:
    _truncate_lifecycle()
    ipo_id = uuid.uuid4()
    _seed_ipo(ipo_id)
    old = datetime.now(UTC) - timedelta(days=PRICING_STALE_DAYS + 5)
    async with fresh_sf() as s:
        s.add(IPOLifecycleStateRow(
            ipo_id=ipo_id,
            current_state=IPOLifecycleStateType.PRICING.value,
            state_entered_at=old,
            last_checked_at=old,
            is_terminal=False,
        ))
        await s.commit()
    detector = StaleDetector(fresh_sf)
    signals = await detector.scan()
    assert len(signals) == 1
    assert signals[0].severity.value == "warning"


@pytest.mark.asyncio
async def test_stale_detector_skips_terminal_rows(fresh_sf) -> None:
    _truncate_lifecycle()
    ipo_id = uuid.uuid4()
    _seed_ipo(ipo_id)
    old = datetime.now(UTC) - timedelta(days=400)
    async with fresh_sf() as s:
        s.add(IPOLifecycleStateRow(
            ipo_id=ipo_id,
            current_state=IPOLifecycleStateType.WITHDRAWN.value,
            state_entered_at=old,
            last_checked_at=old,
            is_terminal=True,
        ))
        await s.commit()
    detector = StaleDetector(fresh_sf)
    assert await detector.scan() == []


def test_days_in_state_simple_diff() -> None:
    assert (
        days_in_state(
            datetime(2026, 1, 1, tzinfo=UTC), as_of=datetime(2026, 1, 30, tzinfo=UTC),
        )
        == 29
    )


# ===========================================================================
# ah_special.py
# ===========================================================================


def test_ah_context_returns_h_share_listing_date() -> None:
    ctx = AHSpecialHandler.from_ipo_metadata(
        uuid.uuid4(),
        h_share_code="2628.HK", a_share_code="601628.SH",
        h_listing_date=date(2026, 5, 1),
        a_listing_date=date(2015, 5, 1),
    )
    assert ctx.is_ah_pair
    assert ctx.checkpoint_anchor_date == date(2026, 5, 1)
    # T+30 checkpoint anchored on H-share listing date.
    target = AHSpecialHandler.resolve_checkpoint_date(ctx, 30)
    assert target == date(2026, 5, 31)


def test_ah_context_for_hk_only_ipo_has_no_a_share() -> None:
    ctx = AHSpecialHandler.from_ipo_metadata(
        uuid.uuid4(),
        h_share_code="2228.HK", a_share_code=None,
        h_listing_date=date(2026, 5, 1), a_listing_date=None,
    )
    assert not ctx.is_ah_pair
    assert ctx.checkpoint_anchor_date == date(2026, 5, 1)


def test_ah_discount_tags_findings_for_ah_pairs() -> None:
    ctx = AHContext(
        ipo_id=uuid.uuid4(), is_ah_pair=True,
        h_share_code="2628.HK", a_share_code="601628.SH",
        h_listing_date=date(2026, 5, 1), a_listing_date=date(2015, 5, 1),
    )
    findings = [{"statement": "earnings concern"}]
    tagged = AHSpecialHandler.discount_pre_listing_signals(ctx, findings)
    assert tagged[0]["pre_discounted_in_a_share"] is True


def test_ah_discount_passthrough_for_hk_only() -> None:
    ctx = AHContext(
        ipo_id=uuid.uuid4(), is_ah_pair=False,
        h_share_code="2228.HK", a_share_code=None,
        h_listing_date=date(2026, 5, 1), a_listing_date=None,
    )
    findings = [{"statement": "earnings concern"}]
    assert AHSpecialHandler.discount_pre_listing_signals(ctx, findings) is findings


# ===========================================================================
# terminal_handlers.py — withdrawn / hearing_failed processing
# ===========================================================================


def _build_snapshot():
    d = ValuationDistribution(
        p10=Decimal("9"), p25=Decimal("9.5"), p50=Decimal("10"),
        p75=Decimal("10.5"), p90=Decimal("11"),
        mean=Decimal("10"), std=Decimal("0.5"),
    )
    return build_snapshot(
        ipo_id=uuid.uuid4(),
        extraction=ProspectusExtraction(
            prospectus_id=f"P-TH-{uuid.uuid4().hex[:6]}",
            company_name_zh="测试", listing_type=ListingType.MAINBOARD_TECH,
            industry_code="TECH", industry_description="AI", business_model="B2B",
            extraction_version="0.0.1", extracted_at=datetime.now(UTC),
        ),
        agent_outputs={
            "fundamental": AgentOutput(
                agent_role=AgentRole.FUNDAMENTAL, scores={"x": 70.0},
                overall_score=70.0, runtime_seconds=0.1,
            ),
        },
        valuation=ValuationEnsembleOutput(
            company_id="P-TH-1",
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
        total_cost_usd=Decimal("0.05"), runtime_seconds=5.0,
    )


@pytest.mark.asyncio
async def test_terminal_handler_writes_outcome_and_review(fresh_sf) -> None:
    _truncate_lifecycle()
    snap = _build_snapshot()
    _seed_ipo(snap.ipo_id)
    registry = PGPredictionRegistry(session_factory=fresh_sf)
    await registry.create_snapshot(snap)
    handler = TerminalHandler(session_factory=fresh_sf, registry=registry)
    result = await handler.handle(
        ipo_id=snap.ipo_id, terminal_state=IPOLifecycleStateType.WITHDRAWN,
    )
    assert result.outcome_id is not None
    assert result.review_id is not None
    # Idempotent re-run: outcome not duplicated.
    second = await handler.handle(
        ipo_id=snap.ipo_id, terminal_state=IPOLifecycleStateType.WITHDRAWN,
    )
    assert second.outcome_id is None  # already exists


@pytest.mark.asyncio
async def test_terminal_handler_skips_non_terminal_state(fresh_sf) -> None:
    handler = TerminalHandler(
        session_factory=fresh_sf,
        registry=PGPredictionRegistry(session_factory=fresh_sf),
    )
    result = await handler.handle(
        ipo_id=uuid.uuid4(), terminal_state=IPOLifecycleStateType.PRICING,
    )
    assert result.skipped
    assert result.outcome_id is None


# ===========================================================================
# 3 required end-to-end simulations (PROJECT_SPEC.md §3.11.1 DONE)
# ===========================================================================


@pytest.mark.asyncio
async def test_simulation_normal_listing_full_lifecycle(fresh_sf) -> None:
    """PRE_LISTING → PRICING → LISTED → TERMINATED, audit trail intact."""
    _truncate_lifecycle()
    ipo_id = uuid.uuid4()
    _seed_ipo(ipo_id)
    sm = StateMachine(fresh_sf)
    await sm.initialize(ipo_id)
    await sm.transition_to(
        ipo_id, IPOLifecycleStateType.PRICING,
        triggered_by=TransitionTrigger.AUTO_DETECTOR,
    )
    await sm.transition_to(
        ipo_id, IPOLifecycleStateType.LISTED,
        triggered_by=TransitionTrigger.AUTO_DETECTOR,
    )
    await sm.transition_to(
        ipo_id, IPOLifecycleStateType.TERMINATED,
        triggered_by=TransitionTrigger.AUTO_DETECTOR,
    )
    state = await sm.get_state(ipo_id)
    assert state is not None
    assert state[0] is IPOLifecycleStateType.TERMINATED
    assert state[1].is_terminal is True

    async with fresh_sf() as s:
        transitions = (
            await s.execute(select(IPOStateTransitionRow).where(IPOStateTransitionRow.ipo_id == ipo_id))
        ).scalars().all()
    # init (None → PRE) + PRE → PRICING + PRICING → LISTED + LISTED → TERMINATED
    assert len(transitions) == 4
    state_sequence = [t.to_state for t in sorted(transitions, key=lambda r: r.transition_at)]
    assert state_sequence == ["pre_listing", "pricing", "listed", "terminated"]


@pytest.mark.asyncio
async def test_simulation_withdrawn_path_writes_terminal_review(fresh_sf) -> None:
    """PRE_LISTING → WITHDRAWN + terminal_review_draft auto-generated."""
    _truncate_lifecycle()
    snap = _build_snapshot()
    _seed_ipo(snap.ipo_id)
    registry = PGPredictionRegistry(session_factory=fresh_sf)
    await registry.create_snapshot(snap)
    sm = StateMachine(fresh_sf)
    handler = TerminalHandler(session_factory=fresh_sf, registry=registry)
    await sm.initialize(snap.ipo_id)
    await sm.transition_to(
        snap.ipo_id, IPOLifecycleStateType.WITHDRAWN,
        triggered_by=TransitionTrigger.AUTO_DETECTOR,
        evidence={"source": "hkex_filing", "title": "撤回上市申请"},
    )
    result = await handler.handle(
        ipo_id=snap.ipo_id, terminal_state=IPOLifecycleStateType.WITHDRAWN,
    )
    assert result.review_id is not None
    # Outcome row uses checkpoint_day = -1 (sentinel).
    async with fresh_sf() as s:
        from hk_ipo_agent.data.models import PredictionOutcomeRow  # noqa: PLC0415

        outcome = (
            await s.execute(
                select(PredictionOutcomeRow)
                .where(PredictionOutcomeRow.snapshot_id == snap.id)
            )
        ).scalar_one()
    assert outcome.checkpoint_day == TERMINAL_CHECKPOINT_DAY


@pytest.mark.asyncio
async def test_simulation_silent_expiry_triggers_critical_alert(fresh_sf) -> None:
    """PRE_LISTING stuck > 180 days → stale_detector emits CRITICAL."""
    _truncate_lifecycle()
    ipo_id = uuid.uuid4()
    _seed_ipo(ipo_id)
    stale_age = datetime.now(UTC) - timedelta(days=PRE_LISTING_STALE_DAYS + 1)
    async with fresh_sf() as s:
        s.add(IPOLifecycleStateRow(
            ipo_id=ipo_id,
            current_state=IPOLifecycleStateType.PRE_LISTING.value,
            state_entered_at=stale_age,
            last_checked_at=stale_age,
            is_terminal=False,
        ))
        await s.commit()
    detector = StaleDetector(fresh_sf)
    signals = await detector.scan()
    assert len(signals) == 1
    sig = signals[0]
    assert sig.severity.value == "critical"
    assert "PRE_LISTING" in sig.message
    assert "招股书" in sig.message
    # Actionable info is mandatory (CLAUDE.md v1.2).
    assert sig.actionable_info and len(sig.actionable_info) > 20
