"""Valuation agent — orchestrates ``valuation/`` ensemble + qualitative scoring.

Per PROJECT_SPEC.md §7.2.

Unlike the other 6 agents, this one is **mostly deterministic**: it calls
``valuation.ensemble.run_ensemble()`` (Phase 4) with the standard model
roster + ``ctx.market_data``, and then emits a qualitative ScoreCard
about the methodology / assumption quality. The actual price range +
distribution is the ensemble's output.

The ensemble's ``ValuationEnsembleOutput`` is stashed on
``ctx.extras.misc['valuation_output']`` so the synthesizer (Phase 6) can
read it directly.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import ClassVar

from ..common.enums import AgentRole
from ..common.schemas import AgentOutput, DataSource, Finding
from ..valuation import (
    AHPremiumValuation,
    ComparableValuation,
    DCFValuation,
    MilestonesValuation,
    PreIPOAnchorValuation,
    run_ensemble,
)
from ..valuation.industry import industry_models
from .base import AgentContext, BaseAgent
from .scoring import ValuationScoreCard


class ValuationAgent(BaseAgent):
    """Drives the valuation ensemble; reports method fit + assumption quality."""

    role: ClassVar[AgentRole] = AgentRole.VALUATION
    prompt_path: ClassVar[str] = "agents/valuation.md"
    score_card_class = ValuationScoreCard

    async def run(self, ctx: AgentContext) -> AgentOutput:
        started = time.monotonic()
        cost_before = ctx.llm_client.cost_log.total_usd()

        # 1. Run the ensemble (Phase 4).
        models = [
            ComparableValuation(),
            DCFValuation(),
            PreIPOAnchorValuation(),
            AHPremiumValuation(),
            MilestonesValuation(),
            *industry_models(),
        ]
        output = await run_ensemble(ctx.extraction, ctx.market_data, models)
        ctx.extras.misc["valuation_output"] = output

        applicable = [m for m in output.single_models if m.applicable]
        p50 = float(output.ensemble_distribution.p50)
        p25 = float(output.ensemble_distribution.p25)
        p75 = float(output.ensemble_distribution.p75)
        up_down_ratio = (p75 / p25) if p25 > 0 else 1.0
        method_fit_score = min(100.0, len(applicable) * 20.0)  # 5 models → 100
        assumption_quality_default = 60.0

        # 2. LLM narrative (optional)
        body, _frontmatter = self._load_prompt_body()
        models_brief = "\n".join(
            f"- {m.model_name}: applicable={m.applicable}, p50={float(m.valuation_distribution.p50):.0f}"
            for m in output.single_models
        )
        user_msg = (
            f"# Ensemble result\n"
            f"- Applicable models: {len(applicable)} / {len(output.single_models)}\n"
            f"- Weights: {output.weights_used}\n"
            f"- P25/P50/P75 RMB: {p25:.0f} / {p50:.0f} / {p75:.0f}\n"
            f"- Implied price range: {output.implied_price_range}\n"
            f"- Notes: {output.notes}\n\n"
            f"# Per-model breakdown\n{models_brief}\n\n"
            f"# Task\nAssess method fit + assumption quality narratively; emit ScoreCard."
        )

        score_card: ValuationScoreCard | None = None
        try:
            resp = await self._call_llm(ctx, system=body, user=user_msg, max_tokens=2500)
            parsed = self._parse_score_card(resp.text)
            if isinstance(parsed, ValuationScoreCard):
                score_card = parsed
        except Exception:
            score_card = None

        if score_card is None:
            # Use a normalized upside/downside ratio: 1.0x ratio → 50, 3.0x → 100.
            ud_norm = min(100.0, max(0.0, 50.0 + (up_down_ratio - 1.0) * 25.0))
            score_card = ValuationScoreCard(
                method_fit=method_fit_score,
                assumption_quality=assumption_quality_default,
                upside_downside_ratio=ud_norm,
                notes="LLM unavailable — deterministic fallback ScoreCard.",
            )

        citations = self._pick_extraction_citations(ctx.extraction, score_card.evidence_pages)
        findings: list[Finding] = []
        findings.append(
            self._make_finding(
                statement=(
                    f"Ensemble fair value P50 = RMB {p50:.0f}; "
                    f"price range low/fair/high = "
                    f"{output.implied_price_range['low']} / "
                    f"{output.implied_price_range['fair']} / "
                    f"{output.implied_price_range['high']}"
                ),
                evidence=f"Aggregated across {len(applicable)} applicable models",
                citations=citations,
                confidence="medium",
            )
        )
        regime_gate_triggered = any("Regime Gate" in n for n in output.notes)
        if regime_gate_triggered:
            findings.append(
                self._make_finding(
                    statement="Regime Gate triggered — ensemble forces SKIP (price range zeroed)",
                    evidence=f"{output.notes}",
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
            uncertainty_flags=(["ensemble_no_applicable_models"] if not applicable else []),
            data_sources_used=[
                DataSource(source="prospectus", detail=ctx.extraction.prospectus_id),
                DataSource(source="ifind", detail="peer_multiples / macro"),
            ],
            cost_usd=Decimal(str(cost_after - cost_before)),
            runtime_seconds=runtime,
        )


__all__ = ("ValuationAgent",)
