"""Bull debater — argues the IPO is attractive.

Per PROJECT_SPEC.md §3.8 (debate subgraph) and ADR 0010 §2.

Bull reads:
- the 7 ``AgentOutput`` (synthesizing positive findings)
- the ``ValuationEnsembleOutput`` (anchors price discussion)
- the previous round's ``bear_argument`` (if round > 1)

Bull produces a free-form positive argument string. No ScoreCard — debate
is qualitative; aggregation happens at Synthesizer level.
"""

from __future__ import annotations

import time
from typing import Any

from ..agents.base import load_prompt
from ..common.llm_client import LLMClient
from ..common.schemas import (
    AgentOutput,
    ValuationEnsembleOutput,
)


def _agent_briefs(agent_outputs: dict[str, AgentOutput]) -> str:
    """Compact one-line summary per agent for prompt inclusion."""
    lines: list[str] = []
    for role, out in agent_outputs.items():
        lines.append(
            f"- [{role}] overall={out.overall_score:.0f}; "
            f"top_findings={[f.statement[:80] for f in out.key_findings[:2]]}"
        )
    return "\n".join(lines) if lines else "(no agent outputs)"


async def run_bull(
    llm: LLMClient,
    *,
    agent_outputs: dict[str, AgentOutput],
    valuation: ValuationEnsembleOutput,
    prior_bear: str | None,
    ipo_id: str,
    round_number: int,
    model: str = "moonshot-v1-128k",
) -> tuple[str, float, Any]:
    """Run one Bull turn. Returns ``(argument, cost_delta, raw_resp)``."""
    body, _frontmatter = load_prompt("debate/bull.md")
    bear_block = f"\n\n# Previous Bear argument (to rebut)\n{prior_bear}\n" if prior_bear else ""
    user_msg = (
        f"# Round {round_number}\n"
        f"# Agent outputs\n{_agent_briefs(agent_outputs)}\n\n"
        f"# Valuation summary\n"
        f"- Ensemble P25/P50/P75 (RMB): "
        f"{valuation.ensemble_distribution.p25} / "
        f"{valuation.ensemble_distribution.p50} / "
        f"{valuation.ensemble_distribution.p75}\n"
        f"- Applicable models: {[m.model_name for m in valuation.single_models if m.applicable]}\n"
        f"- Implied price range: {valuation.implied_price_range}\n"
        f"{bear_block}\n"
        f"# Task\n"
        f"Argue why this IPO is attractive. Be concrete + cite agent findings. ≤ 600 chars."
    )

    cost_before = llm.cost_log.total_usd()
    _ = time.monotonic()
    resp = await llm.acomplete(
        model=model,
        messages=[{"role": "user", "content": user_msg}],
        system=body,
        max_tokens=1500,
        temperature=0.4,
        agent_role="bull",
        ipo_id=ipo_id,
    )
    cost_after = llm.cost_log.total_usd()
    return resp.text, float(cost_after - cost_before), resp


__all__ = ("run_bull",)
