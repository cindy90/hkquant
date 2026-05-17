"""Main LangGraph: ingest → 7-agent fanout → valuation → debate → cross_check
→ synthesize → create_snapshot → (hitl?) → report.

Per PROJECT_SPEC.md §8.1 + ADR 0010. ``ingest`` and ``extract`` from the
original spec block are pre-Phase-6 work (Phase 3 prospectus pipeline);
the entry point here assumes ``state["extraction"]`` is already set.
"""

from __future__ import annotations

from typing import Any

from ..common.llm_client import LLMClient
from ..valuation.base import MarketData
from .checkpoint import get_checkpointer
from .edges import route_after_hitl, route_after_snapshot
from .nodes import make_nodes
from .states import AnalysisState

# Agent node names that should run in parallel after extraction.
_PARALLEL_AGENTS: tuple[str, ...] = (
    "fundamental",
    "industry",
    "policy",
    "liquidity",
    "cornerstone",
    "sentiment",
)


def build_main_graph(
    *,
    llm_client: LLMClient,
    market_data: MarketData,
    prospectus_tool: Any = None,
    ifind_tool: Any = None,
    kb_tool: Any = None,
    use_checkpointer: bool = True,
) -> Any:
    """Build + compile the main LangGraph state machine.

    Returns the compiled graph; caller invokes ``await graph.ainvoke(state)``.
    """
    from langgraph.graph import END, START, StateGraph

    nodes = make_nodes(
        llm_client=llm_client,
        market_data=market_data,
        prospectus_tool=prospectus_tool,
        ifind_tool=ifind_tool,
        kb_tool=kb_tool,
    )

    g: Any = StateGraph(AnalysisState)
    for name, fn in nodes.items():
        g.add_node(name, fn)

    # Fan out: START → all 6 parallel agents (Phase 5 NACS-aware agents) +
    # we run "valuation" after them all because valuation_agent reads
    # ctx.extras (regime/cluster/theme) populated by policy/cornerstone/sentiment.
    for name in _PARALLEL_AGENTS:
        g.add_edge(START, name)

    # All 6 parallel agents must complete before valuation_agent runs.
    for name in _PARALLEL_AGENTS:
        g.add_edge(name, "valuation")

    # Sequential tail: valuation → debate → cross_check → synthesize.
    g.add_edge("valuation", "debate")
    g.add_edge("debate", "cross_check")
    g.add_edge("cross_check", "synthesize")
    g.add_edge("synthesize", "create_snapshot")

    # Conditional: hitl bypass via Settings.
    g.add_conditional_edges(
        "create_snapshot",
        route_after_snapshot,
        {"hitl_wait": "hitl_wait", "report": "report"},
    )
    g.add_conditional_edges(
        "hitl_wait",
        route_after_hitl,
        {"report": "report", "hitl_wait": "hitl_wait", "END": END},
    )
    g.add_edge("report", END)

    checkpointer = get_checkpointer() if use_checkpointer else None
    return g.compile(checkpointer=checkpointer) if checkpointer else g.compile()


__all__ = ("build_main_graph",)
