"""Bear debater — argues the IPO carries unacceptable risks.

Per PROJECT_SPEC.md §3.8 and ADR 0010 §2. Symmetric to Bull but pushes
the negative case.
"""

from __future__ import annotations

from typing import Any

from ..agents.base import load_prompt
from ..common.llm_client import LLMClient
from ..common.schemas import AgentOutput, ValuationEnsembleOutput
from .bull import _agent_briefs


async def run_bear(
    llm: LLMClient,
    *,
    agent_outputs: dict[str, AgentOutput],
    valuation: ValuationEnsembleOutput,
    prior_bull: str | None,
    ipo_id: str,
    round_number: int,
    model: str = "moonshot-v1-128k",
) -> tuple[str, float, Any]:
    """Run one Bear turn. Returns ``(argument, cost_delta, raw_resp)``."""
    body, _frontmatter = load_prompt("debate/bear.md")
    bull_block = (
        f"\n\n# Previous Bull argument (to challenge)\n{prior_bull}\n"
        if prior_bull
        else ""
    )
    regime_note = (
        "\n# NACS Regime Gate\n"
        "Negative regime_score triggers ensemble SKIP (ADR 0005 §2). "
        "If applicable, surface this as the strongest bear argument.\n"
    )
    user_msg = (
        f"# Round {round_number}\n"
        f"# Agent outputs\n{_agent_briefs(agent_outputs)}\n\n"
        f"# Valuation summary\n"
        f"- Ensemble P25/P50/P75 (RMB): "
        f"{valuation.ensemble_distribution.p25} / "
        f"{valuation.ensemble_distribution.p50} / "
        f"{valuation.ensemble_distribution.p75}\n"
        f"- Notes: {valuation.notes}\n"
        f"{regime_note}"
        f"{bull_block}\n"
        f"# Task\n"
        f"Argue the bear case. Cite specific risks + uncertainty flags. ≤ 600 chars."
    )

    cost_before = llm.cost_log.total_usd()
    resp = await llm.acomplete(
        model=model,
        messages=[{"role": "user", "content": user_msg}],
        system=body,
        max_tokens=1500,
        temperature=0.4,
        agent_role="bear",
        ipo_id=ipo_id,
    )
    cost_after = llm.cost_log.total_usd()
    return resp.text, float(cost_after - cost_before), resp


__all__ = ("run_bear",)
