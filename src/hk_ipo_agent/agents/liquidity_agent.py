"""Liquidity agent — float quality / lockup risk / southbound eligibility.

Per PROJECT_SPEC.md §7.2.

Deterministic primitives:
- Pre-IPO controlling shareholder % (high % → low float quality)
- Number of pre-IPO investors with buyback clauses (lockup risk proxy)
- Listing type-based Stock Connect eligibility estimate
- Number of contemporaneous IPOs in 60d window (pipeline crowding)
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import ClassVar

from ..common.enums import AgentRole, ListingType
from ..common.schemas import AgentOutput, DataSource, Finding, ShareholderEntry
from .base import AgentContext, BaseAgent
from .scoring import LiquidityScoreCard

# Stock Connect eligibility tier by listing type (rough heuristic).
_SC_TIER: dict[ListingType, float] = {
    ListingType.MAINBOARD_OTHER: 80.0,
    ListingType.MAINBOARD_TECH: 75.0,
    ListingType.AH_DUAL: 90.0,
    ListingType.CH18C_COMMERCIALIZED: 50.0,
    ListingType.CH18C_PRE_COMMERCIAL: 30.0,
    ListingType.CH18A_BIOTECH: 40.0,
}


def controlling_share_pct(shareholders: list[ShareholderEntry]) -> float | None:
    """Sum of pct held by controlling shareholders."""
    controllers = [s for s in shareholders if s.is_controlling]
    if not controllers:
        return None
    return float(sum(s.pct_pre_ipo for s in controllers))


def buyback_clause_count(shareholders: list[ShareholderEntry]) -> int:
    """Number of pre-IPO investors with disclosed buyback clauses."""
    return sum(1 for s in shareholders if s.has_buyback_clause)


class LiquidityAgent(BaseAgent):
    """Float / lockup / Southbound eligibility."""

    role: ClassVar[AgentRole] = AgentRole.LIQUIDITY
    prompt_path: ClassVar[str] = "agents/liquidity.md"
    score_card_class = LiquidityScoreCard

    async def run(self, ctx: AgentContext) -> AgentOutput:
        started = time.monotonic()
        cost_before = ctx.llm_client.cost_log.total_usd()

        controlling = controlling_share_pct(ctx.extraction.shareholders)
        buyback_count = buyback_clause_count(ctx.extraction.shareholders)
        sc_tier = _SC_TIER.get(ctx.extraction.listing_type, 50.0)
        competing_n = len(ctx.extras.competing_ipos or [])

        body, _frontmatter = self._load_prompt_body()
        user_msg = (
            f"# Target IPO\n"
            f"- {ctx.extraction.company_name_zh}\n"
            f"- Listing type: {ctx.extraction.listing_type.value}\n\n"
            f"# Computed primitives\n"
            f"- Controlling shareholder %: "
            f"{controlling:.1%}\n" if controlling is not None else "- Controlling shareholder: n/a\n"
        )
        user_msg += (
            f"- Buyback-clause investors: {buyback_count}\n"
            f"- Stock Connect tier (heuristic): {sc_tier:.0f}\n"
            f"- Competing IPOs in 60d window: {competing_n}\n\n"
            f"# Task\nAssess float quality / lockup risk / Southbound eligibility; emit ScoreCard."
        )

        score_card: LiquidityScoreCard | None = None
        try:
            resp = await self._call_llm(ctx, system=body, user=user_msg, max_tokens=2500)
            parsed = self._parse_score_card(resp.text)
            if isinstance(parsed, LiquidityScoreCard):
                score_card = parsed
        except Exception:
            score_card = None

        if score_card is None:
            # Heuristic fallback: higher controlling % → lower float quality.
            float_q = 70.0
            if controlling is not None:
                float_q = max(0.0, min(100.0, 100.0 - controlling * 100.0))
            lockup = 40.0 + buyback_count * 5.0
            lockup = max(0.0, min(100.0, lockup))
            score_card = LiquidityScoreCard(
                float_quality=float_q,
                lockup_risk=lockup,
                southbound_eligibility=sc_tier,
                notes="LLM unavailable — heuristic fallback ScoreCard.",
            )
        else:
            # Always set southbound_eligibility from the deterministic tier.
            score_card.southbound_eligibility = sc_tier

        citations = self._pick_extraction_citations(ctx.extraction, score_card.evidence_pages)
        findings: list[Finding] = []
        if controlling is not None and controlling > 0.50:
            findings.append(
                self._make_finding(
                    statement=f"Concentrated ownership: controlling share = {controlling:.1%}",
                    evidence="Sum of is_controlling=True shareholders",
                    citations=citations,
                    confidence="high",
                )
            )
        if buyback_count > 0:
            findings.append(
                self._make_finding(
                    statement=(
                        f"{buyback_count} pre-IPO investor(s) hold buyback clauses → "
                        f"post-IPO lockup risk"
                    ),
                    evidence="extraction.shareholders[].has_buyback_clause",
                    citations=citations,
                    confidence="medium",
                )
            )

        cost_after = ctx.llm_client.cost_log.total_usd()
        runtime = time.monotonic() - started

        return AgentOutput(
            agent_role=self.role,
            scores=score_card.score_dict(),
            overall_score=max(0.0, min(100.0, score_card.overall())),
            key_findings=findings,
            uncertainty_flags=(
                ["no_shareholder_data"] if not ctx.extraction.shareholders else []
            ),
            data_sources_used=[
                DataSource(source="prospectus", detail=ctx.extraction.prospectus_id),
            ],
            cost_usd=Decimal(str(cost_after - cost_before)),
            runtime_seconds=runtime,
        )


__all__ = ("LiquidityAgent", "buyback_clause_count", "controlling_share_pct")
