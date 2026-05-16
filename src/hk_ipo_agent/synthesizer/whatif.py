"""What-If valuation re-runner per PROJECT_SPEC.md §16.9 + ADR 0011.

Takes a baseline ``PredictionSnapshot`` + a dict of modified assumptions
and produces a new ``ValuationEnsembleOutput`` showing how the
ensemble's price range / distribution shifts. Used by:

- ``POST /api/whatif/run`` (UI's interactive scenario explorer)
- ``WhatIfRequest`` / ``WhatIfResponse`` Pydantic in ``common/schemas.py``

Supported assumption keys:
- ``regime_score``: float — overrides ``extras.regime_score``
- ``cluster_bonus_multiplier``: float — overrides ``extras.cluster_bonus_multiplier``
- ``theme_heat``: float — overrides ``extras.theme_heat``
- ``ai_gilding_flag``: bool — overrides ``extras.ai_gilding_flag``
- ``liquidity_discount``: float — passed to comparable model
- ``peer_ps_ttm`` / ``peer_pe_ttm``: list[float] — replaces market_data peer multiples
- ``mc_seed``: int — Monte Carlo seed (default reuses baseline)

Out-of-scope (Phase 7 MVP):
- Modifying agent narratives (agents not re-run; only valuation re-runs)
- Modifying extraction-level data (would invalidate snapshot integrity)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from time import monotonic
from typing import Any
from uuid import uuid4

from ..agents.workflow_extras import WorkflowExtras
from ..common.enums import ListingType
from ..common.schemas import (
    PredictionSnapshot,
    ProspectusExtraction,
    ValuationDistribution,
    ValuationEnsembleOutput,
    WhatIfResponse,
)
from ..valuation import (
    AHPremiumValuation,
    ComparableValuation,
    DCFValuation,
    MarketData,
    MilestonesValuation,
    PeerMultiples,
    PreIPOAnchorValuation,
    run_ensemble,
)
from ..valuation.industry import industry_models


@dataclass
class _WhatIfContext:
    """Internal struct: a reconstructed MarketData + WorkflowExtras."""

    market_data: MarketData
    extras: WorkflowExtras


def _market_data_from_snapshot(
    snapshot: PredictionSnapshot,
    modified: dict[str, Any],
) -> _WhatIfContext:
    """Reconstruct MarketData + extras with What-If overrides applied."""
    raw_ext = snapshot.input_data_snapshot.get("extras", {})
    if isinstance(raw_ext, dict):
        extras = WorkflowExtras(
            **{k: v for k, v in raw_ext.items() if k != "misc"},
            misc=raw_ext.get("misc", {}),
        )
    elif isinstance(raw_ext, WorkflowExtras):
        extras = WorkflowExtras(**asdict(raw_ext))
    else:
        extras = WorkflowExtras()

    # Apply NACS overrides
    for k in ("regime_score", "cluster_bonus_multiplier", "theme_heat", "ai_gilding_flag"):
        if k in modified:
            setattr(extras, k, modified[k])

    # Reconstruct MarketData. The original snapshot doesn't persist MarketData;
    # rebuild from extras + extraction.listing_type.
    extraction_dict = snapshot.input_data_snapshot.get("extraction", {})
    listing_type_str = extraction_dict.get("listing_type") or ListingType.MAINBOARD_TECH.value
    try:
        listing_type = ListingType(listing_type_str)
    except ValueError:
        listing_type = ListingType.MAINBOARD_TECH

    peer_ps = modified.get("peer_ps_ttm") or extras.peer_multiples.get("ps_ttm", [])
    peer_pe = modified.get("peer_pe_ttm") or extras.peer_multiples.get("pe_ttm", [])

    md = MarketData(
        as_of_date=snapshot.as_of_date,
        listing_type=listing_type,
        peer_multiples=PeerMultiples(
            ps_ttm=peer_ps,
            pe_ttm=peer_pe,
            sample_size=max(len(peer_ps), len(peer_pe)),
        ),
        regime_score=extras.regime_score,
        extra={
            "mc_seed": modified.get("mc_seed", 0),
            "liquidity_discount": modified.get("liquidity_discount", 0.0),
        },
    )
    return _WhatIfContext(market_data=md, extras=extras)


def _delta_summary(
    original: ValuationDistribution, new: ValuationDistribution
) -> dict[str, float]:
    """Per-percentile pct deltas (handles zero baseline safely)."""

    def pct(a: Decimal, b: Decimal) -> float:
        if a == 0:
            return 0.0
        return float((b - a) / a)

    return {
        "p10_pct": round(pct(original.p10, new.p10), 4),
        "p25_pct": round(pct(original.p25, new.p25), 4),
        "p50_pct": round(pct(original.p50, new.p50), 4),
        "p75_pct": round(pct(original.p75, new.p75), 4),
        "p90_pct": round(pct(original.p90, new.p90), 4),
    }


async def run_whatif(
    snapshot: PredictionSnapshot,
    modified_assumptions: dict[str, Any],
) -> WhatIfResponse:
    """Run the valuation ensemble with overrides; return delta vs snapshot."""
    started = monotonic()

    ctx = _market_data_from_snapshot(snapshot, modified_assumptions)
    extraction = ProspectusExtraction.model_validate(
        snapshot.input_data_snapshot["extraction"]
    )

    models = [
        ComparableValuation(),
        DCFValuation(),
        PreIPOAnchorValuation(),
        AHPremiumValuation(),
        MilestonesValuation(),
        *industry_models(),
    ]
    new_ensemble: ValuationEnsembleOutput = await run_ensemble(
        extraction, ctx.market_data, models
    )

    return WhatIfResponse(
        calculation_id=uuid4(),
        original_distribution=snapshot.valuation_output.ensemble_distribution,
        new_distribution=new_ensemble.ensemble_distribution,
        delta_summary=_delta_summary(
            snapshot.valuation_output.ensemble_distribution,
            new_ensemble.ensemble_distribution,
        ),
        affected_models=[m.model_name for m in new_ensemble.single_models if m.applicable],
        cost_usd=Decimal("0"),  # MC is local; no LLM cost
        runtime_ms=int((monotonic() - started) * 1000),
    )


__all__ = ("run_whatif",)
