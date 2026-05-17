"""Fundamental agent — business quality / financial health / governance.

Per PROJECT_SPEC.md §7.2.

Deterministic computations:
- Revenue CAGR (from extraction.financials if ≥ 2 periods)
- Gross margin trend (last 3 periods)
- Customer concentration top-1 / top-5
- Control structure (any controlling shareholder?)

The LLM produces narrative + scoring on top of these primitives. The
prospectus tool (RAG) is used to fetch evidence-backed responses for
governance / business-model questions when available.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import ClassVar

from ..common.enums import AgentRole
from ..common.schemas import (
    AgentOutput,
    CustomerConcentration,
    DataSource,
    FinancialSnapshot,
    Finding,
)
from .base import AgentContext, BaseAgent
from .scoring import FundamentalScoreCard


def revenue_cagr(financials: list[FinancialSnapshot]) -> float | None:
    """Year-over-year revenue CAGR across all available periods.

    Returns None if < 2 periods or any revenue is non-positive.
    """
    revs = [
        float(f.revenue_rmb)
        for f in financials
        if f.revenue_rmb is not None and float(f.revenue_rmb) > 0
    ]
    if len(revs) < 2:
        return None
    years = len(revs) - 1
    return float((revs[-1] / revs[0]) ** (1.0 / years) - 1.0)


def gross_margin_trend(financials: list[FinancialSnapshot]) -> list[float]:
    """Return the last up-to-3 gross margins as floats (skip None)."""
    margins: list[float] = []
    for f in financials[-3:]:
        if f.gross_margin is not None:
            margins.append(float(f.gross_margin))
    return margins


def customer_concentration_top1(
    customer_concentration: list[CustomerConcentration],
) -> float | None:
    """Latest period's top-1 customer concentration, if available."""
    if not customer_concentration:
        return None
    return float(customer_concentration[-1].top1_pct)


class FundamentalAgent(BaseAgent):
    """Business / financial / governance assessment."""

    role: ClassVar[AgentRole] = AgentRole.FUNDAMENTAL
    prompt_path: ClassVar[str] = "agents/fundamental.md"
    score_card_class = FundamentalScoreCard

    async def run(self, ctx: AgentContext) -> AgentOutput:
        started = time.monotonic()
        cost_before = ctx.llm_client.cost_log.total_usd()

        # 1. Deterministic primitives
        cagr = revenue_cagr(ctx.extraction.financials)
        margins = gross_margin_trend(ctx.extraction.financials)
        top1 = customer_concentration_top1(ctx.extraction.customer_concentration)
        has_controlling = any(s.is_controlling for s in ctx.extraction.shareholders)

        # 2. LLM narrative
        body, _frontmatter = self._load_prompt_body()
        fin_brief = (
            "\n".join(
                f"- FY{f.fiscal_year} {f.fiscal_period}: revenue="
                f"{float(f.revenue_rmb) if f.revenue_rmb else 'n/a'}, "
                f"gross_margin={f.gross_margin}, "
                f"net_profit={float(f.net_profit_rmb) if f.net_profit_rmb else 'n/a'}"
                for f in ctx.extraction.financials[-3:]
            )
            or "(no financial snapshots)"
        )
        risks_brief = (
            "\n".join(
                f"- [{r.category}/{r.severity}] {r.description[:120]}"
                for r in ctx.extraction.risk_factors[:5]
            )
            or "(no risk factors extracted)"
        )

        # R1-4: extract conditional lines into named variables so each
        # if/else binds only to its own line. Pre-fix the precedence bug
        # made the entire user_msg parenthesis the body of the ternary,
        # silently dropping # Target IPO / # Financials snapshot / # Task.
        cagr_line = (
            f"- Revenue CAGR (n={len(ctx.extraction.financials)}): {cagr:.2%}\n"
            if cagr is not None
            else "- Revenue CAGR: insufficient periods\n"
        )
        top1_line = (
            f"- Gross margin (last 3): {margins}\n- Top-1 customer concentration: {top1:.2%}\n"
            if top1 is not None
            else f"- Gross margin (last 3): {margins}\n- Top-1 customer concentration: n/a\n"
        )
        user_msg = (
            f"# Target IPO\n"
            f"- {ctx.extraction.company_name_zh} ({ctx.extraction.stock_code or 'TBD'})\n"
            f"- Industry: {ctx.extraction.industry_code} — {ctx.extraction.industry_description}\n"
            f"- Business model: {ctx.extraction.business_model[:300]}\n\n"
            f"# Computed primitives (DO NOT recompute)\n"
            f"{cagr_line}"
            f"{top1_line}"
            f"- Has controlling shareholder: {has_controlling}\n\n"
            f"# Financials snapshot\n{fin_brief}\n\n"
            f"# Risk factors\n{risks_brief}\n\n"
            f"# Task\nAssess business / financial / governance qualitatively; emit ScoreCard."
        )

        score_card: FundamentalScoreCard | None = None
        try:
            resp = await self._call_llm(ctx, system=body, user=user_msg, max_tokens=3500)
            parsed = self._parse_score_card(resp.text)
            if isinstance(parsed, FundamentalScoreCard):
                score_card = parsed
        except Exception:
            score_card = None

        if score_card is None:
            # Deterministic fallback ScoreCard based on heuristics.
            biz_q = 50.0
            if cagr is not None:
                biz_q = min(100.0, max(0.0, 50.0 + cagr * 100.0))
            fin_h = 50.0
            if margins:
                fin_h = min(100.0, max(0.0, margins[-1] * 100.0))
            gov = 60.0 if has_controlling else 40.0
            score_card = FundamentalScoreCard(
                business_quality=biz_q,
                financial_health=fin_h,
                governance=gov,
                notes="LLM unavailable — heuristic fallback ScoreCard.",
            )

        # 3. Findings
        citations = self._pick_extraction_citations(ctx.extraction, score_card.evidence_pages)
        findings: list[Finding] = []
        if cagr is not None:
            findings.append(
                self._make_finding(
                    statement=f"Revenue CAGR = {cagr:.2%} over {len(ctx.extraction.financials)} periods",
                    evidence=f"Periods: {[f.fiscal_year for f in ctx.extraction.financials]}",
                    citations=citations,
                    confidence="high",
                )
            )
        if top1 is not None and top1 > 0.30:
            findings.append(
                self._make_finding(
                    statement=f"High top-1 customer concentration = {top1:.1%}",
                    evidence="customer_concentration in latest fiscal period",
                    citations=citations,
                    confidence="high",
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
                ["insufficient_financial_periods"] if len(ctx.extraction.financials) < 2 else []
            ),
            data_sources_used=[
                DataSource(source="prospectus", detail=ctx.extraction.prospectus_id),
            ],
            cost_usd=Decimal(str(cost_after - cost_before)),
            runtime_seconds=runtime,
        )


__all__ = ("FundamentalAgent", "customer_concentration_top1", "gross_margin_trend", "revenue_cagr")
