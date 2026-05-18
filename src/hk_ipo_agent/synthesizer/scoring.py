"""Overall scorecard aggregator.

Per PROJECT_SPEC.md §3.8 / §7. Builds the final ``scorecard: dict[str, float]``
field of ``FinalDecision`` by extracting each agent's overall_score and
mixing in NACS modifiers (regime / cluster / theme).

Aggregation (ADR 0020 — corrected after the 越疆 2432.HK regression):
- ``base`` = arithmetic mean of the 7 agent overall_scores (0..100).
- NACS adjusters add to ``base`` (do NOT multiply / discount it):
    * regime_adj  ∈ [-20, +20]   (regime_score × 100, clamped)
    * cluster_adj ∈ [ 0,  +5]    (log-scaled cornerstone cluster bonus)
    * theme_adj   ∈ {-5, 0, +5}  (theme heat tier)
    * gilding_adj ∈ {-10, 0}     (AI-gilding penalty)
- ``overall = clamp(base + Σadj, 0, 100)``.

Why this changed (ADR 0020): the previous implementation was
``overall = base*0.6 + Σadj`` which the docstring described as "agent
average → 60% of final" but in practice compressed every IPO by 40%
without ever filling the remaining 40% (adj sum capped at +25). 越疆
2432.HK with base=44.29 / adj=0 scored overall=26.57 → SKIP, despite
post-listing +30% performance proving the underlying agent signal was
adequate. With the fixed formula it scores 44.29 → WAIT_FOR_SIGNAL,
matching the decision-engine threshold semantics in PROJECT_SPEC.md §7.

This is a heuristic; the Synthesizer LLM still produces the final
decision text and the rule engine in ``decision_engine.py`` enforces
hard SKIP triggers.
"""

from __future__ import annotations

import math

from ..agents.workflow_extras import WorkflowExtras
from ..common.schemas import AgentOutput


def build_scorecard(
    agent_outputs: dict[str, AgentOutput],
    extras: WorkflowExtras,
) -> dict[str, float]:
    """Build per-agent + NACS modifiers + final ``overall`` score."""
    scorecard: dict[str, float] = {}
    overalls: list[float] = []
    for role, out in agent_outputs.items():
        scorecard[role] = round(out.overall_score, 2)
        overalls.append(out.overall_score)

    base = sum(overalls) / len(overalls) if overalls else 50.0

    regime_adj = 0.0
    if extras.regime_score is not None:
        if extras.regime_score > 0:
            regime_adj = min(20.0, extras.regime_score * 100.0)
        else:
            regime_adj = max(-20.0, extras.regime_score * 100.0)
    scorecard["regime_adj"] = round(regime_adj, 2)

    cluster_adj = 0.0
    if extras.cluster_bonus_multiplier and extras.cluster_bonus_multiplier > 1.0:
        cluster_adj = 5.0 * math.log(extras.cluster_bonus_multiplier) / math.log(1.20)
    scorecard["cluster_adj"] = round(cluster_adj, 2)

    gilding_adj = -10.0 if extras.ai_gilding_flag else 0.0
    scorecard["gilding_adj"] = round(gilding_adj, 2)

    theme_adj = 0.0
    if extras.theme_heat is not None:
        # Hot theme (>0.7) gives a small boost; cold (<0.3) a small drag.
        if extras.theme_heat > 0.7:
            theme_adj = 5.0
        elif extras.theme_heat < 0.3:
            theme_adj = -5.0
    scorecard["theme_adj"] = round(theme_adj, 2)

    # ADR 0020: overall = base + Σadj. Previously base was multiplied by
    # 0.6, which structurally pushed every IPO toward SKIP regardless of
    # agent signal quality. See module docstring + ADR 0020 §Decision.
    overall = base + regime_adj + cluster_adj + gilding_adj + theme_adj
    scorecard["base_avg"] = round(base, 2)
    scorecard["overall"] = round(max(0.0, min(100.0, overall)), 2)
    return scorecard


__all__ = ("build_scorecard",)
