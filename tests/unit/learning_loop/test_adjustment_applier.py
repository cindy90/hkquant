"""adjustment_applier.py tests — Phase 10b per ADR 0015.

THE CRITICAL TESTS: the strict human-gate must be enforced; no config
mutation can sneak through.
"""

from __future__ import annotations

import functools
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from hk_ipo_agent.common.enums import AdjustmentStatus, AdjustmentType, Confidence
from hk_ipo_agent.common.exceptions import AdjustmentNotApprovedError
from hk_ipo_agent.common.settings import get_settings
from hk_ipo_agent.learning_loop.adjustment_applier import (
    AdjustmentApplier,
    ApplierConfig,
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


@pytest.fixture(autouse=True)
def _fresh_engine() -> Iterator[None]:
    from hk_ipo_agent.data.database import async_session_factory, get_engine

    get_engine.cache_clear()  # type: ignore[attr-defined]
    async_session_factory.cache_clear()  # type: ignore[attr-defined]
    yield
    get_engine.cache_clear()  # type: ignore[attr-defined]
    async_session_factory.cache_clear()  # type: ignore[attr-defined]


@pytest_asyncio.fixture
async def sf():
    """Clean only the learning-loop tables (keep ipo_events for later
    e2e tests that depend on the ETL-seeded data)."""
    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "TRUNCATE TABLE config_versions, prediction_reviews, prediction_outcomes, "
            "post_ipo_events, prediction_snapshots "
            "RESTART IDENTITY CASCADE"
        )
        conn.commit()
    engine = create_async_engine(get_settings().database.url, poolclass=NullPool)
    sf_ = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    yield sf_
    await engine.dispose()


_UNSET: Any = object()  # R3-7 sentinel — let callers explicitly pass proposed_value=None


def _seed_snapshot_and_review(
    *,
    reviewer: str | None,
    status: AdjustmentStatus,
    proposal_target: str = "config/test.yaml",
    proposed_value: Any = _UNSET,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Sync-seed an ipo_event + snapshot + review with one proposal.

    Returns (snapshot_id, review_id).

    R3-7: ``proposed_value=None`` is now a meaningful test input (the bug
    we're guarding against). Use ``_UNSET`` to mean "default to {x: 1.0}".
    """
    import json

    snap_id = uuid.uuid4()
    review_id = uuid.uuid4()
    ipo_id = uuid.uuid4()
    effective_proposed_value = {"x": 1.0} if proposed_value is _UNSET else proposed_value
    proposal = {
        "target_path": proposal_target,
        "adjustment_type": AdjustmentType.WEIGHT_CHANGE.value,
        "current_value": None,
        "proposed_value": effective_proposed_value,
        "rationale": "test",
        "evidence_snapshot_ids": [],
        "expected_impact": "test",
        "confidence": Confidence.MEDIUM.value,
    }
    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ipo_events (id, stock_code, company_name_zh, listing_type, "
            "created_at, updated_at) VALUES (%s, %s, %s, %s, NOW(), NOW())",
            (ipo_id, "TEST.HK", "Test", "MB-TECH"),
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
        cur.execute(
            "INSERT INTO prediction_reviews "
            "(id, snapshot_id, review_checkpoint_day, reviewer, "
            " primary_attribution, proposed_adjustments, adjustment_status, "
            " notes_md, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, NOW(), NOW())",
            (
                review_id,
                snap_id,
                30,
                reviewer,
                "test",
                json.dumps([proposal]),
                status.value,
                "test note",
            ),
        )
        conn.commit()
    return snap_id, review_id


# ===========================================================================
# Critical human-gate tests
# ===========================================================================


@pg_required
@pytest.mark.asyncio
async def test_applier_rejects_proposal_when_status_is_proposed(sf) -> None:
    """status=proposed → MUST raise; no config write."""
    _, review_id = _seed_snapshot_and_review(
        reviewer="alice",
        status=AdjustmentStatus.PROPOSED,
    )
    applier = AdjustmentApplier(
        session_factory=sf,
        config=ApplierConfig(write_to_disk=False, run_sanity_backtest=False),
    )
    with pytest.raises(AdjustmentNotApprovedError, match="requires"):
        await applier.apply_review(review_id)


@pg_required
@pytest.mark.asyncio
async def test_applier_rejects_proposal_when_reviewer_empty(sf) -> None:
    """reviewer="" → MUST raise."""
    _, review_id = _seed_snapshot_and_review(
        reviewer="",
        status=AdjustmentStatus.ACCEPTED,
    )
    applier = AdjustmentApplier(
        session_factory=sf,
        config=ApplierConfig(write_to_disk=False, run_sanity_backtest=False),
    )
    with pytest.raises(AdjustmentNotApprovedError, match="empty reviewer"):
        await applier.apply_review(review_id)


@pg_required
@pytest.mark.asyncio
async def test_applier_rejects_proposal_when_status_implemented(sf) -> None:
    """Already-implemented can't be re-applied."""
    _, review_id = _seed_snapshot_and_review(
        reviewer="alice",
        status=AdjustmentStatus.IMPLEMENTED,
    )
    applier = AdjustmentApplier(
        session_factory=sf,
        config=ApplierConfig(write_to_disk=False, run_sanity_backtest=False),
    )
    with pytest.raises(AdjustmentNotApprovedError):
        await applier.apply_review(review_id)


# ===========================================================================
# Happy path
# ===========================================================================


@pg_required
@pytest.mark.asyncio
async def test_applier_happy_path_marks_review_implemented(sf) -> None:
    _, review_id = _seed_snapshot_and_review(
        reviewer="alice",
        status=AdjustmentStatus.ACCEPTED,
    )
    applier = AdjustmentApplier(
        session_factory=sf,
        config=ApplierConfig(write_to_disk=False, run_sanity_backtest=False),
    )
    result = await applier.apply_review(review_id)
    assert result.success is True
    assert result.applied_version == "1.0.0"  # first bump
    # Verify status changed
    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT adjustment_status, applied_version FROM prediction_reviews WHERE id = %s",
            (review_id,),
        )
        status, applied_version = cur.fetchone()
    assert status == AdjustmentStatus.IMPLEMENTED.value
    assert applied_version == "1.0.0"


@pg_required
@pytest.mark.asyncio
async def test_applier_subsequent_apply_bumps_version(sf) -> None:
    """Two distinct reviews applied to the same target → versions 1.0.0 then 1.0.1."""
    _, review_a = _seed_snapshot_and_review(
        reviewer="alice",
        status=AdjustmentStatus.ACCEPTED,
    )
    _, review_b = _seed_snapshot_and_review(
        reviewer="alice",
        status=AdjustmentStatus.ACCEPTED,
    )
    applier = AdjustmentApplier(
        session_factory=sf,
        config=ApplierConfig(write_to_disk=False, run_sanity_backtest=False),
    )
    r_a = await applier.apply_review(review_a)
    r_b = await applier.apply_review(review_b)
    assert r_a.applied_version == "1.0.0"
    assert r_b.applied_version == "1.0.1"


# ===========================================================================
# Sanity backtest path
# ===========================================================================


@pg_required
@pytest.mark.asyncio
async def test_applier_rolls_back_on_backtest_regression(sf) -> None:
    """Sanity backtest returns regression → review marked REJECTED + rollback."""
    _, review_id = _seed_snapshot_and_review(
        reviewer="alice",
        status=AdjustmentStatus.ACCEPTED,
    )
    applier = AdjustmentApplier(
        session_factory=sf,
        config=ApplierConfig(
            write_to_disk=False,
            run_sanity_backtest=True,
            rebacktest_ic_tolerance=0.02,
        ),
    )

    async def _bad_backtest() -> tuple[float, float]:
        # Baseline=0.10, new=0.04 → drop=0.06 > tol 0.02
        return 0.10, 0.04

    result = await applier.apply_review(
        review_id,
        run_walk_forward_fn=_bad_backtest,
    )
    assert result.success is False
    assert "regression" in result.reason
    # Status should be REJECTED
    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT adjustment_status FROM prediction_reviews WHERE id = %s",
            (review_id,),
        )
        status = cur.fetchone()[0]
    assert status == AdjustmentStatus.REJECTED.value


@pg_required
@pytest.mark.asyncio
async def test_applier_passes_when_backtest_within_tolerance(sf) -> None:
    """Sanity backtest within tolerance → success."""
    _, review_id = _seed_snapshot_and_review(
        reviewer="alice",
        status=AdjustmentStatus.ACCEPTED,
    )
    applier = AdjustmentApplier(
        session_factory=sf,
        config=ApplierConfig(
            write_to_disk=False,
            run_sanity_backtest=True,
            rebacktest_ic_tolerance=0.05,
        ),
    )

    async def _good_backtest() -> tuple[float, float]:
        return 0.10, 0.09  # drop=0.01 < tol 0.05

    result = await applier.apply_review(
        review_id,
        run_walk_forward_fn=_good_backtest,
    )
    assert result.success is True


# ===========================================================================
# Edge cases
# ===========================================================================


@pg_required
@pytest.mark.asyncio
async def test_applier_unknown_review_raises_key_error(sf) -> None:
    applier = AdjustmentApplier(session_factory=sf)
    with pytest.raises(KeyError, match="not found"):
        await applier.apply_review(uuid.uuid4())


@pg_required
@pytest.mark.asyncio
async def test_applier_proposal_index_out_of_range_returns_failure(sf) -> None:
    _, review_id = _seed_snapshot_and_review(
        reviewer="alice",
        status=AdjustmentStatus.ACCEPTED,
    )
    applier = AdjustmentApplier(
        session_factory=sf,
        config=ApplierConfig(write_to_disk=False, run_sanity_backtest=False),
    )
    result = await applier.apply_review(review_id, proposal_index=5)
    assert result.success is False
    assert "out of range" in result.reason


# ===========================================================================
# R3-5 — _rollback writes sentinel when no prior version exists
# ===========================================================================


@pg_required
@pytest.mark.asyncio
async def test_applier_rollback_writes_sentinel_when_no_prior_version(sf) -> None:
    """R3-5 — when the bump that just failed was the first version for
    a target_path, _rollback writes a ``rollback_initial_seed`` sentinel
    config_versions row so the audit trail captures both events:
    the failed apply AND its rollback.

    Pre-fix wrapped the rollback in ``contextlib.suppress(KeyError)``
    and the _rollback method itself returned silently when
    ``len(versions) < 2``, leaving the broken bad-content version row
    as the active record — a future ``get_active_version`` would
    happily return the failed content as canonical.
    """
    _, review_id = _seed_snapshot_and_review(
        reviewer="alice",
        status=AdjustmentStatus.ACCEPTED,
        proposal_target="config/r3_5_first_apply.yaml",
        proposed_value={"weight": 1.0},
    )

    # Make the disk write fail → applier hits the except → _rollback.
    # With write_to_disk=True but target path traversal poisoned, we'll
    # raise from within _write_target_file. Simplest: use a sanity
    # backtest stub that raises so we hit the same except branch.
    async def _raise_backtest():
        raise RuntimeError("simulated backtest crash")

    applier = AdjustmentApplier(
        session_factory=sf,
        config=ApplierConfig(write_to_disk=False, run_sanity_backtest=True),
    )

    # Sanity backtest returning (False, ic, ic) triggers the NORMAL
    # rollback path (already covered). To hit the EXCEPT branch we
    # have to make run_walk_forward_fn itself raise inside the try.
    # The applier wraps it; passing a fn that raises mid-await:
    async def _raises():
        raise RuntimeError("simulated")

    result = await applier.apply_review(review_id, run_walk_forward_fn=_raises)
    # Sanity backtest "raised" is captured inside _sanity_backtest and
    # returns (False, None, None), which is the rejected-NOT-exception
    # path. To test the OUTER except we need a different vector — patch
    # _write_target_file. For simplicity here we verify behaviour by
    # querying config_versions: with write_to_disk=False the bump_version
    # itself succeeds; sanity backtest fails → _rollback runs → since
    # there's no prior version we get the sentinel.
    versions = await applier._vm.list_versions("config/r3_5_first_apply.yaml")
    # Expected: original 1.0.0 + sentinel rollback row at 1.0.1.
    change_types = [v.change_type for v in versions]
    assert "rollback_initial_seed" in change_types, (
        f"R3-5 sentinel row missing. version history: {change_types}. "
        f"Expected ``rollback_initial_seed`` after first-apply failure."
    )
    # The applier returned success=False because backtest failed.
    assert result.success is False


# ===========================================================================
# R3-7 — proposed_value=None must be rejected, not silently wrapped
# ===========================================================================


@pg_required
@pytest.mark.asyncio
async def test_applier_rejects_none_proposed_value(sf) -> None:
    """R3-7 — applier MUST refuse to apply when both proposed_content
    and proposal.proposed_value are None.

    Pre-fix the applier silently wrapped None into ``{"value": None}``
    and wrote that to disk, corrupting the target config file. R3-7
    short-circuits before the write so reviewers are forced to supply
    concrete content via ``--proposed-content path/to/json``.
    """
    _, review_id = _seed_snapshot_and_review(
        reviewer="alice",
        status=AdjustmentStatus.ACCEPTED,
        proposal_target="config/r3_7_none_value.yaml",
        proposed_value=None,  # ← THE bug source
    )
    applier = AdjustmentApplier(
        session_factory=sf,
        config=ApplierConfig(write_to_disk=False, run_sanity_backtest=False),
    )
    result = await applier.apply_review(review_id)
    assert result.success is False
    assert "proposed_value is None" in result.reason
    assert result.applied_version is None  # No version bump happened.


@pg_required
@pytest.mark.asyncio
async def test_applier_accepts_explicit_proposed_content_when_value_is_none(sf) -> None:
    """R3-7 — caller can override the None proposed_value by passing
    proposed_content directly (this is the CLI ``--proposed-content``
    code path). Apply succeeds despite proposal.proposed_value=None."""
    _, review_id = _seed_snapshot_and_review(
        reviewer="alice",
        status=AdjustmentStatus.ACCEPTED,
        proposal_target="config/r3_7_override.yaml",
        proposed_value=None,
    )
    applier = AdjustmentApplier(
        session_factory=sf,
        config=ApplierConfig(write_to_disk=False, run_sanity_backtest=False),
    )
    result = await applier.apply_review(
        review_id,
        proposed_content={"weight": 0.42},  # ← reviewer-supplied
    )
    assert result.success is True
    assert result.applied_version is not None
