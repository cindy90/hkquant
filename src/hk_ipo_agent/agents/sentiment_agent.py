"""Sentiment agent — theme heat + AI gilding (NACS Theme Tracker).

Per PROJECT_SPEC.md §7.2 and ADR 0005 §2 + §5.

This agent MUST consume:
1. ``themes/heat_today.json`` — per-theme 0-100 heat snapshot.
2. ``themes/theme_definitions.json`` — taxonomy for matching company → theme.
3. ``themes/ai_revenue_manual.json`` — base for AI gilding detection
   (claimed AI exposure but revenue share < 10% → narrative risk).

All three are surfaced via ``ctx.kb_tool`` (``agents/tools/kb_tool.py``).

Outputs:
- ``ctx.extras.theme_heat``: 0-1 normalized heat of the matched theme
- ``ctx.extras.theme_matched``: theme_id (or None)
- ``ctx.extras.ai_gilding_flag``: True if AI claim ↔ AI revenue < 10%
"""

from __future__ import annotations

import re
import time
from decimal import Decimal
from typing import Any, ClassVar

from ..common.enums import AgentRole
from ..common.schemas import AgentOutput, DataSource, Finding
from .base import AgentContext, BaseAgent
from .scoring import SentimentScoreCard

# AI gilding threshold (ADR 0005 §5 — NACS v8: revenue < 10% but claims AI = ×0.85).
_AI_GILDING_THRESHOLD: float = 0.10


def detect_ai_gilding(
    extraction_industry: str,
    extraction_business: str,
    ai_revenue_pct: float | None,
) -> bool:
    """Return True if the company claims AI exposure but ai_revenue_pct < 10%.

    "Claims AI exposure" = AI-related keyword in industry_code or business_model.
    Conservative: only flag when ai_revenue_pct is *known* and below threshold.
    """
    ind_blob = extraction_industry + " " + extraction_business
    claims_ai = False
    for keyword in ("AI", "人工智能", "智能", "machine learning", "深度学习"):
        # ASCII keywords need word-boundary match to avoid e.g. "AI" matching
        # "chain"; CJK keywords use substring (no word boundaries).
        if keyword.isascii():
            if re.search(rf"\b{re.escape(keyword)}\b", ind_blob, flags=re.IGNORECASE):
                claims_ai = True
                break
        elif keyword in ind_blob:
            claims_ai = True
            break

    if not claims_ai:
        return False
    if ai_revenue_pct is None:
        return False
    return ai_revenue_pct < _AI_GILDING_THRESHOLD


def lookup_ai_revenue(
    stock_code: str | None,
    ai_revenue_manual: dict[str, Any],
) -> float | None:
    """Look up the company's AI revenue share from ``ai_revenue_manual.json``."""
    if not stock_code:
        return None
    samples = ai_revenue_manual.get("samples", []) or []
    for sample in samples:
        if sample.get("code") == stock_code and not sample.get("needs_review"):
            try:
                return float(sample.get("ai_revenue_pct"))
            except (TypeError, ValueError):
                return None
    return None


class SentimentAgent(BaseAgent):
    """Market temperature + narrative risk + theme heat (ADR 0005 §5)."""

    role: ClassVar[AgentRole] = AgentRole.SENTIMENT
    prompt_path: ClassVar[str] = "agents/sentiment.md"
    score_card_class = SentimentScoreCard

    async def run(self, ctx: AgentContext) -> AgentOutput:  # noqa: PLR0915
        started = time.monotonic()
        cost_before = ctx.llm_client.cost_log.total_usd()

        # 1. KB reads
        themes_heat: dict[str, Any]
        theme_defs: dict[str, Any]
        ai_revenue: dict[str, Any]
        if ctx.kb_tool is None:
            themes_heat = {}
            theme_defs = {}
            ai_revenue = {}
        else:
            themes_heat = ctx.kb_tool.themes_heat()
            theme_defs = ctx.kb_tool.theme_definitions()
            ai_revenue = ctx.kb_tool.ai_revenue_manual()

        # 2. Match company to themes (deterministic).
        matched_theme: str | None = None
        theme_heat_pct: float | None = None
        if ctx.kb_tool is not None and theme_defs:
            matches = ctx.kb_tool.match_themes(
                industry_code=ctx.extraction.industry_code,
                company_name=ctx.extraction.company_name_zh
                + " "
                + (ctx.extraction.company_name_en or ""),
            )
            if matches:
                matched_theme = matches[0]
                # Read heat (0-100 in JSON); normalize to 0-1 for downstream.
                heat_record = themes_heat.get("themes", {}).get(matched_theme, {})
                raw_heat = heat_record.get("heat_score")
                if isinstance(raw_heat, (int, float)):
                    theme_heat_pct = float(raw_heat) / 100.0

        ctx.extras.theme_matched = matched_theme
        # Don't overwrite ctx.extras.theme_heat to None — sentiment_agent
        # produces this signal, but ADR 0019 `_assert_required_extras` then
        # fails on the same key. Preserve any caller-supplied value when our
        # kb_tool path has nothing to report (fallback branch).
        if theme_heat_pct is not None:
            ctx.extras.theme_heat = theme_heat_pct

        # 3. AI gilding detection.
        ai_pct = lookup_ai_revenue(ctx.extraction.stock_code, ai_revenue)
        gilding = detect_ai_gilding(
            extraction_industry=ctx.extraction.industry_code
            + " "
            + (ctx.extraction.industry_description or ""),
            extraction_business=ctx.extraction.business_model or "",
            ai_revenue_pct=ai_pct,
        )
        ctx.extras.ai_gilding_flag = gilding

        # 4. LLM narrative
        # R1-4: extract the ternary into a named line so the if/else binds
        # only to the heat line, not the entire user_msg parenthesis.
        # Pre-fix the whole concat was swallowed when theme_heat_pct=None.
        theme_heat_line = (
            f"- theme_heat: {theme_heat_pct:.3f} (0-1 scale)\n"
            if theme_heat_pct is not None
            else "- theme_heat: N/A\n"
        )
        body, _frontmatter = self._load_prompt_body()
        user_msg = (
            f"# Target IPO\n"
            f"- {ctx.extraction.company_name_zh} ({ctx.extraction.stock_code or 'TBD'})\n"
            f"- Industry: {ctx.extraction.industry_code} — {ctx.extraction.industry_description}\n\n"
            f"# Computed signals (DO NOT recompute)\n"
            f"- matched_theme: {matched_theme or 'none'}\n"
            f"{theme_heat_line}"
            f"- ai_revenue_pct (lookup): {ai_pct}\n"
            f"- ai_gilding_flag: {gilding}\n\n"
            f"# Competing IPOs in 60d window\n"
            f"{len(ctx.extras.competing_ipos)} contemporaneous HK IPO(s)\n\n"
            f"# Task\n"
            f"Assess market temperature + narrative risk + theme momentum; emit ScoreCard."
        )

        score_card: SentimentScoreCard | None = None
        try:
            resp = await self._call_llm(ctx, system=body, user=user_msg, max_tokens=2500)
            parsed = self._parse_score_card(resp.text)
            if isinstance(parsed, SentimentScoreCard):
                score_card = parsed
        except Exception:
            score_card = None

        if score_card is None:
            score_card = SentimentScoreCard(
                market_temperature=50.0,
                narrative_risk=70.0 if gilding else 40.0,
                theme_heat=(theme_heat_pct or 0.0) * 100.0,
                notes="LLM unavailable — deterministic fallback ScoreCard.",
            )
        else:
            # Deterministic override of theme_heat from KB data
            score_card.theme_heat = (theme_heat_pct or 0.0) * 100.0

        citations = self._pick_extraction_citations(ctx.extraction, score_card.evidence_pages)
        findings: list[Finding] = []
        if matched_theme:
            findings.append(
                self._make_finding(
                    statement=(
                        f"Theme matched: {matched_theme}; heat = {(theme_heat_pct or 0.0):.2%}"
                    ),
                    evidence="keyword match via theme_definitions.json",
                    citations=citations,
                    confidence="medium",
                )
            )
        if gilding:
            findings.append(
                self._make_finding(
                    statement=(
                        f"AI gilding risk: claims AI exposure but AI revenue share "
                        f"= {ai_pct:.1%} < 10% threshold (ADR 0005 §5)"
                    ),
                    evidence=(
                        "ai_revenue_manual.json + extraction industry/business keywords; "
                        "NACS v8: apply ×0.85 narrative-risk multiplier (synthesizer aggregates)"
                    ),
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
            uncertainty_flags=(["no_matched_theme"] if matched_theme is None else []),
            data_sources_used=[
                DataSource(source="themes", detail=matched_theme or "no_match"),
                DataSource(source="prospectus", detail=ctx.extraction.prospectus_id),
            ],
            cost_usd=Decimal(str(cost_after - cost_before)),
            runtime_seconds=runtime,
        )


__all__ = ("SentimentAgent", "detect_ai_gilding", "lookup_ai_revenue")
