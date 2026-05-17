"""Policy agent — regulatory regime + NACS Regime Gate.

Per PROJECT_SPEC.md §7.2 and ADR 0005 §2.

This agent MUST output ``regime_score`` (population standard 30d return %
of HK IPOs that priced in ``[pricing_date - 120d, pricing_date - 30d]``)
because:

1. The valuation ensemble (``valuation/ensemble.py``) applies a hard gate:
   ``regime_score < 0 → force SKIP``. This is the most consequential single
   signal in the system (NACS v7 empirics: regime≥0 subsample 60d IC=+0.247
   vs full-sample +0.057, t-stat +2.41).
2. Other agents (synthesizer downstream) factor this in as regime context.

Computation path:
- Try ``ctx.ifind_tool.ipo_history()`` for the [-120d, -30d] window.
- Fall back to PG ``ipo_postmarket`` table (Phase 7 will wire DB tool).
- For Phase 5 we accept either real data or, in tests, a manually
  injected value via ``ctx.extras.misc['regime_score_override']``.

Phase 5 deliberately keeps the **calculation deterministic** (median
30d return) — the LLM only writes the narrative + assesses
``regime_fit`` / ``policy_tailwind`` qualitatively.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, ClassVar

from ..common.enums import AgentRole, Confidence
from ..common.schemas import AgentOutput, DataSource, Finding
from .base import AgentContext, BaseAgent
from .scoring import PolicyScoreCard


@dataclass
class _RegimeComputation:
    """Internal result of ``compute_regime_score``."""

    score: float | None
    sample_size: int
    window_start: date | None
    window_end: date | None
    raw_returns: list[float]


async def compute_regime_score(ctx: AgentContext) -> _RegimeComputation:
    """Compute the NACS Regime Gate score (ADR 0005 §2).

    Window: ``[pricing_date - 120d, pricing_date - 30d]`` (90-day band
    ending 30 days before the target IPO prices). Metric: median of each
    new listing's 30-day cumulative return.

    Falls back to ``extras.misc['regime_score_override']`` if no real
    data source is available (e.g. unit tests).
    """
    override = ctx.extras.misc.get("regime_score_override")
    if override is not None:
        return _RegimeComputation(
            score=float(override),
            sample_size=0,
            window_start=None,
            window_end=None,
            raw_returns=[],
        )

    pricing = ctx.extras.pricing_date or ctx.market_data.as_of_date
    if not isinstance(pricing, date):
        return _RegimeComputation(
            score=None, sample_size=0, window_start=None, window_end=None, raw_returns=[]
        )

    window_start = pricing - timedelta(days=120)
    window_end = pricing - timedelta(days=30)

    if ctx.ifind_tool is None:
        return _RegimeComputation(
            score=None,
            sample_size=0,
            window_start=window_start,
            window_end=window_end,
            raw_returns=[],
        )

    try:
        history_raw = await ctx.ifind_tool.ipo_history(
            as_of_date=window_end, start=window_start, pool_filter="AHK"
        )
        history: list[dict[str, Any]] = history_raw if isinstance(history_raw, list) else []
    except Exception:
        return _RegimeComputation(
            score=None,
            sample_size=0,
            window_start=window_start,
            window_end=window_end,
            raw_returns=[],
        )

    returns: list[float] = []
    for record in history:
        r30 = record.get("return_30d") or record.get("ret_30d")
        if r30 is None:
            continue
        try:
            returns.append(float(r30))
        except (TypeError, ValueError):
            continue

    if not returns:
        return _RegimeComputation(
            score=None,
            sample_size=0,
            window_start=window_start,
            window_end=window_end,
            raw_returns=[],
        )

    median = statistics.median(returns)
    return _RegimeComputation(
        score=median,
        sample_size=len(returns),
        window_start=window_start,
        window_end=window_end,
        raw_returns=returns,
    )


class PolicyAgent(BaseAgent):
    """Outputs ``regime_score`` (ADR 0005 §2) + regulatory fit narrative."""

    role: ClassVar[AgentRole] = AgentRole.POLICY
    prompt_path: ClassVar[str] = "agents/policy.md"
    score_card_class = PolicyScoreCard

    async def run(self, ctx: AgentContext) -> AgentOutput:
        started = time.monotonic()
        cost_before = ctx.llm_client.cost_log.total_usd()

        # 1. Deterministic regime score (cannot delegate to LLM).
        regime = await compute_regime_score(ctx)
        ctx.extras.regime_score = regime.score

        # 2. LLM narrative + qualitative scores.
        body, _frontmatter = self._load_prompt_body()
        regime_str = "N/A" if regime.score is None else f"{regime.score:.4f}"
        regulatory_regime = ctx.extras.misc.get("regulatory_regime", "post_new_pricing")
        user_msg = (
            f"# Target IPO\n"
            f"- 公司: {ctx.extraction.company_name_zh} ({ctx.extraction.stock_code or 'TBD'})\n"
            f"- Listing type: {ctx.extraction.listing_type.value}\n"
            f"- Industry: {ctx.extraction.industry_code} — {ctx.extraction.industry_description}\n"
            f"- Pricing date (planned): {ctx.extras.pricing_date or ctx.market_data.as_of_date}\n\n"
            f"# Computed signals (DO NOT recompute — already deterministic)\n"
            f"- NACS regime_score = {regime_str} (n={regime.sample_size}, window={regime.window_start}~{regime.window_end})\n"
            f"- Active regulatory regime: {regulatory_regime}\n\n"
            f"# Task\n"
            f"Assess regulatory fit + policy tailwind narratively, then emit the ScoreCard."
        )
        system = body

        score_card: PolicyScoreCard | None = None
        narrative = ""
        try:
            llm_resp = await self._call_llm(
                ctx,
                system=system,
                user=user_msg,
                max_tokens=2500,
                temperature=0.2,
            )
            narrative = llm_resp.text
            parsed = self._parse_score_card(narrative)
            if isinstance(parsed, PolicyScoreCard):
                score_card = parsed
        except Exception:
            # LLM failure is non-fatal — emit a deterministic minimal scorecard
            score_card = None

        if score_card is None:
            score_card = PolicyScoreCard(
                regime_fit=50.0,
                policy_tailwind=50.0,
                regime_score=(regime.score or 0.0) * 100.0,
                notes="LLM unavailable — deterministic fallback ScoreCard.",
            )
        else:
            # Always overwrite the LLM-suggested regime_score with the deterministic value.
            score_card.regime_score = (regime.score or 0.0) * 100.0

        # 3. Findings + citations
        citations = self._pick_extraction_citations(ctx.extraction, score_card.evidence_pages)
        findings: list[Finding] = []
        if regime.score is not None:
            findings.append(
                self._make_finding(
                    statement=(
                        f"NACS Regime Gate: median 30d return of HK IPOs in last 90d "
                        f"= {regime.score:.2%} (n={regime.sample_size}); "
                        f"{'BELOW' if regime.score < 0 else 'ABOVE'} threshold"
                    ),
                    evidence=(
                        f"Window [{regime.window_start} ~ {regime.window_end}]; "
                        f"median of {regime.sample_size} IPO 30d returns"
                    ),
                    citations=citations,
                    confidence="high" if regime.sample_size >= 5 else "low",
                )
            )

        cost_after = ctx.llm_client.cost_log.total_usd()
        runtime = time.monotonic() - started

        return AgentOutput(
            agent_role=self.role,
            scores=score_card.score_dict(),
            overall_score=max(0.0, min(100.0, score_card.overall())),
            key_findings=findings,
            uncertainty_flags=(["regime_score_unavailable"] if regime.score is None else []),
            data_sources_used=[
                DataSource(source="ifind", detail="get_ipo_history (regime window)"),
                DataSource(source="prospectus", detail=ctx.extraction.prospectus_id),
            ],
            cost_usd=Decimal(str(cost_after - cost_before)),
            runtime_seconds=runtime,
        )


__all__ = ("PolicyAgent", "compute_regime_score")


_ = Confidence  # silence unused — re-exported for tests
