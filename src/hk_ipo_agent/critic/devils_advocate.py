"""Devil's Advocate — meta-challenges both Bull and Bear arguments.

Per PROJECT_SPEC.md §3.8 and ADR 0010 §2.

Devil does NOT take a side on the IPO. Devil interrogates the QUALITY of
the Bull / Bear arguments:
- Are the cited data points fresh / authoritative?
- Are causal links speculative (correlation ≠ causation)?
- Are critical risks being ignored by both sides?

Output is a free-form "challenge" string. Stored on each ``DebateRound``.
"""

from __future__ import annotations

from typing import Any

from ..agents.base import load_prompt
from ..common.llm_client import LLMClient
from ..common.settings import resolve_agent_model


async def run_devils_advocate(
    llm: LLMClient,
    *,
    bull_argument: str,
    bear_argument: str,
    ipo_id: str,
    round_number: int,
    model: str | None = None,
) -> tuple[str, float, Any]:
    """Run one Devil turn. Returns ``(challenge, cost_delta, raw_resp)``.

    R4-1: defaults to ``resolve_agent_model("debate.devils_advocate")``.
    """
    if model is None:
        model = resolve_agent_model("debate.devils_advocate")
    body, _frontmatter = load_prompt("debate/devils_advocate.md")
    user_msg = (
        f"# Round {round_number}\n"
        f"# Bull argument\n{bull_argument}\n\n"
        f"# Bear argument\n{bear_argument}\n\n"
        f"# Task\n"
        f"Meta-challenge both arguments: question data quality, causal links, "
        f"and unaddressed risks. Do NOT take a side on the IPO. ≤ 500 chars."
    )

    cost_before = llm.cost_log.total_usd()
    resp = await llm.acomplete(
        model=model,
        messages=[{"role": "user", "content": user_msg}],
        system=body,
        max_tokens=1200,
        temperature=0.5,
        agent_role="devils_advocate",
        ipo_id=ipo_id,
    )
    cost_after = llm.cost_log.total_usd()
    return resp.text, float(cost_after - cost_before), resp


__all__ = ("run_devils_advocate",)
