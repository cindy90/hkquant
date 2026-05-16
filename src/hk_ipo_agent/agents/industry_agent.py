"""Industry agent — competitive position / growth outlook / comp valuation.

Per PROJECT_SPEC.md §7.2.

Deterministic primitives:
- HHI of disclosed competitors (if mentioned in extraction.industry_description)
- Peer multiples summary stats from ``ctx.extras.peer_multiples`` if present
"""

from __future__ import annotations

import statistics
import time
from decimal import Decimal
from typing import ClassVar

from ..common.enums import AgentRole
from ..common.schemas import AgentOutput, DataSource, Finding
from .base import AgentContext, BaseAgent
from .scoring import IndustryScoreCard


def peer_multiple_summary(values: list[float]) -> dict[str, float]:
    """Return median + p25 + p75 + n for a list of multiples."""
    cleaned = [v for v in values if v is not None and v > 0]
    if not cleaned:
        return {"n": 0}
    cleaned.sort()
    return {
        "n": float(len(cleaned)),
        "p25": float(statistics.quantiles(cleaned, n=4)[0]) if len(cleaned) >= 4 else cleaned[0],
        "p50": float(statistics.median(cleaned)),
        "p75": float(statistics.quantiles(cleaned, n=4)[-1]) if len(cleaned) >= 4 else cleaned[-1],
    }


class IndustryAgent(BaseAgent):
    """Industry positioning + peer benchmark."""

    role: ClassVar[AgentRole] = AgentRole.INDUSTRY
    prompt_path: ClassVar[str] = "agents/industry.md"
    score_card_class = IndustryScoreCard

    async def run(self, ctx: AgentContext) -> AgentOutput:
        started = time.monotonic()
        cost_before = ctx.llm_client.cost_log.total_usd()

        peer = ctx.extras.peer_multiples or {}
        ps_summary = peer_multiple_summary(peer.get("ps_ttm", []))
        pe_summary = peer_multiple_summary(peer.get("pe_ttm", []))

        body, _frontmatter = self._load_prompt_body()
        user_msg = (
            f"# Target IPO\n"
            f"- {ctx.extraction.company_name_zh}\n"
            f"- Industry: {ctx.extraction.industry_code} — {ctx.extraction.industry_description}\n\n"
            f"# Peer multiples (computed)\n"
            f"- PS TTM: {ps_summary}\n"
            f"- PE TTM: {pe_summary}\n\n"
            f"# Task\nAssess competitive position, growth outlook, peer valuation; emit ScoreCard."
        )

        score_card: IndustryScoreCard | None = None
        try:
            resp = await self._call_llm(
                ctx, system=body, user=user_msg, max_tokens=2500
            )
            parsed = self._parse_score_card(resp.text)
            if isinstance(parsed, IndustryScoreCard):
                score_card = parsed
        except Exception:
            score_card = None

        if score_card is None:
            score_card = IndustryScoreCard(
                competitive_position=50.0,
                growth_outlook=60.0,
                comp_valuation=50.0,
                notes="LLM unavailable — neutral fallback ScoreCard.",
            )

        citations = self._pick_extraction_citations(ctx.extraction, score_card.evidence_pages)
        findings: list[Finding] = []
        if ps_summary.get("n", 0) >= 3:
            findings.append(
                self._make_finding(
                    statement=(
                        f"Peer PS TTM range: p25={ps_summary['p25']:.1f}x / "
                        f"p50={ps_summary['p50']:.1f}x / p75={ps_summary['p75']:.1f}x "
                        f"(n={int(ps_summary['n'])})"
                    ),
                    evidence="Industry peer pool aggregated from iFind",
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
                ["insufficient_peer_data"] if ps_summary.get("n", 0) < 3 else []
            ),
            data_sources_used=[
                DataSource(source="ifind", detail="peer_multiples"),
                DataSource(source="prospectus", detail=ctx.extraction.prospectus_id),
            ],
            cost_usd=Decimal(str(cost_after - cost_before)),
            runtime_seconds=runtime,
        )


__all__ = ("IndustryAgent", "peer_multiple_summary")
