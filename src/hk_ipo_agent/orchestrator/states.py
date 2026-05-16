"""``AnalysisState`` — the LangGraph main-graph state per PROJECT_SPEC.md §3.8.

Carries every artifact the pipeline produces, from prospectus extraction
through final ``FinalDecision``. Uses ``TypedDict`` + ``Annotated`` reducers
so LangGraph can fan-out / fan-in the 7 expert agents without overwrites
(ADR 0010 §5: ``operator.or_`` dict-merge reducer).

Reducer choice cheat sheet (see ADR 0010):
- ``agent_outputs``: ``Annotated[dict, operator.or_]`` — each fanout agent
  returns ``{"agent_outputs": {role: AgentOutput}}``; the reducer merges them.
- ``extras``: ``Annotated[dict, _merge_extras]`` — same shape but with
  a custom merger because ``WorkflowExtras`` is a dataclass.
- ``valuation_output`` / ``debate_output`` / ``decision`` / ``snapshot_id``:
  default reducer (replace) — only one producer per field.
"""

from __future__ import annotations

import operator
from dataclasses import asdict
from datetime import date
from typing import Annotated, Any, TypedDict
from uuid import UUID

from ..agents.workflow_extras import WorkflowExtras
from ..common.schemas import (
    AgentOutput,
    DebateOutput,
    FinalDecision,
    ProspectusExtraction,
    ValuationEnsembleOutput,
)


def _merge_extras(left: WorkflowExtras, right: WorkflowExtras) -> WorkflowExtras:
    """Reducer for ``WorkflowExtras``: per-field "last non-None wins"."""
    merged_dict = asdict(left)
    for key, val in asdict(right).items():
        if key == "misc":
            merged_dict["misc"] = {**(merged_dict.get("misc") or {}), **(val or {})}
            continue
        # Non-None on right overrides.
        if val is None:
            continue
        if isinstance(val, (list, dict)) and not val:
            continue
        merged_dict[key] = val
    # Reconstruct as dataclass.
    misc = merged_dict.pop("misc", {})
    return WorkflowExtras(**merged_dict, misc=misc)


class AnalysisState(TypedDict, total=False):
    """LangGraph main-graph state. Total=False so partial returns merge cleanly."""

    # --- identity / metadata ---
    ipo_id: str
    prospectus_id: str
    as_of_date: date

    # --- Phase 3 input (deterministic, set once at START) ---
    extraction: ProspectusExtraction

    # --- Phase 5 fanout outputs (7 expert agents) ---
    agent_outputs: Annotated[dict[str, AgentOutput], operator.or_]

    # --- Cross-agent NACS signals (mutated by policy/cornerstone/sentiment) ---
    extras: Annotated[WorkflowExtras, _merge_extras]

    # --- Phase 4 valuation ensemble ---
    valuation_output: ValuationEnsembleOutput

    # --- Phase 6 debate + cross-check ---
    debate_output: DebateOutput
    cross_check_notes: list[str]

    # --- Phase 6 synthesizer → FinalDecision ---
    decision: FinalDecision

    # --- Phase 7.5 prediction registry: snapshot must exist before report ---
    snapshot_id: UUID

    # --- HITL gate (ADR 0010 §4): non-None means waiting for human ---
    hitl_status: str  # "pending" / "approved" / "rejected" / None

    # --- Free-form runtime metadata (cost / runtime aggregates) ---
    runtime_meta: dict[str, Any]


__all__ = ("AnalysisState", "_merge_extras")
