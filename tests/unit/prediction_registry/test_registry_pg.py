"""PGPredictionRegistry integration tests — Phase 7.5a per ADR 0012.

Per PROJECT_SPEC.md §9 spirit ("integration tests hit real DB, no mocks"):
these tests connect to the docker postgres instance brought up by
``make db-up``. CI runs the same docker compose stack, so behaviour is
authoritative.

Covers:
- create → read round-trip preserves SHA-256 hash integrity
- duplicate snapshot id raises ``SnapshotIntegrityError``
- DB trigger ``snapshot_no_update`` blocks UPDATE
- DB trigger ``snapshot_no_delete`` blocks DELETE
- ``attach_review`` appends to ``prediction_reviews`` without touching the snapshot row
- ``list_active_predictions`` filters by ``window_days``
- ``set_registry`` swap is observable via ``get_registry``
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from hk_ipo_agent.common.enums import (
    AdjustmentStatus,
    AgentRole,
    DecisionType,
    ListingType,
)
from hk_ipo_agent.common.schemas import (
    AgentErrorAnalysis,
    AgentOutput,
    Attribution,
    DebateOutput,
    DebateQualityAnalysis,
    FinalDecision,
    PredictionReview,
    PredictionSnapshot,
    ProspectusExtraction,
    SingleModelValuation,
    ValuationDistribution,
    ValuationEnsembleOutput,
)
from hk_ipo_agent.common.settings import get_settings
from hk_ipo_agent.data.database import (
    async_session_factory,
)
from hk_ipo_agent.prediction_registry.registry import (
    InMemoryPredictionRegistry,
    PGPredictionRegistry,
    PredictionRegistryProtocol,
    get_registry,
    reset_registry,
    set_registry,
)
from hk_ipo_agent.prediction_registry.snapshot import (
    SnapshotIntegrityError,
    build_snapshot,
)


async def _ensure_ipo_event(session_factory: async_sessionmaker, ipo_id: uuid.UUID) -> None:
    """Insert a stub ipo_event row so prediction_snapshots FK passes."""
    async with session_factory() as s:
        await s.execute(
            text(
                "INSERT INTO ipo_events (id, stock_code, company_name_zh, listing_type, "
                "created_at, updated_at) VALUES (:id, :code, :name, :lt, NOW(), NOW()) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": ipo_id, "code": "TEST.HK", "name": "Test Co.", "lt": "mainboard_tech"},
        )
        await s.commit()


def _build() -> PredictionSnapshot:
    ext = ProspectusExtraction(
        prospectus_id=f"P-PG-{uuid.uuid4().hex[:6]}",
        company_name_zh="测试 PG",
        listing_type=ListingType.MAINBOARD_TECH,
        industry_code="TECH",
        industry_description="AI",
        business_model="B2B",
        extraction_version="0.0.1",
        extracted_at=datetime.now(UTC),
    )
    dist = ValuationDistribution(
        p10=Decimal("90"), p25=Decimal("95"), p50=Decimal("100"),
        p75=Decimal("105"), p90=Decimal("110"),
        mean=Decimal("100"), std=Decimal("5"),
    )
    val = ValuationEnsembleOutput(
        company_id=ext.prospectus_id,
        single_models=[SingleModelValuation(model_name="x", applicable=True, valuation_distribution=dist)],
        weights_used={"x": 1.0},
        ensemble_distribution=dist,
        implied_price_range={"low": Decimal("95"), "fair": Decimal("100"), "high": Decimal("105")},
    )
    decision = FinalDecision(
        decision=DecisionType.PARTIAL,
        confidence=0.7,
        suggested_allocation_pct=0.02,
        price_range_low=Decimal("95"),
        price_range_fair=Decimal("100"),
        price_range_high=Decimal("105"),
        expected_return_6m=dist,
        expected_return_12m=dist,
    )
    return build_snapshot(
        ipo_id=uuid.uuid4(),
        extraction=ext,
        agent_outputs={
            "fundamental": AgentOutput(
                agent_role=AgentRole.FUNDAMENTAL,
                scores={"x": 70.0},
                overall_score=70.0,
                runtime_seconds=0.1,
            )
        },
        valuation=val,
        debate=DebateOutput(final_consensus="balanced"),
        decision=decision,
        total_cost_usd=Decimal("0.05"),
        runtime_seconds=10.0,
    )


@pytest_asyncio.fixture
async def pg_registry():
    """Fresh PG registry over the docker postgres instance.

    Each test builds its own AsyncEngine (NullPool, tied to this
    event loop) instead of sharing the lru_cached project-wide engine.
    The shared engine reuses connections across pytest event loops,
    which triggers "Event loop is closed" errors during asyncpg
    teardown. This fixture is single-purpose so the test pollution
    stays contained.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker as _amsk  # noqa: PLC0415
    from sqlalchemy.pool import NullPool  # noqa: PLC0415

    db_url = get_settings().database.url
    engine = create_async_engine(db_url, poolclass=NullPool, echo=False)
    sf = _amsk(bind=engine, expire_on_commit=False, autoflush=False)
    async with sf() as s:
        await s.execute(
            text(
                "TRUNCATE TABLE prediction_reviews, prediction_outcomes, "
                "post_ipo_events, prediction_snapshots, audit_logs, ipo_events "
                "RESTART IDENTITY CASCADE"
            )
        )
        await s.commit()
    try:
        yield PGPredictionRegistry(session_factory=sf), sf
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_create_and_get_roundtrip(pg_registry) -> None:
    reg, sf = pg_registry
    snap = _build()
    await _ensure_ipo_event(sf, snap.ipo_id)
    snap_id = await reg.create_snapshot(snap)
    assert snap_id == snap.id
    fetched = await reg.get_snapshot(snap_id)
    assert fetched.id == snap.id
    assert fetched.input_data_hash == snap.input_data_hash
    assert fetched.decision.decision == DecisionType.PARTIAL


@pytest.mark.asyncio
async def test_duplicate_id_raises(pg_registry) -> None:
    reg, sf = pg_registry
    snap = _build()
    await _ensure_ipo_event(sf, snap.ipo_id)
    await reg.create_snapshot(snap)
    with pytest.raises(SnapshotIntegrityError):
        await reg.create_snapshot(snap)


@pytest.mark.asyncio
async def test_get_unknown_raises_key_error(pg_registry) -> None:
    reg, _ = pg_registry
    with pytest.raises(KeyError):
        await reg.get_snapshot(uuid.uuid4())


@pytest.mark.asyncio
async def test_db_trigger_blocks_update(pg_registry) -> None:
    """Adversarial: direct SQL UPDATE on prediction_snapshots must fail."""
    reg, sf = pg_registry
    snap = await _create_and_get(reg, sf)
    async with sf() as s:
        with pytest.raises(Exception) as exc_info:
            await s.execute(
                text("UPDATE prediction_snapshots SET system_version = 'hacked' WHERE id = :i"),
                {"i": snap.id},
            )
            await s.commit()
        assert "immutable" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_db_trigger_blocks_delete(pg_registry) -> None:
    """Adversarial: direct SQL DELETE on prediction_snapshots must fail."""
    reg, sf = pg_registry
    snap = await _create_and_get(reg, sf)
    async with sf() as s:
        with pytest.raises(Exception) as exc_info:
            await s.execute(
                text("DELETE FROM prediction_snapshots WHERE id = :i"),
                {"i": snap.id},
            )
            await s.commit()
        assert "immutable" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_attach_review_appends_to_prediction_reviews(pg_registry) -> None:
    reg, sf = pg_registry
    snap = await _create_and_get(reg, sf)
    review = PredictionReview(
        snapshot_id=snap.id,
        review_checkpoint_day=30,
        reviewer="alice",
        what_we_got_right="cornerstone read",
        what_we_got_wrong="missed margin compression",
        primary_attribution="valuation_model",
        attribution_details=Attribution(
            snapshot_id=snap.id,
            checkpoint_day=30,
            agent_errors=[
                AgentErrorAnalysis(
                    agent_role=AgentRole.FUNDAMENTAL,
                    score_calibration=0.5,
                    findings_accuracy=0.7,
                )
            ],
            valuation_errors=[],
            debate_quality=DebateQualityAnalysis(
                bear_predictions_validated=2,
                bear_predictions_total=4,
                bull_predictions_validated=3,
                bull_predictions_total=4,
            ),
            primary_attribution="valuation_model",
            llm_diagnosis="ensemble overestimated growth tail",
            proposed_adjustments=[],
        ),
        adjustment_status=AdjustmentStatus.PROPOSED,
        created_at=datetime.now(UTC),
    )
    review_id = await reg.attach_review(snap.id, review)
    assert isinstance(review_id, uuid.UUID)
    # Snapshot row itself unchanged — UPDATE trigger never fired.
    refetched = await reg.get_snapshot(snap.id)
    assert refetched.input_data_hash == snap.input_data_hash


@pytest.mark.asyncio
async def test_list_active_predictions_filters_window(pg_registry) -> None:
    """list_active_predictions(window_days=N) returns only snapshots in window."""
    reg, sf = pg_registry
    fresh = _build()
    await _ensure_ipo_event(sf, fresh.ipo_id)
    await reg.create_snapshot(fresh)
    # Insert an "old" snapshot directly, bypassing build_snapshot's "now" stamp.
    old_snap_id = uuid.uuid4()
    old_ipo_id = uuid.uuid4()
    await _ensure_ipo_event(sf, old_ipo_id)
    old_age = datetime.now(UTC) - timedelta(days=400)
    async with sf() as s:
        await s.execute(
            text(
                "INSERT INTO prediction_snapshots "
                "(id, ipo_id, as_of_date, prospectus_version, input_data_hash, "
                " input_data_snapshot, agent_outputs, valuation_output, debate_output, "
                " decision, system_version, model_versions, config_snapshot, "
                " total_cost_usd, runtime_seconds, created_at) "
                "VALUES (:id, :ipo, :asof, 'PHIP', :h, '{}'::jsonb, '{}'::jsonb, "
                " '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, '0.0.1', '{}'::jsonb, '{}'::jsonb, "
                " 0.0, 0.0, :ts)"
            ),
            {"id": old_snap_id, "ipo": old_ipo_id, "asof": old_age.date(),
             "h": "0" * 64, "ts": old_age},
        )
        await s.commit()
    active = await reg.list_active_predictions(window_days=360)
    active_ids = {s.id for s in active}
    assert fresh.id in active_ids
    assert old_snap_id not in active_ids


@pytest.mark.asyncio
async def test_set_registry_swaps_default() -> None:
    """set_registry replaces what get_registry returns."""
    reset_registry()
    assert isinstance(get_registry(), InMemoryPredictionRegistry)
    pg = PGPredictionRegistry(session_factory=async_session_factory())
    set_registry(pg)
    assert get_registry() is pg
    assert isinstance(get_registry(), PGPredictionRegistry)
    reset_registry()  # restore default for downstream tests


# ---------------------------------------------------------------------------


async def _create_and_get(
    reg: PredictionRegistryProtocol,
    sf: async_sessionmaker,
) -> PredictionSnapshot:
    snap = _build()
    await _ensure_ipo_event(sf, snap.ipo_id)
    await reg.create_snapshot(snap)
    return snap


# SQLAlchemy's async engine is reused across this module; pytest-asyncio
# handles event-loop scoping, so we deliberately don't dispose the engine
# in module teardown — that previously caused "Event loop is closed"
# RuntimeErrors during pool cleanup.
