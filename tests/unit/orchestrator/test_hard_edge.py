"""Orchestrator hard edge tests — Phase 7.5a per ADR 0012.

The CLAUDE.md "prediction lifecycle" constraints require that a
snapshot be persisted BEFORE the report is emitted. Phase 7.5a wraps
``create_snapshot_node`` in a try/except so a persistence failure
raises ``SnapshotCreationFailed`` and the LangGraph invocation fails
loudly rather than silently advancing to ``report``.

These tests do NOT exercise the full graph (that's integration-test
territory); they exercise the node function in isolation with a stubbed
registry, which is enough to verify the hard-edge contract.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from hk_ipo_agent.common.enums import AgentRole, DecisionType, ListingType
from hk_ipo_agent.common.exceptions import SnapshotCreationFailed
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
from hk_ipo_agent.orchestrator.nodes import make_nodes
from hk_ipo_agent.prediction_registry.registry import (
    InMemoryPredictionRegistry,
    reset_registry,
    set_registry,
)
from hk_ipo_agent.valuation.base import MarketData


def _state() -> dict[str, Any]:
    """Build the minimal AnalysisState the create_snapshot node needs."""
    dist = ValuationDistribution(
        p10=Decimal("90"),
        p25=Decimal("95"),
        p50=Decimal("100"),
        p75=Decimal("105"),
        p90=Decimal("110"),
        mean=Decimal("100"),
        std=Decimal("5"),
    )
    return {
        "ipo_id": "PG-TEST-1",
        "extraction": ProspectusExtraction(
            prospectus_id="P-HE-1",
            company_name_zh="测试 HE",
            listing_type=ListingType.MAINBOARD_TECH,
            industry_code="TECH",
            industry_description="AI",
            business_model="B2B",
            extraction_version="0.0.1",
            extracted_at=datetime.now(UTC),
        ),
        "agent_outputs": {
            "fundamental": AgentOutput(
                agent_role=AgentRole.FUNDAMENTAL,
                scores={"x": 70.0},
                overall_score=70.0,
                runtime_seconds=0.1,
            )
        },
        "valuation_output": ValuationEnsembleOutput(
            company_id="P-HE-1",
            single_models=[
                SingleModelValuation(model_name="x", applicable=True, valuation_distribution=dist)
            ],
            weights_used={"x": 1.0},
            ensemble_distribution=dist,
            implied_price_range={
                "low": Decimal("95"),
                "fair": Decimal("100"),
                "high": Decimal("105"),
            },
        ),
        "debate_output": DebateOutput(final_consensus="balanced"),
        "decision": FinalDecision(
            decision=DecisionType.PARTIAL,
            confidence=0.7,
            suggested_allocation_pct=0.02,
            price_range_low=Decimal("95"),
            price_range_fair=Decimal("100"),
            price_range_high=Decimal("105"),
            expected_return_6m=dist,
            expected_return_12m=dist,
        ),
        "runtime_meta": {"runtime_seconds": 5.5},
    }


def _md() -> MarketData:
    from datetime import date

    return MarketData(as_of_date=date(2026, 5, 16), listing_type=ListingType.MAINBOARD_TECH)


@pytest.fixture(autouse=True)
def _reset_registry_after_test() -> None:
    yield
    reset_registry()


@pytest.mark.asyncio
async def test_create_snapshot_node_succeeds_with_inmemory(monkeypatch) -> None:
    """Happy path: snapshot is persisted and snapshot_id returned."""
    monkeypatch.setenv("KIMI_API_KEY", "sk-test")
    set_registry(InMemoryPredictionRegistry())
    nodes = make_nodes(llm_client=LLMClient(daily_budget_usd=Decimal("100")), market_data=_md())
    result = await nodes["create_snapshot"](_state())
    assert "snapshot_id" in result
    assert result["snapshot_id"] is not None


@pytest.mark.asyncio
async def test_create_snapshot_node_raises_on_registry_failure(monkeypatch) -> None:
    """ADR 0012 hard edge: registry failure must propagate as SnapshotCreationFailed."""
    monkeypatch.setenv("KIMI_API_KEY", "sk-test")

    class _FailingRegistry:
        async def create_snapshot(self, snapshot):  # type: ignore[no-untyped-def]
            raise RuntimeError("simulated DB outage")

        async def get_snapshot(self, snapshot_id):  # type: ignore[no-untyped-def]
            raise KeyError(snapshot_id)

        async def list_snapshots(self, limit: int = 100):
            return []

        async def list_active_predictions(self, as_of_date=None, window_days=360):
            return []

        async def attach_review(self, snapshot_id, review):  # type: ignore[no-untyped-def]
            return uuid4()

    set_registry(_FailingRegistry())  # type: ignore[arg-type]
    nodes = make_nodes(llm_client=LLMClient(daily_budget_usd=Decimal("100")), market_data=_md())
    with pytest.raises(SnapshotCreationFailed) as exc_info:
        await nodes["create_snapshot"](_state())
    # Wrapped chain preserves the original cause.
    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert "simulated DB outage" in str(exc_info.value)


@pytest.mark.asyncio
async def test_create_snapshot_node_hash_mismatch_blocks_persistence(monkeypatch) -> None:
    """Tampered hash on the snapshot object is caught before DB write."""
    monkeypatch.setenv("KIMI_API_KEY", "sk-test")
    set_registry(InMemoryPredictionRegistry())

    # Patch build_snapshot to return a tampered snapshot.
    from hk_ipo_agent.orchestrator import nodes as nodes_mod

    original_build = nodes_mod.build_snapshot

    def _tampered_build(*args, **kwargs):  # type: ignore[no-untyped-def]
        snap = original_build(*args, **kwargs)
        return snap.model_copy(update={"input_data_hash": "0" * 64})

    monkeypatch.setattr(nodes_mod, "build_snapshot", _tampered_build)
    nodes = make_nodes(llm_client=LLMClient(daily_budget_usd=Decimal("100")), market_data=_md())
    with pytest.raises(SnapshotCreationFailed):
        await nodes["create_snapshot"](_state())
