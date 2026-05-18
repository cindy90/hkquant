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
from dataclasses import fields as dc_fields
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
    """Reducer for ``WorkflowExtras``: per-field "last non-None wins".

    R5-7: pre-fix this used ``asdict(left)`` + ``asdict(right)`` which
    deep-recursively copies every nested dataclass / list / dict on each
    reducer invocation. For a WorkflowExtras carrying e.g. 1314
    cornerstone_profiles, the reducer becomes O(n) per merge. The new
    implementation iterates ``dc_fields(left)`` and uses ``getattr`` /
    ``setattr`` directly — O(num_fields), independent of payload size.
    """
    # Cheap field-by-field copy. We mutate the freshly-constructed
    # ``out`` instance because WorkflowExtras is mutable by design
    # (it's the cross-agent state carrier).
    out = WorkflowExtras()
    for f in dc_fields(left):
        setattr(out, f.name, getattr(left, f.name))

    for f in dc_fields(right):
        key = f.name
        val = getattr(right, key)
        if key == "misc":
            # Merge dicts: right wins on collisions.
            out.misc = {**(out.misc or {}), **(val or {})}
            continue
        # Non-None / non-empty on right overrides.
        if val is None:
            continue
        if isinstance(val, (list, dict)) and not val:
            continue
        setattr(out, key, val)
    return out


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
