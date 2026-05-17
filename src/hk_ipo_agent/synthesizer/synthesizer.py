"""Top-level synthesizer agent — Opus 4.7.

Per PROJECT_SPEC.md §3.8 / §7 + ADR 0005 §2.

Orchestration:
1. Build deterministic scorecard from agent_outputs + extras (NACS modifiers)
2. Apply the rule-based ``decide()`` engine — hard rules cannot be overridden
3. Derive price range (defense-in-depth Regime Gate)
4. Call Opus to write ``key_reasons_for`` / ``key_reasons_against`` /
   ``confidence`` / narrative — but the LLM CANNOT change ``decision_type``
   or violate the price-range hard zeros
5. Attach trigger rules
6. Return a fully-populated ``FinalDecision`` (spec §6) + total cost
"""

from __future__ import annotations

import time
from decimal import Decimal

from pydantic import BaseModel, Field

from ..agents.base import load_prompt
from ..agents.workflow_extras import WorkflowExtras
from ..common.llm_client import LLMClient
from ..common.schemas import (
    AgentOutput,
    DebateOutput,
    FinalDecision,
    ValuationDistribution,
    ValuationEnsembleOutput,
)
from .decision_engine import DecisionGate, decide
from .price_range import derive_price_range
from .scoring import build_scorecard
from .trigger_rules import attach_trigger_rules, build_trigger_rules


class _SynthLLMOutput(BaseModel):
    """Constrained Opus JSON output."""

    confidence: float = Field(ge=0.0, le=1.0)
    key_reasons_for: list[str] = Field(default_factory=list)
    key_reasons_against: list[str] = Field(default_factory=list)
    narrative: str = ""
    # Allow Opus to *suggest* an allocation refinement within the band; the
    # rule engine still caps it.
    allocation_pct_suggested: float | None = Field(default=None, ge=0.0, le=0.10)


def _normalize_allocation(gate: DecisionGate, opus_suggested: float | None) -> float | None:
    """Cap Opus allocation to the band the rule engine assigned."""
    if gate.suggested_allocation_pct is None:
        return None
    if opus_suggested is None:
        return gate.suggested_allocation_pct
    # Band: ±25% of rule-engine value, never > 0.07 or < 0.005.
    base = gate.suggested_allocation_pct
    low = max(0.005, base * 0.75)
    high = min(0.07, base * 1.25)
    return round(max(low, min(high, opus_suggested)), 4)


def _build_distributions(
    ensemble: ValuationEnsembleOutput,
) -> tuple[ValuationDistribution, ValuationDistribution]:
    """Build naive 6m / 12m expected return distributions from ensemble.

    Phase 6: use the ensemble's own distribution directly (no temporal
    decay model). Phase 8 will calibrate distinct 6m / 12m forecasts.
    """
    return ensemble.ensemble_distribution, ensemble.ensemble_distribution


async def synthesize(
    llm: LLMClient,
    *,
    ipo_id: str,
    agent_outputs: dict[str, AgentOutput],
    valuation: ValuationEnsembleOutput,
    debate: DebateOutput,
    extras: WorkflowExtras,
    cross_check_notes: list[str] | None = None,
    model: str = "moonshot-v1-128k",
) -> tuple[FinalDecision, Decimal]:
    """Run the synthesizer; return ``(FinalDecision, total_cost_usd)``."""
    started = time.monotonic()
    cost_before = llm.cost_log.total_usd()

    # 1. Deterministic primitives
    scorecard = build_scorecard(agent_outputs, extras)
    gate = decide(
        overall_score=scorecard["overall"],
        extras=extras,
        ensemble=valuation,
        agent_outputs=agent_outputs,
    )
    low, fair, high = derive_price_range(valuation, regime_score=extras.regime_score)

    # 2. LLM call (Opus)
    body, _frontmatter = load_prompt("system/synthesizer.md")
    agent_brief = "\n".join(
        f"- [{r}] overall={o.overall_score:.0f}; "
        f"findings={len(o.key_findings)}; flags={o.uncertainty_flags or '-'}"
        for r, o in agent_outputs.items()
    )
    notes = cross_check_notes or []
    user_msg = (
        f"# Decision context\n"
        f"- Pre-LLM rule decision: {gate.decision.value}\n"
        f"- Hard reason (if any): {gate.hard_reason or 'none'}\n"
        f"- Scorecard overall: {scorecard['overall']:.1f}\n"
        f"- Scorecard breakdown: {scorecard}\n\n"
        f"# Agent overview\n{agent_brief}\n\n"
        f"# Valuation\n"
        f"- Price range RMB (low/fair/high): {low} / {fair} / {high}\n"
        f"- Ensemble notes: {valuation.notes}\n\n"
        f"# Debate ({len(debate.rounds)} rounds)\n"
        f"- Consensus: {debate.final_consensus[:300]}\n"
        f"- Unresolved: {debate.unresolved_issues}\n\n"
        f"# Cross-check\n{notes}\n\n"
        f"# Task\n"
        f"Emit a JSON object with: confidence (0-1), key_reasons_for (≤5), "
        f"key_reasons_against (≤5), narrative (≤800 chars), "
        f"allocation_pct_suggested (optional, ≤0.07)."
    )

    try:
        opus = await llm.acomplete_json(
            model=model,
            messages=[{"role": "user", "content": user_msg}],
            system=body,
            response_model=_SynthLLMOutput,
            max_tokens=2500,
            temperature=0.1,
            agent_role="synthesizer",
            ipo_id=ipo_id,
        )
    except Exception:
        opus = _SynthLLMOutput(
            confidence=0.5,
            key_reasons_for=["LLM synthesizer unavailable; falling back to scorecard."],
            key_reasons_against=[],
            narrative="Deterministic fallback (LLM error).",
        )

    # 3. Compose FinalDecision
    alloc = _normalize_allocation(gate, opus.allocation_pct_suggested)
    dist6, dist12 = _build_distributions(valuation)
    refs = [*agent_outputs, "valuation_ensemble", "debate"]

    decision = FinalDecision(
        decision=gate.decision,
        confidence=opus.confidence,
        suggested_allocation_pct=alloc,
        price_range_low=low,
        price_range_fair=fair,
        price_range_high=high,
        expected_return_6m=dist6,
        expected_return_12m=dist12,
        scorecard=scorecard,
        key_reasons_for=opus.key_reasons_for or [f"Scorecard overall = {scorecard['overall']:.1f}"],
        key_reasons_against=opus.key_reasons_against,
        trigger_rules=[],  # filled below
        references_to_agent_outputs=refs,
    )

    # 4. Attach trigger rules
    rules = build_trigger_rules(
        decision_type=decision.decision,
        ensemble=valuation,
        extras=extras,
    )
    decision = attach_trigger_rules(decision, rules)

    cost_after = llm.cost_log.total_usd()
    _ = time.monotonic() - started  # runtime tracked at orchestrator level
    return decision, Decimal(str(cost_after - cost_before))


__all__ = ("synthesize",)
