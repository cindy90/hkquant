"""Tests for prediction_registry/registry.py — in-memory append-only store."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from hk_ipo_agent.common.enums import AgentRole, DecisionType, ListingType
from hk_ipo_agent.common.schemas import (
    AgentOutput,
    DebateOutput,
    FinalDecision,
    ProspectusExtraction,
    SingleModelValuation,
    ValuationDistribution,
    ValuationEnsembleOutput,
)
from hk_ipo_agent.prediction_registry.registry import (
    PredictionRegistry,
    get_registry,
    reset_registry,
)
from hk_ipo_agent.prediction_registry.snapshot import (
    SnapshotIntegrityError,
    build_snapshot,
)


def _build_snapshot():
    ext = ProspectusExtraction(
        prospectus_id="P-REG-1",
        company_name_zh="测试",
        listing_type=ListingType.MAINBOARD_TECH,
        industry_code="TECH",
        industry_description="AI",
        business_model="B2B",
        extraction_version="0.0.1",
        extracted_at=datetime.now(UTC),
    )
    d = ValuationDistribution(
        p10=Decimal("90"),
        p25=Decimal("95"),
        p50=Decimal("100"),
        p75=Decimal("105"),
        p90=Decimal("110"),
        mean=Decimal("100"),
        std=Decimal("5"),
    )
    val = ValuationEnsembleOutput(
        company_id="P-REG-1",
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
    debate = DebateOutput(final_consensus="balanced")
    decision = FinalDecision(
        decision=DecisionType.PARTIAL,
        confidence=0.7,
        suggested_allocation_pct=0.02,
        price_range_low=Decimal("95"),
        price_range_fair=Decimal("100"),
        price_range_high=Decimal("105"),
        expected_return_6m=d,
        expected_return_12m=d,
        scorecard={},
    )
    return build_snapshot(
        ipo_id=uuid4(),
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
        debate=debate,
        decision=decision,
        total_cost_usd=Decimal("0.05"),
        runtime_seconds=10.0,
    )


@pytest.mark.asyncio
async def test_create_and_get_snapshot() -> None:
    reg = PredictionRegistry()
    snap = _build_snapshot()
    snap_id = await reg.create_snapshot(snap)
    fetched = await reg.get_snapshot(snap_id)
    assert fetched.id == snap.id
    assert len(reg) == 1


@pytest.mark.asyncio
async def test_create_duplicate_raises() -> None:
    reg = PredictionRegistry()
    snap = _build_snapshot()
    await reg.create_snapshot(snap)
    with pytest.raises(SnapshotIntegrityError):
        await reg.create_snapshot(snap)


@pytest.mark.asyncio
async def test_get_unknown_raises_key_error() -> None:
    reg = PredictionRegistry()
    with pytest.raises(KeyError):
        await reg.get_snapshot(uuid4())


@pytest.mark.asyncio
async def test_list_snapshots() -> None:
    reg = PredictionRegistry()
    await reg.create_snapshot(_build_snapshot())
    await reg.create_snapshot(_build_snapshot())
    snaps = await reg.list_snapshots()
    assert len(snaps) == 2


def test_get_registry_singleton() -> None:
    reset_registry()
    r1 = get_registry()
    r2 = get_registry()
    assert r1 is r2
    reset_registry()
    r3 = get_registry()
    assert r3 is not r1


@pytest.mark.asyncio
async def test_create_snapshot_verifies_integrity() -> None:
    """Tampered hash → SnapshotIntegrityError on create."""
    reg = PredictionRegistry()
    snap = _build_snapshot()
    tampered = snap.model_copy(update={"input_data_hash": "0" * 64})
    with pytest.raises(SnapshotIntegrityError):
        await reg.create_snapshot(tampered)


# ---------------------------------------------------------------------------
# R2-3 — explicit application-layer rejection of update/delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_in_memory_registry_rejects_update() -> None:
    """R2-3 — InMemoryPredictionRegistry must explicitly raise on update_snapshot.

    Pre-fix the application layer relied solely on the absence of the method
    (AttributeError if accessed). Adding an explicit ``NotImplementedError``
    method to the Protocol forces backend symmetry and gives downstream code
    a clear, named failure mode that explains *why* the operation is forbidden.

    CLAUDE.md §预测生命周期约束: "snapshot 绝对不可变" — application code
    must never expose a mutating API for snapshots.
    """
    reg = PredictionRegistry()
    snap = _build_snapshot()
    await reg.create_snapshot(snap)

    with pytest.raises(NotImplementedError, match="immutable by design"):
        await reg.update_snapshot(snap.id, snap)


@pytest.mark.asyncio
async def test_in_memory_registry_rejects_delete() -> None:
    """R2-3 — InMemoryPredictionRegistry must explicitly raise on delete_snapshot."""
    reg = PredictionRegistry()
    snap = _build_snapshot()
    await reg.create_snapshot(snap)

    with pytest.raises(NotImplementedError, match="immutable by design"):
        await reg.delete_snapshot(snap.id)
