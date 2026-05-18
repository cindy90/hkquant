"""LangGraph node functions per PROJECT_SPEC.md §3.8 / §8.1.

Each node consumes ``AnalysisState`` (partial), runs work, returns a
*partial* state dict that LangGraph merges via reducers (ADR 0010 §5).

The 7 expert agent nodes intentionally write to ``agent_outputs`` only
(reducer ``operator.or_`` merges them). They also write incremental
NACS signals to ``extras`` (reducer ``_merge_extras`` keeps non-None
values).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from ..agents import (
    AgentContext,
    CornerstoneSignalAgent,
    FundamentalAgent,
    IndustryAgent,
    LiquidityAgent,
    PolicyAgent,
    SentimentAgent,
    ValuationAgent,
)
from ..agents.workflow_extras import WorkflowExtras
from ..common.exceptions import SnapshotCreationFailed
from ..common.llm_client import LLMClient
from ..common.logging import get_logger
from ..common.schemas import (
    AgentOutput,
    DebateOutput,
    ProspectusExtraction,
    ValuationEnsembleOutput,
)
from ..critic import cross_check, run_debate
from ..prediction_registry.registry import (
    PredictionRegistryProtocol,
    get_registry,
)
from ..prediction_registry.snapshot import build_snapshot
from ..synthesizer import synthesize
from ..valuation.base import MarketData
from .states import AnalysisState

# ---------------------------------------------------------------------------
# Node factory — captures LLM client + market data without polluting state.
# ---------------------------------------------------------------------------


def make_nodes(
    *,
    llm_client: LLMClient,
    market_data: MarketData,
    prospectus_tool: Any = None,
    ifind_tool: Any = None,
    kb_tool: Any = None,
    registry: PredictionRegistryProtocol | None = None,
) -> dict[str, Any]:
    """Build the 14 node callables bound to a given LLM + tool set.

    Returns a dict ``{node_name: async fn(state) -> partial_state}``.

    Args:
        registry: R5-4 — explicit registry to write snapshots into. When
            None, ``create_snapshot_node`` falls back to ``get_registry()``
            for backwards compatibility (API routers + standalone tests
            still rely on the global). The pipeline layer always injects
            explicitly so concurrent runs can't clobber each other's
            registry via the global ``set_registry()``.
    """

    def _ctx_for(state: AnalysisState) -> AgentContext:
        extras = state.get("extras") or WorkflowExtras()
        return AgentContext(
            ipo_id=state["ipo_id"],
            extraction=state["extraction"],
            market_data=market_data,
            llm_client=llm_client,
            extras=extras,
            prospectus_tool=prospectus_tool,
            ifind_tool=ifind_tool,
            kb_tool=kb_tool,
        )

    # ------------------------------------------------------------------ agents
    async def fundamental_node(state: AnalysisState) -> dict[str, Any]:
        out = await FundamentalAgent().run(_ctx_for(state))
        return {"agent_outputs": {out.agent_role.value: out}}

    async def industry_node(state: AnalysisState) -> dict[str, Any]:
        out = await IndustryAgent().run(_ctx_for(state))
        return {"agent_outputs": {out.agent_role.value: out}}

    async def policy_node(state: AnalysisState) -> dict[str, Any]:
        ctx = _ctx_for(state)
        out = await PolicyAgent().run(ctx)
        return {
            "agent_outputs": {out.agent_role.value: out},
            "extras": ctx.extras,
        }

    async def liquidity_node(state: AnalysisState) -> dict[str, Any]:
        out = await LiquidityAgent().run(_ctx_for(state))
        return {"agent_outputs": {out.agent_role.value: out}}

    async def cornerstone_node(state: AnalysisState) -> dict[str, Any]:
        ctx = _ctx_for(state)
        out = await CornerstoneSignalAgent().run(ctx)
        return {
            "agent_outputs": {out.agent_role.value: out},
            "extras": ctx.extras,
        }

    async def sentiment_node(state: AnalysisState) -> dict[str, Any]:
        ctx = _ctx_for(state)
        out = await SentimentAgent().run(ctx)
        return {
            "agent_outputs": {out.agent_role.value: out},
            "extras": ctx.extras,
        }

    async def valuation_node(state: AnalysisState) -> dict[str, Any]:
        ctx = _ctx_for(state)
        out = await ValuationAgent().run(ctx)
        valuation_out: ValuationEnsembleOutput = ctx.extras.misc["valuation_output"]
        return {
            "agent_outputs": {out.agent_role.value: out},
            "valuation_output": valuation_out,
            "extras": ctx.extras,
        }

    # ------------------------------------------------------------------ debate / cross-check
    async def debate_node(state: AnalysisState) -> dict[str, Any]:
        agent_outputs: dict[str, AgentOutput] = state.get("agent_outputs") or {}
        valuation: ValuationEnsembleOutput = state["valuation_output"]
        debate_out, _cost = await run_debate(
            llm_client,
            agent_outputs=agent_outputs,
            valuation=valuation,
            ipo_id=state["ipo_id"],
        )
        return {"debate_output": debate_out}

    async def cross_check_node(state: AnalysisState) -> dict[str, Any]:
        result = cross_check(
            listing_type=state["extraction"].listing_type,
            industry_code=state["extraction"].industry_code,
            historical_records=(state.get("runtime_meta") or {}).get("historical_records"),
        )
        return {
            "cross_check_notes": [
                *(state.get("cross_check_notes") or []),
                f"cross_check: n={result.sample_size}, "
                f"median_60d_ret={result.median_60d_return}, "
                f"median_dd={result.median_drawdown}",
                *result.notes,
            ]
        }

    # ------------------------------------------------------------------ synthesize + snapshot
    async def synthesize_node(state: AnalysisState) -> dict[str, Any]:
        extras = state.get("extras") or WorkflowExtras()
        decision, _ = await synthesize(
            llm_client,
            ipo_id=state["ipo_id"],
            agent_outputs=state.get("agent_outputs") or {},
            valuation=state["valuation_output"],
            debate=state.get("debate_output") or DebateOutput(final_consensus="(no debate)"),
            extras=extras,
            cross_check_notes=state.get("cross_check_notes"),
        )
        return {"decision": decision}

    async def create_snapshot_node(state: AnalysisState) -> dict[str, Any]:
        """CLAUDE.md HARD constraint: snapshot MUST be created before report.

        ADR 0012 (Phase 7.5a) hard edge: persistence failure raises
        ``SnapshotCreationFailed`` so the LangGraph invocation propagates
        the error rather than silently advancing to ``report``. The HITL
        / report branches downstream are unreachable without a written
        snapshot.
        """
        extraction: ProspectusExtraction = state["extraction"]
        ipo_uuid = uuid5(NAMESPACE_URL, f"hkipo:{state['ipo_id']}")
        snapshot = build_snapshot(
            ipo_id=ipo_uuid,
            extraction=extraction,
            agent_outputs=state.get("agent_outputs") or {},
            valuation=state["valuation_output"],
            debate=state.get("debate_output") or DebateOutput(final_consensus="(no debate)"),
            decision=state["decision"],
            total_cost_usd=Decimal(str(llm_client.cost_log.total_usd())),
            runtime_seconds=(state.get("runtime_meta") or {}).get("runtime_seconds", 0.0),
        )
        # R5-4: prefer explicit injection; fall back to global for back-compat.
        active_registry = registry if registry is not None else get_registry()
        logger = get_logger(__name__)
        try:
            await active_registry.create_snapshot(snapshot)
        except Exception as exc:
            logger.error(
                "snapshot_creation_failed",
                ipo_id=str(state["ipo_id"]),
                snapshot_id=str(snapshot.id),
                input_data_hash=snapshot.input_data_hash,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise SnapshotCreationFailed(
                f"Failed to persist snapshot {snapshot.id} for ipo_id={state['ipo_id']}: {exc}"
            ) from exc
        return {"snapshot_id": snapshot.id}

    # ------------------------------------------------------------------ report (terminal)
    async def report_node(state: AnalysisState) -> dict[str, Any]:
        """Terminal node. Phase 7 reporting layer will replace the stub."""
        meta = state.get("runtime_meta") or {}
        meta = {**meta, "finished_at": datetime.now(UTC).isoformat()}
        return {"runtime_meta": meta}

    # ------------------------------------------------------------------ HITL
    async def hitl_wait_node(state: AnalysisState) -> dict[str, Any]:
        """Pseudo-interrupt for HITL. Real wait happens via LangGraph's
        ``interrupt`` mechanism when ``enable_hitl=True``. Here we just
        stamp the state.
        """
        return {"hitl_status": state.get("hitl_status") or "pending"}

    return {
        "fundamental": fundamental_node,
        "industry": industry_node,
        "policy": policy_node,
        "liquidity": liquidity_node,
        "cornerstone": cornerstone_node,
        "sentiment": sentiment_node,
        "valuation": valuation_node,
        "debate": debate_node,
        "cross_check": cross_check_node,
        "synthesize": synthesize_node,
        "create_snapshot": create_snapshot_node,
        "hitl_wait": hitl_wait_node,
        "report": report_node,
    }


__all__ = ("make_nodes",)


_ = time  # type marker for cost / runtime helpers used in Phase 7
