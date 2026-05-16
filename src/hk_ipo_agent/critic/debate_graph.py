"""Bull-Bear-Devil debate subgraph with Jaccard early-stop.

Per ADR 0010 §1. Each round:
1. Bull argues
2. Bear argues
3. Devil meta-challenges
4. Check Jaccard(bull_tokens, bear_tokens) — if ≥ threshold + round ≥ 1
   → converged, stop. Else next round (up to ``max_rounds``).

We don't actually build a LangGraph subgraph here — it's overkill for
a sequential debate. We expose ``run_debate()`` which the main graph's
``debate`` node calls directly.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from ..common.llm_client import LLMClient
from ..common.schemas import (
    AgentOutput,
    DebateOutput,
    DebateRound,
    ValuationEnsembleOutput,
)
from ..common.settings import get_settings
from .bear import run_bear
from .bull import run_bull
from .devils_advocate import run_devils_advocate

# Character-level n-gram (n=1) Jaccard for Chinese-friendly similarity.
# Phase 8 calibration may upgrade to BGE embeddings.
_TOKEN_RE = re.compile(r"[\w一-鿿]+", re.UNICODE)


def tokenize(text: str) -> set[str]:
    """Tokenize to lowercase ascii words + CJK characters."""
    text = text.lower()
    tokens: set[str] = set()
    for match in _TOKEN_RE.finditer(text):
        token = match.group()
        # Split CJK runs into single characters; keep ASCII words whole.
        if any("一" <= c <= "鿿" for c in token):
            tokens.update(c for c in token if "一" <= c <= "鿿")
        else:
            tokens.add(token)
    return tokens


def jaccard(a: str, b: str) -> float:
    """Token-set Jaccard similarity between two strings."""
    ta, tb = tokenize(a), tokenize(b)
    if not ta and not tb:
        return 1.0
    inter = ta & tb
    union = ta | tb
    return len(inter) / len(union) if union else 0.0


async def run_debate(
    llm: LLMClient,
    *,
    agent_outputs: dict[str, AgentOutput],
    valuation: ValuationEnsembleOutput,
    ipo_id: str,
    max_rounds: int | None = None,
    jaccard_threshold: float | None = None,
) -> tuple[DebateOutput, Decimal]:
    """Run Bull-Bear-Devil debate with Jaccard early-stop.

    Returns ``(DebateOutput, total_cost_usd)``.
    """
    settings = get_settings().orchestrator
    max_r = max_rounds if max_rounds is not None else settings.debate_max_rounds
    threshold = (
        jaccard_threshold
        if jaccard_threshold is not None
        else settings.debate_jaccard_threshold
    )

    rounds: list[DebateRound] = []
    prior_bear: str | None = None
    total_cost = 0.0

    for r in range(1, max_r + 1):
        bull_text, c1, _ = await run_bull(
            llm,
            agent_outputs=agent_outputs,
            valuation=valuation,
            prior_bear=prior_bear,
            ipo_id=ipo_id,
            round_number=r,
        )
        total_cost += c1
        bear_text, c2, _ = await run_bear(
            llm,
            agent_outputs=agent_outputs,
            valuation=valuation,
            prior_bull=bull_text,
            ipo_id=ipo_id,
            round_number=r,
        )
        total_cost += c2
        devil_text, c3, _ = await run_devils_advocate(
            llm,
            bull_argument=bull_text,
            bear_argument=bear_text,
            ipo_id=ipo_id,
            round_number=r,
        )
        total_cost += c3

        sim = jaccard(bull_text, bear_text)
        converged = sim >= threshold and r >= 1
        rounds.append(
            DebateRound(
                round_number=r,
                bull_argument=bull_text,
                bear_argument=bear_text,
                devil_challenge=devil_text,
                resolution=_summarize_resolution(bull_text, bear_text, devil_text)
                if converged or r == max_r
                else None,
            )
        )
        prior_bear = bear_text
        if converged:
            break

    final_consensus = (
        _build_final_consensus(rounds)
        if rounds
        else "No rounds executed."
    )
    unresolved: list[str] = []
    if rounds and rounds[-1].resolution is None:
        unresolved.append("max rounds reached without convergence")

    return (
        DebateOutput(
            rounds=rounds,
            final_consensus=final_consensus,
            unresolved_issues=unresolved,
        ),
        Decimal(str(total_cost)),
    )


def _summarize_resolution(bull: str, bear: str, devil: str) -> str:
    """Deterministic last-round summary; LLM-free to keep cost bounded."""
    # Take first sentence of each — Phase 8 may use a tiny LLM call to merge.
    def first_sentence(s: str) -> str:
        s = s.strip()
        for sep in ("。", ".", "！", "!", "？", "?", "\n"):
            idx = s.find(sep)
            if idx >= 0:
                return s[: idx + 1].strip()
        return s[:120]

    return (
        f"Bull: {first_sentence(bull)} | "
        f"Bear: {first_sentence(bear)} | "
        f"Devil: {first_sentence(devil)}"
    )


def _build_final_consensus(rounds: list[DebateRound]) -> str:
    """Pick the last round's resolution (if set) or the last bull+bear briefs."""
    last = rounds[-1]
    if last.resolution:
        return last.resolution
    return f"Bull: {last.bull_argument[:160]} | Bear: {last.bear_argument[:160]}"


_ = Any  # type marker
__all__ = ("jaccard", "run_debate", "tokenize")
