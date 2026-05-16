"""Weighted ensemble across single valuation models + Regime Gate hard gate.

Per PROJECT_SPEC.md §3.7 and ADR 0005 §2.

Algorithm:
1. Run all configured models in parallel via ``asyncio.gather``.
2. Filter to ``applicable=True`` outputs only.
3. Weight blend per ``config/valuation_weights.yaml`` (looked up by listing_type).
4. Renormalize weights across the applicable subset.
5. Per-percentile linear blend of ``ValuationDistribution`` summaries
   (faster + sufficient for downstream agent consumption; full MC re-pooling
   reserved for the synthesizer's What-If path if needed).
6. **Regime Gate hard gate (ADR 0005 §2)** — if ``market_data.regime_score < 0``,
   force the ensemble to SKIP by zeroing the price range and adding a note.
   This is a hard truncation, not a soft penalty — the synthesizer also
   re-checks regime_score for defense-in-depth.

Legacy NACS post-adjustments (×0.70 for 18C high-val, AH-hedge tier, etc.)
are *not* applied here in Phase 4; they were empirically valid in v8 but
will be re-calibrated in Phase 8. ADR 0005 §3 documents the deferral.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from ..common.enums import ListingType
from ..common.schemas import (
    ProspectusExtraction,
    SingleModelValuation,
    ValuationDistribution,
    ValuationEnsembleOutput,
)
from .base import MarketData, ValuationModel

# Where to find the weights file relative to repo root.
_CONFIG_PATH: Path = Path(__file__).resolve().parents[3] / "config" / "valuation_weights.yaml"

# Regime Gate threshold (ADR 0005 §2). Below this → force SKIP.
REGIME_GATE_THRESHOLD: float = 0.0

# How implied_price_range maps from the blended distribution.
# low = p25, fair = p50, high = p75 (consistent with NACS v8 reporting).
_PRICE_LOW_KEY: str = "p25"
_PRICE_FAIR_KEY: str = "p50"
_PRICE_HIGH_KEY: str = "p75"


def _load_weights(listing_type: ListingType) -> dict[str, float]:
    """Load weights from YAML. Empty dict if not configured."""
    if not _CONFIG_PATH.exists():
        return {}
    raw = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    weights_map: dict[str, dict[str, float]] = raw.get("weights", {}) or {}
    return weights_map.get(listing_type.value, {}) or {}


def _renormalize(weights: dict[str, float]) -> dict[str, float]:
    """Renormalize so weights sum to 1.0 (or all-zero if input is all-zero)."""
    s = sum(weights.values())
    if s <= 0:
        return dict.fromkeys(weights, 0.0)
    return {k: v / s for k, v in weights.items()}


def _blend_distributions(
    parts: list[tuple[float, ValuationDistribution]],
) -> ValuationDistribution:
    """Weighted linear blend across distribution percentiles + moments."""
    if not parts:
        zero = Decimal("0")
        return ValuationDistribution(
            p10=zero, p25=zero, p50=zero, p75=zero, p90=zero, mean=zero, std=zero
        )

    def _w(field: str) -> Decimal:
        total = Decimal("0")
        for w, d in parts:
            total += Decimal(str(w)) * getattr(d, field)
        return total.quantize(Decimal("0.0001"))

    return ValuationDistribution(
        p10=_w("p10"),
        p25=_w("p25"),
        p50=_w("p50"),
        p75=_w("p75"),
        p90=_w("p90"),
        mean=_w("mean"),
        std=_w("std"),
    )


async def run_ensemble(
    extraction: ProspectusExtraction,
    market_data: MarketData,
    models: Iterable[ValuationModel],
    *,
    weights_override: dict[str, float] | None = None,
) -> ValuationEnsembleOutput:
    """Run all ``models`` in parallel, blend per listing_type weights, apply Regime Gate.

    Args:
        extraction: structured prospectus extraction.
        market_data: runtime market context (must carry ``listing_type``).
        models: any iterable of ``ValuationModel`` instances. Models that
                are not applicable to the listing type are skipped silently
                (they self-report ``applicable=False``).
        weights_override: optional dict to override YAML weights (for tests
                or What-If).

    Returns:
        ``ValuationEnsembleOutput`` with blended distribution + implied range
        + applied weights + notes (including Regime Gate truncation note if
        triggered).
    """
    results: list[SingleModelValuation] = await asyncio.gather(
        *(m.value(extraction, market_data) for m in models)
    )

    # Filter to applicable only.
    applicable = [r for r in results if r.applicable]
    notes: list[str] = []

    if not applicable:
        notes.append("no models applicable — ensemble empty")
        return _empty_output(extraction, results, notes)

    # Lookup weights.
    weights = weights_override or _load_weights(extraction.listing_type)
    if not weights:
        # Fallback: equal weight across applicable models.
        weights = {r.model_name: 1.0 / len(applicable) for r in applicable}
        notes.append("no weights configured; using equal weight across applicable models")

    # Restrict to applicable + renormalize.
    used = {r.model_name: weights.get(r.model_name, 0.0) for r in applicable}
    if sum(used.values()) <= 0:
        # No overlap between configured weights and applicable models → equal weight.
        used = {r.model_name: 1.0 / len(applicable) for r in applicable}
        notes.append("configured weights had no overlap with applicable models; using equal weight")
    used = _renormalize(used)

    # Blend.
    parts = [(used[r.model_name], r.valuation_distribution) for r in applicable]
    blended = _blend_distributions(parts)

    # Implied price range from blended distribution.
    price_range: dict[str, Decimal] = {
        "low": getattr(blended, _PRICE_LOW_KEY),
        "fair": getattr(blended, _PRICE_FAIR_KEY),
        "high": getattr(blended, _PRICE_HIGH_KEY),
    }

    # --- Regime Gate hard gate (ADR 0005 §2) ---
    if (
        market_data.regime_score is not None
        and market_data.regime_score < REGIME_GATE_THRESHOLD
    ):
        notes.append(
            f"Regime Gate triggered: regime_score={market_data.regime_score:.3f} < "
            f"{REGIME_GATE_THRESHOLD}; forcing SKIP — price range zeroed (ADR 0005 §2)"
        )
        zero = Decimal("0")
        price_range = {"low": zero, "fair": zero, "high": zero}
        # Keep the blended distribution intact for diagnostics but mark gate hit.

    return ValuationEnsembleOutput(
        company_id=extraction.prospectus_id,
        single_models=results,
        weights_used=used,
        ensemble_distribution=blended,
        implied_price_range=price_range,
        notes=notes,
    )


def _empty_output(
    extraction: ProspectusExtraction,
    results: list[SingleModelValuation],
    notes: list[str],
) -> ValuationEnsembleOutput:
    zero = Decimal("0")
    return ValuationEnsembleOutput(
        company_id=extraction.prospectus_id,
        single_models=results,
        weights_used={},
        ensemble_distribution=ValuationDistribution(
            p10=zero, p25=zero, p50=zero, p75=zero, p90=zero, mean=zero, std=zero
        ),
        implied_price_range={"low": zero, "fair": zero, "high": zero},
        notes=notes,
    )


__all__ = ("REGIME_GATE_THRESHOLD", "run_ensemble")


_ = Any  # silence unused-import in dataclass / typing future use
