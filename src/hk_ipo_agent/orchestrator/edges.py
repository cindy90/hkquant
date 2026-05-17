"""Conditional edge routers for the main LangGraph.

Per ADR 0010 §4: ``synthesize → create_snapshot → (hitl OR report)``.
HITL bypass is keyed on ``Settings.orchestrator.enable_hitl``.
"""

from __future__ import annotations

from ..common.settings import get_settings
from .states import AnalysisState


def route_after_snapshot(state: AnalysisState) -> str:
    """Route to ``hitl_wait`` if HITL enabled (and not already approved),
    otherwise straight to ``report``.
    """
    settings = get_settings().orchestrator
    if not settings.enable_hitl:
        return "report"
    hitl = state.get("hitl_status")
    if hitl == "approved":
        return "report"
    # Else interrupt — LangGraph hitl mechanism will pause the graph here.
    return "hitl_wait"


def route_after_hitl(state: AnalysisState) -> str:
    """After human input, route to report if approved else END.

    R2-2: ``pending`` MUST route to END, not back to ``hitl_wait``.
    Pre-fix the loop-back combined with ``hitl_wait_node`` re-stamping
    ``pending`` produced a tight cycle that could only be broken by
    process termination. The spec intent (ADR 0010 §4 + CLAUDE.md
    «HITL 默认 bypass，生产 env 强制开») is that the graph pauses at
    pending and the EXTERNAL caller re-invokes the graph with
    ``hitl_status="approved"`` or ``"rejected"`` after collecting the
    human decision. Internal cycling is not how LangGraph HITL works.
    """
    hitl = state.get("hitl_status")
    if hitl == "approved":
        return "report"
    if hitl == "rejected":
        return "END"
    # Still pending (or unset) — graph returns control to caller for
    # an out-of-band human-input cycle.
    return "END"


def route_after_validation(state: AnalysisState) -> str:
    """Placeholder for Phase 3 validate_extraction → human_review fork.
    Phase 6 returns "parallel_agents" unconditionally; Phase 3 already
    flags issues via ``extraction.needs_human_review``."""
    extraction = state.get("extraction")
    if extraction is not None and extraction.needs_human_review:
        return "hitl_wait"
    return "parallel_agents"


__all__ = (
    "route_after_hitl",
    "route_after_snapshot",
    "route_after_validation",
)
