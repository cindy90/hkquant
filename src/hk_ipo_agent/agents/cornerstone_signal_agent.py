"""Cornerstone Signal agent — ultimate_holder clustering (NACS Cluster Bonus).

Per PROJECT_SPEC.md §7.2 and ADR 0005 §2.

**Critical behaviour**: this agent MUST detect industry-capital syndicates
by clustering predicted cornerstones on their ``ultimate_holder`` attribute.
Empirical effect (NACS v8):
- Cluster ≥ 2 cornerstones with same ultimate_holder → 60d mean +22%
  (vs no-cluster +14% baseline), std ↓ 40%
- ``cluster_bonus_multiplier`` is written to ``ctx.extras`` for the
  synthesizer to weight up.

Inputs:
- ``ctx.extraction.shareholders`` — pre-IPO investors (may already share
  ultimate_holder).
- ``ctx.extras.cornerstone_profiles`` — predicted cornerstones (Phase 2
  ``cornerstone_profile_builder.py``); for Phase 5 may be empty, in
  which case we degrade gracefully (cluster_bonus = 0).
- ``ctx.extras.sponsor_track_records`` — sponsor 24m HK IPO win rate.

The clustering itself is deterministic (no LLM). The LLM produces the
qualitative narrative + scoring.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, ClassVar

from ..common.enums import AgentRole
from ..common.schemas import AgentOutput, DataSource, Finding
from .base import AgentContext, BaseAgent
from .scoring import CornerstoneScoreCard


@dataclass
class _ClusterResult:
    """Deterministic clustering output."""

    groups: list[dict[str, Any]]
    multi_member_groups: int
    multiplier: float


def cluster_by_ultimate_holder(
    cornerstones: list[dict[str, Any]],
) -> _ClusterResult:
    """Group cornerstones by ``ultimate_holder``; return clusters with ≥2 members.

    Empty / missing ultimate_holder is treated as a sentinel and not
    clustered. NACS v8 used "≥2 members in a single holder" as the
    cluster trigger. Multiplier ladder: 1 cluster → 1.10x, ≥2 → 1.20x.
    """
    by_holder: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for cs in cornerstones:
        holder = (cs.get("ultimate_holder") or "").strip()
        if not holder or holder.lower() in {"unknown", "n/a", "none"}:
            continue
        by_holder[holder].append(cs)

    groups: list[dict[str, Any]] = []
    multi = 0
    for holder, members in by_holder.items():
        groups.append(
            {
                "ultimate_holder": holder,
                "members": [m.get("name") or m.get("name_zh") for m in members],
                "count": len(members),
            }
        )
        if len(members) >= 2:
            multi += 1

    if multi >= 2:
        mult = 1.20
    elif multi == 1:
        mult = 1.10
    else:
        mult = 1.0

    groups = [g for g in groups if g["count"] >= 2]
    return _ClusterResult(groups=groups, multi_member_groups=multi, multiplier=mult)


class CornerstoneSignalAgent(BaseAgent):
    """Detects ultimate_holder syndicates + sponsor + cornerstone strength."""

    role: ClassVar[AgentRole] = AgentRole.CORNERSTONE_SIGNAL
    prompt_path: ClassVar[str] = "agents/cornerstone_signal.md"
    score_card_class = CornerstoneScoreCard

    async def run(self, ctx: AgentContext) -> AgentOutput:
        started = time.monotonic()
        cost_before = ctx.llm_client.cost_log.total_usd()

        cornerstones = ctx.extras.cornerstone_profiles or []
        sponsors = ctx.extras.sponsor_track_records or []

        # 1. Deterministic clustering.
        cluster = cluster_by_ultimate_holder(cornerstones)
        ctx.extras.cluster_bonus_multiplier = cluster.multiplier
        ctx.extras.cluster_groups = cluster.groups

        # 2. LLM narrative.
        body, _frontmatter = self._load_prompt_body()
        roster_brief = (
            "\n".join(
                f"- {cs.get('name')} | category={cs.get('category')} | "
                f"ultimate_holder={cs.get('ultimate_holder', 'unknown')}"
                for cs in cornerstones[:20]
            )
            if cornerstones
            else "(no predicted cornerstone roster available)"
        )
        sponsor_brief = (
            "\n".join(
                f"- {sp.get('name')}: 24m HK IPO win rate "
                f"{sp.get('win_rate_24m', 'n/a')} (n={sp.get('sample_size_24m', 0)})"
                for sp in sponsors[:5]
            )
            if sponsors
            else "(no sponsor track record available)"
        )
        cluster_brief = (
            "\n".join(
                f"- {g['ultimate_holder']}: {g['count']} cornerstones "
                f"({', '.join(filter(None, g['members'][:5]))})"
                for g in cluster.groups
            )
            or "(no multi-member clusters detected)"
        )

        user_msg = (
            f"# Target IPO\n"
            f"- {ctx.extraction.company_name_zh} ({ctx.extraction.stock_code or 'TBD'})\n"
            f"- Listing type: {ctx.extraction.listing_type.value}\n\n"
            f"# Predicted cornerstone roster\n{roster_brief}\n\n"
            f"# Sponsors\n{sponsor_brief}\n\n"
            f"# Detected clusters (ADR 0005 §2 — DO NOT recompute)\n"
            f"- cluster_bonus_multiplier = {cluster.multiplier:.2f}x "
            f"({cluster.multi_member_groups} multi-member group(s))\n"
            f"{cluster_brief}\n\n"
            f"# Task\nAssess sponsor + roster quality narratively; emit ScoreCard."
        )

        score_card: CornerstoneScoreCard | None = None
        try:
            resp = await self._call_llm(ctx, system=body, user=user_msg, max_tokens=2500)
            parsed = self._parse_score_card(resp.text)
            if isinstance(parsed, CornerstoneScoreCard):
                score_card = parsed
        except Exception:
            score_card = None

        if score_card is None:
            score_card = CornerstoneScoreCard(
                sponsor_quality=50.0,
                cornerstone_strength=50.0 if cornerstones else 30.0,
                cluster_bonus=0.0
                if not cluster.multi_member_groups
                else (50.0 if cluster.multi_member_groups == 1 else 100.0),
                notes="LLM unavailable — deterministic fallback ScoreCard.",
            )
        # Deterministic cluster_bonus override — never trust LLM here.
        elif cluster.multi_member_groups == 0:
            score_card.cluster_bonus = 0.0
        elif cluster.multi_member_groups == 1:
            score_card.cluster_bonus = 50.0
        else:
            score_card.cluster_bonus = 100.0

        citations = self._pick_extraction_citations(ctx.extraction, score_card.evidence_pages)
        findings: list[Finding] = []
        if cluster.groups:
            top = cluster.groups[0]
            findings.append(
                self._make_finding(
                    statement=(
                        f"NACS Cluster Bonus triggered: {len(cluster.groups)} multi-member "
                        f"ultimate_holder group(s); top: {top['ultimate_holder']} "
                        f"(n={top['count']})"
                    ),
                    evidence=(
                        f"cluster_bonus_multiplier={cluster.multiplier:.2f}x "
                        f"(NACS v8: ≥2 same holder → 60d mean +22% vs +14%, std ↓40%)"
                    ),
                    citations=citations,
                    confidence="high",
                )
            )
        if sponsors:
            top_sponsor = max(sponsors, key=lambda s: s.get("win_rate_24m") or 0.0)
            findings.append(
                self._make_finding(
                    statement=(
                        f"Lead sponsor {top_sponsor.get('name')}: 24m win rate "
                        f"{top_sponsor.get('win_rate_24m', 'n/a')}"
                    ),
                    evidence=f"n={top_sponsor.get('sample_size_24m', 0)} prior HK IPOs",
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
            uncertainty_flags=(["no_predicted_cornerstones"] if not cornerstones else []),
            data_sources_used=[
                DataSource(source="kb_cornerstones", detail=f"n={len(cornerstones)}"),
                DataSource(source="kb_sponsors", detail=f"n={len(sponsors)}"),
                DataSource(source="prospectus", detail=ctx.extraction.prospectus_id),
            ],
            cost_usd=Decimal(str(cost_after - cost_before)),
            runtime_seconds=runtime,
        )


__all__ = ("CornerstoneSignalAgent", "cluster_by_ultimate_holder")
