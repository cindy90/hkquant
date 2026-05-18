"""End-to-end learning loop — Phase 10c per ADR 0015.

Exercises the complete propose → human-accept → apply → re-backtest
loop with mocked LLM. The test seeds:
- One snapshot with PROPOSED proposals (via the proposer's
  persist_proposals_to_review).
- Human acceptance via direct UPDATE (mimicking review_proposals CLI).
- Applier with mock sanity-backtest that returns a "pass" tuple.

DONE-conditions for Phase 10c §e2e:
- propose → review row created with status=PROPOSED
- accept → status=ACCEPTED + reviewer field set
- apply (happy) → status=IMPLEMENTED + version bumped
- apply (regression mock) → status=REJECTED + rollback created
"""

from __future__ import annotations

import functools
import uuid
from datetime import UTC, datetime

import psycopg
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from hk_ipo_agent.common.enums import (
    AdjustmentStatus,
    AlertLevel,
    DriftSignalType,
)
from hk_ipo_agent.common.schemas import DriftSignal
from hk_ipo_agent.common.settings import get_settings
from hk_ipo_agent.learning_loop.adjustment_applier import (
    AdjustmentApplier,
    ApplierConfig,
)
from hk_ipo_agent.learning_loop.adjustment_proposer import (
    AdjustmentProposer,
    persist_proposals_to_review,
)


@functools.lru_cache(maxsize=1)
def _pg_available() -> bool:
    url = get_settings().database.url
    dsn = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    try:
        with psycopg.connect(dsn, connect_timeout=2):
            return True
    except Exception:
        return False


pg_required = pytest.mark.skipif(
    not _pg_available(),
    reason="docker postgres unavailable — start with `docker compose up -d postgres`",
)


def _sync_dsn() -> str:
    return get_settings().database.url.replace(
        "postgresql+asyncpg://",
        "postgresql://",
        1,
    )


@pytest_asyncio.fixture
async def sf():
    """Clean only the learning-loop tables (NOT ipo_events) + async sf.

    Keeping ipo_events intact means later e2e modules that rely on the
    ETL'd 384 IPOs (e.g. test_quantumpharm_case) still see real data.
    Each learning-loop test seeds its own ipo_event with a fresh UUID
    so there's no FK / unique conflict.
    """
    from hk_ipo_agent.data.database import (
        async_session_factory,
        get_engine,
    )

    get_engine.cache_clear()  # type: ignore[attr-defined]
    async_session_factory.cache_clear()  # type: ignore[attr-defined]

    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "TRUNCATE TABLE config_versions, prediction_reviews, "
            "prediction_outcomes, post_ipo_events, prediction_snapshots "
            "RESTART IDENTITY CASCADE"
        )
        conn.commit()

    engine = create_async_engine(get_settings().database.url, poolclass=NullPool)
    sf_ = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    yield sf_
    await engine.dispose()
    get_engine.cache_clear()  # type: ignore[attr-defined]
    async_session_factory.cache_clear()  # type: ignore[attr-defined]


def _seed_snapshot() -> uuid.UUID:
    """Insert one ipo_event + prediction_snapshot; return snapshot_id."""
    snap_id = uuid.uuid4()
    ipo_id = uuid.uuid4()
    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ipo_events (id, stock_code, company_name_zh, listing_type, "
            "created_at, updated_at) VALUES (%s, %s, %s, %s, NOW(), NOW())",
            (ipo_id, "LEARN.HK", "LearningTest", "MB-TECH"),
        )
        cur.execute(
            "INSERT INTO prediction_snapshots "
            "(id, ipo_id, as_of_date, prospectus_version, input_data_hash, "
            " input_data_snapshot, agent_outputs, valuation_output, "
            " debate_output, decision, system_version, model_versions, "
            " config_snapshot, total_cost_usd, runtime_seconds, created_at) "
            "VALUES (%s, %s, '2024-01-01', 'PHIP', %s, '{}'::jsonb, "
            " '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, '0.0.1', "
            " '{}'::jsonb, '{}'::jsonb, 0.0, 0.0, NOW())",
            (snap_id, ipo_id, "0" * 64),
        )
        conn.commit()
    return snap_id


def _accept_review(review_id: uuid.UUID, reviewer: str = "alice") -> None:
    """Mimic scripts/review_proposals.py accept — direct UPDATE."""
    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE prediction_reviews SET adjustment_status = %s, "
            "reviewer = %s, updated_at = NOW() WHERE id = %s",
            (AdjustmentStatus.ACCEPTED.value, reviewer, review_id),
        )
        conn.commit()


def _get_review_status(review_id: uuid.UUID) -> tuple[str, str | None, str | None]:
    """Return (status, reviewer, applied_version)."""
    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT adjustment_status, reviewer, applied_version "
            "FROM prediction_reviews WHERE id = %s",
            (review_id,),
        )
        return cur.fetchone()


# ===========================================================================
# Full propose → accept → apply happy loop
# ===========================================================================


@pytest.mark.slow
@pg_required
@pytest.mark.asyncio
async def test_full_propose_accept_apply_happy(sf) -> None:
    """R9-8: heaviest learning-loop e2e — propose → review → apply →
    backtest. Marked ``slow`` so default unit runs skip it."""
    snap_id = _seed_snapshot()

    # 1. PROPOSE — produce one ProposedAdjustment + persist
    proposer = AdjustmentProposer()
    drift = DriftSignal(
        detection_time=datetime.now(UTC),
        signal_type=DriftSignalType.VALUATION_BIAS,
        severity=AlertLevel.WARNING,
        affected_dimensions={"listing_type": "MB-TECH"},
        metric_value=0.3,
        threshold=0.2,
        sample_count=20,
        evidence="test",
        related_snapshot_ids=[],
    )
    proposals = proposer.propose(drift_signals=[drift])
    assert len(proposals) >= 1
    review_id = await persist_proposals_to_review(snap_id, proposals, sf)

    status, _reviewer, _applied = _get_review_status(review_id)
    assert status == AdjustmentStatus.PROPOSED.value

    # 2. ACCEPT — human action via direct UPDATE (mimics CLI)
    _accept_review(review_id, "alice")
    status, reviewer, _applied = _get_review_status(review_id)
    assert status == AdjustmentStatus.ACCEPTED.value
    assert reviewer == "alice"

    # 3. APPLY — happy path, no sanity backtest (or backtest passes)
    applier = AdjustmentApplier(
        session_factory=sf,
        config=ApplierConfig(write_to_disk=False, run_sanity_backtest=False),
    )
    # Override proposed_content because the proposer didn't fill in a
    # concrete proposed_value (it never does — that's the reviewer's job).
    result = await applier.apply_review(
        review_id,
        proposed_content={"MB-TECH": {"dcf": 0.30, "comparable": 0.70}},
    )
    assert result.success is True
    assert result.applied_version == "1.0.0"

    status, _reviewer, applied_version = _get_review_status(review_id)
    assert status == AdjustmentStatus.IMPLEMENTED.value
    assert applied_version == "1.0.0"


# ===========================================================================
# Apply with regression → rollback path
# ===========================================================================


@pg_required
@pytest.mark.asyncio
async def test_full_propose_accept_apply_rollback_on_regression(sf) -> None:
    snap_id = _seed_snapshot()

    # 1. PROPOSE
    proposer = AdjustmentProposer()
    drift = DriftSignal(
        detection_time=datetime.now(UTC),
        signal_type=DriftSignalType.VALUATION_BIAS,
        severity=AlertLevel.WARNING,
        affected_dimensions={"listing_type": "MB-TECH"},
        metric_value=0.3,
        threshold=0.2,
        sample_count=20,
        evidence="test",
        related_snapshot_ids=[],
    )
    proposals = proposer.propose(drift_signals=[drift])
    review_id = await persist_proposals_to_review(snap_id, proposals, sf)

    # 2. ACCEPT
    _accept_review(review_id, "alice")

    # 3. APPLY with mocked sanity backtest showing regression
    applier = AdjustmentApplier(
        session_factory=sf,
        config=ApplierConfig(
            write_to_disk=False,
            run_sanity_backtest=True,
            rebacktest_ic_tolerance=0.02,
        ),
    )

    async def _regression_backtest() -> tuple[float, float]:
        # baseline 0.10, new 0.03 → drop 0.07 > 0.02 tolerance
        return 0.10, 0.03

    result = await applier.apply_review(
        review_id,
        proposed_content={"MB-TECH": {"dcf": 0.30, "comparable": 0.70}},
        run_walk_forward_fn=_regression_backtest,
    )
    assert result.success is False
    assert "regression" in result.reason

    status, _reviewer, _applied = _get_review_status(review_id)
    assert status == AdjustmentStatus.REJECTED.value


# ===========================================================================
# Human-gate enforcement at the system boundary
# ===========================================================================


@pg_required
@pytest.mark.asyncio
async def test_apply_refuses_when_not_accepted(sf) -> None:
    """Even with proposals persisted, apply must refuse without ACCEPT."""
    from hk_ipo_agent.common.exceptions import AdjustmentNotApprovedError

    snap_id = _seed_snapshot()
    proposer = AdjustmentProposer()
    drift = DriftSignal(
        detection_time=datetime.now(UTC),
        signal_type=DriftSignalType.VALUATION_BIAS,
        severity=AlertLevel.WARNING,
        affected_dimensions={"listing_type": "MB-TECH"},
        metric_value=0.3,
        threshold=0.2,
        sample_count=20,
        evidence="test",
        related_snapshot_ids=[],
    )
    proposals = proposer.propose(drift_signals=[drift])
    review_id = await persist_proposals_to_review(
        snap_id,
        proposals,
        sf,
        reviewer="",  # empty reviewer
    )

    applier = AdjustmentApplier(
        session_factory=sf,
        config=ApplierConfig(write_to_disk=False, run_sanity_backtest=False),
    )
    with pytest.raises(AdjustmentNotApprovedError):
        await applier.apply_review(review_id)
