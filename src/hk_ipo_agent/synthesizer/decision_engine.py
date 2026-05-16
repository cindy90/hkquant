"""Go / no-go decision rule engine.

Per PROJECT_SPEC.md §7 / §8 + ADR 0005 §2 (Regime Gate defense-in-depth).

Hard rules (applied before LLM synthesizer is even invoked):
- ``regime_score < 0`` → **SKIP** (ADR 0005 §2, defense in depth)
- ``ensemble has no applicable models`` → **WAIT_FOR_SIGNAL**
- ``ai_gilding_flag = True`` AND ``narrative_risk score ≥ 70`` → **SKIP**

Soft thresholds (applied to scorecard.overall):
- overall ≥ 75 → **PARTICIPATE** (allocation 4-7%)
- 60 ≤ overall < 75 → **PARTIAL** (allocation 1-3%)
- 45 ≤ overall < 60 → **WAIT_FOR_SIGNAL**
- overall < 45 → **SKIP**

The LLM synthesizer's job is to write the narrative + adjust the
allocation within the band — it cannot override the hard SKIP rules.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..agents.workflow_extras import WorkflowExtras
from ..common.enums import DecisionType
from ..common.schemas import AgentOutput, ValuationEnsembleOutput


@dataclass(frozen=True)
class DecisionGate:
    """Result of running the deterministic decision gate."""

    decision: DecisionType
    suggested_allocation_pct: float | None
    hard_reason: str | None  # set when a hard rule fires
    overall_score: float


def _allocation_for_decision(decision: DecisionType, overall: float) -> float | None:
    """Map decision + overall score to allocation pct in [0,1]."""
    if decision == DecisionType.PARTICIPATE:
        # Scale 75..100 → 0.04..0.07
        return round(0.04 + (overall - 75.0) / 25.0 * 0.03, 4)
    if decision == DecisionType.PARTIAL:
        return round(0.01 + (overall - 60.0) / 15.0 * 0.02, 4)
    return None


def decide(
    *,
    overall_score: float,
    extras: WorkflowExtras,
    ensemble: ValuationEnsembleOutput,
    agent_outputs: dict[str, AgentOutput],
) -> DecisionGate:
    """Apply hard rules then soft thresholds. Return a ``DecisionGate``."""
    # Hard rules first (return early)
    if extras.regime_score is not None and extras.regime_score < 0:
        return DecisionGate(
            decision=DecisionType.SKIP,
            suggested_allocation_pct=None,
            hard_reason=(
                f"regime_score={extras.regime_score:.3f} < 0 (NACS Regime Gate, "
                "ADR 0005 §2)"
            ),
            overall_score=overall_score,
        )

    applicable_models = [m for m in ensemble.single_models if m.applicable]
    if not applicable_models:
        return DecisionGate(
            decision=DecisionType.WAIT_FOR_SIGNAL,
            suggested_allocation_pct=None,
            hard_reason="no applicable valuation models — insufficient data",
            overall_score=overall_score,
        )

    sentiment_out = agent_outputs.get("sentiment")
    narrative_risk = (
        sentiment_out.scores.get("narrative_risk", 0.0)
        if sentiment_out
        else 0.0
    )
    if extras.ai_gilding_flag and narrative_risk >= 70.0:
        return DecisionGate(
            decision=DecisionType.SKIP,
            suggested_allocation_pct=None,
            hard_reason=(
                f"ai_gilding_flag + narrative_risk={narrative_risk:.0f} (≥70) "
                "→ pass per ADR 0005 §5"
            ),
            overall_score=overall_score,
        )

    # Soft thresholds
    if overall_score >= 75.0:
        decision_type = DecisionType.PARTICIPATE
    elif overall_score >= 60.0:
        decision_type = DecisionType.PARTIAL
    elif overall_score >= 45.0:
        decision_type = DecisionType.WAIT_FOR_SIGNAL
    else:
        decision_type = DecisionType.SKIP

    alloc = _allocation_for_decision(decision_type, overall_score)
    return DecisionGate(
        decision=decision_type,
        suggested_allocation_pct=alloc,
        hard_reason=None,
        overall_score=overall_score,
    )


__all__ = ("DecisionGate", "decide")
