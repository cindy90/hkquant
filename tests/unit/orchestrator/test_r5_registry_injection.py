"""R5-4 — make_nodes / build_main_graph accept an explicit registry.

Pre-R5-4 ``pipelines.pdf_to_snapshot.run_pdf_to_snapshot`` mutated the
process-wide ``_default_registry`` via ``set_registry(PGPredictionRegistry())``
or ``set_registry(InMemoryPredictionRegistry())`` just before invoking the
graph. Two concurrent pipelines (e.g. an API caller + a background backtest)
would clobber each other's registry — the second ``set_registry`` overwrote
the first, so whichever finished last wrote its snapshot into the wrong
backing store.

Post-R5-4 the registry is passed explicitly through ``build_main_graph``
into ``make_nodes`` and captured in the ``create_snapshot_node`` closure.
The global ``set_registry`` / ``get_registry`` accessor still exists for
backwards compatibility (API routers + standalone tests use it), but the
pipeline no longer touches it.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from hk_ipo_agent.common.enums import AgentRole, DecisionType, ListingType
from hk_ipo_agent.common.llm_client import LLMClient
from hk_ipo_agent.common.schemas import (
    AgentOutput,
    DebateOutput,
    FinalDecision,
    PredictionReview,
    PredictionSnapshot,
    ProspectusExtraction,
    SingleModelValuation,
    ValuationDistribution,
    ValuationEnsembleOutput,
)
from hk_ipo_agent.orchestrator.nodes import make_nodes
from hk_ipo_agent.prediction_registry.registry import (
    reset_registry,
    set_registry,
)
from hk_ipo_agent.valuation.base import MarketData


def _state(ipo_id: str = "PG-INJECT-1") -> dict[str, Any]:
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
        "ipo_id": ipo_id,
        "extraction": ProspectusExtraction(
            prospectus_id=f"P-{ipo_id}",
            company_name_zh="注入测试",
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
            company_id=f"P-{ipo_id}",
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
    return MarketData(as_of_date=date(2026, 5, 16), listing_type=ListingType.MAINBOARD_TECH)


class _RecordingRegistry:
    """Records every create_snapshot call. Structural ``PredictionRegistryProtocol``."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.created: list[UUID] = []

    async def create_snapshot(self, snapshot: PredictionSnapshot) -> UUID:
        self.created.append(snapshot.id)
        return snapshot.id

    async def get_snapshot(self, snapshot_id: UUID) -> PredictionSnapshot:
        raise NotImplementedError

    async def list_snapshots(self, limit: int = 100) -> list[PredictionSnapshot]:
        return []

    async def list_active_predictions(
        self,
        as_of_date: datetime | None = None,
        window_days: int = 360,
    ) -> list[PredictionSnapshot]:
        return []

    async def attach_review(self, snapshot_id: UUID, review: PredictionReview) -> UUID:
        return uuid4()

    async def update_snapshot(self, snapshot_id: UUID, snapshot: PredictionSnapshot) -> None:
        raise NotImplementedError

    async def delete_snapshot(self, snapshot_id: UUID) -> None:
        raise NotImplementedError


@pytest.fixture(autouse=True)
def _reset_global() -> Any:
    reset_registry()
    yield
    reset_registry()


# ---------------------------------------------------------------------- tests


@pytest.mark.asyncio
async def test_make_nodes_uses_injected_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """R5-4 — when ``registry=`` is passed, the node calls IT, not the global."""
    monkeypatch.setenv("KIMI_API_KEY", "sk-test")
    injected = _RecordingRegistry("INJECTED")
    nodes = make_nodes(
        llm_client=LLMClient(daily_budget_usd=Decimal("100")),
        market_data=_md(),
        registry=injected,
    )
    out = await nodes["create_snapshot"](_state("9999.HK"))

    assert "snapshot_id" in out
    assert isinstance(out["snapshot_id"], UUID)
    assert len(injected.created) == 1
    assert injected.created[0] == out["snapshot_id"]


@pytest.mark.asyncio
async def test_make_nodes_falls_back_to_get_registry_when_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R5-4 — back-compat: API routers + standalone tests still work via set_registry()."""
    monkeypatch.setenv("KIMI_API_KEY", "sk-test")
    global_reg = _RecordingRegistry("GLOBAL")
    set_registry(global_reg)  # type: ignore[arg-type]

    nodes = make_nodes(
        llm_client=LLMClient(daily_budget_usd=Decimal("100")),
        market_data=_md(),
        # NO registry kwarg → must fall back to get_registry()
    )
    out = await nodes["create_snapshot"](_state("8888.HK"))

    assert isinstance(out["snapshot_id"], UUID)
    assert len(global_reg.created) == 1


@pytest.mark.asyncio
async def test_concurrent_runs_no_registry_clobber(monkeypatch: pytest.MonkeyPatch) -> None:
    """R5-4 — two graphs with two registries don't trample each other.

    The load-bearing contract pre-R5-4 violated: pipeline A would
    ``set_registry(R_A)``; pipeline B then ``set_registry(R_B)``; A's
    ``create_snapshot`` — running after B's set — wrote to R_B. With
    closure injection, each node ALWAYS hits its own registry.
    """
    monkeypatch.setenv("KIMI_API_KEY", "sk-test")
    reg_a = _RecordingRegistry("A")
    reg_b = _RecordingRegistry("B")

    nodes_a = make_nodes(
        llm_client=LLMClient(daily_budget_usd=Decimal("100")),
        market_data=_md(),
        registry=reg_a,
    )
    nodes_b = make_nodes(
        llm_client=LLMClient(daily_budget_usd=Decimal("100")),
        market_data=_md(),
        registry=reg_b,
    )

    # Interleave: both run concurrently.
    out_a, out_b = await asyncio.gather(
        nodes_a["create_snapshot"](_state("0001.HK")),
        nodes_b["create_snapshot"](_state("0002.HK")),
    )

    assert len(reg_a.created) == 1
    assert len(reg_b.created) == 1
    assert reg_a.created[0] == out_a["snapshot_id"]
    assert reg_b.created[0] == out_b["snapshot_id"]
    # The two snapshot IDs are distinct because ipo_id differs.
    assert reg_a.created[0] != reg_b.created[0]


def test_build_main_graph_accepts_registry_kwarg(monkeypatch: pytest.MonkeyPatch) -> None:
    """R5-4 — public API surface: ``build_main_graph(registry=...)`` is supported."""
    monkeypatch.setenv("KIMI_API_KEY", "sk-test")
    from hk_ipo_agent.orchestrator.graph import build_main_graph

    reg = _RecordingRegistry("BUILD-ARG")
    graph = build_main_graph(
        llm_client=LLMClient(daily_budget_usd=Decimal("100")),
        market_data=_md(),
        registry=reg,
        use_checkpointer=False,
    )
    assert graph is not None


def test_pipelines_module_no_longer_calls_set_registry() -> None:
    """R5-4 — pipelines.pdf_to_snapshot no longer **calls** ``set_registry``.

    Catches regressions: anyone re-adding the global side effect trips this.
    We walk the AST so prose mentions in comments / docstrings don't false-fire.
    """
    import ast
    import inspect

    import hk_ipo_agent.pipelines.pdf_to_snapshot as pdf_mod

    tree = ast.parse(inspect.getsource(pdf_mod))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else (func.attr if isinstance(func, ast.Attribute) else None)
            )
            assert name != "set_registry", (
                "pipelines.pdf_to_snapshot still calls set_registry(...) — "
                "R5-4 requires explicit injection via build_main_graph(registry=...)."
            )
    # And the symbol must not be imported either.
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert all(alias.name != "set_registry" for alias in node.names), (
                "pipelines.pdf_to_snapshot still imports set_registry"
            )
