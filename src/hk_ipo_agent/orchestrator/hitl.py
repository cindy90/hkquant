"""Human-in-the-loop helpers per ADR 0010 §4.

Phase 6 keeps HITL light: ``approve(state, reviewer)`` mutates state +
returns; production env will wire this through LangGraph's
``interrupt_before=["hitl_wait"]`` and a UI callback.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..common.settings import get_settings
from .states import AnalysisState


def hitl_enabled() -> bool:
    """Returns True iff ``Settings.orchestrator.enable_hitl`` is on."""
    return get_settings().orchestrator.enable_hitl


def approve(state: AnalysisState, *, reviewer: str) -> dict[str, Any]:
    """Mark the state as approved; orchestrator will resume to ``report``."""
    meta = state.get("runtime_meta") or {}
    meta = {
        **meta,
        "hitl_reviewer": reviewer,
        "hitl_approved_at": datetime.now(UTC).isoformat(),
    }
    return {"hitl_status": "approved", "runtime_meta": meta}


def reject(state: AnalysisState, *, reviewer: str, reason: str) -> dict[str, Any]:
    """Mark the state as rejected; orchestrator will route to END."""
    meta = state.get("runtime_meta") or {}
    meta = {
        **meta,
        "hitl_reviewer": reviewer,
        "hitl_rejected_at": datetime.now(UTC).isoformat(),
        "hitl_reject_reason": reason,
    }
    return {"hitl_status": "rejected", "runtime_meta": meta}


__all__ = ("approve", "hitl_enabled", "reject")
