"""Pre-IPO last-round valuation anchor + discount distribution.

Per PROJECT_SPEC.md §3.7. Anchor model: take the last private-round
valuation as a base, then apply a triangular discount distribution
(negative = premium, positive = discount) to reflect IPO-vs-private gap.

Defaults reflect HK 2024-2026 observed patterns:
- p10  : -20%  (premium to last round — bull case for hot 18C names)
- mode : +10%  (small discount — market norm)
- p90  : +50%  (heavy discount — frozen primary market / down-round)

Override via ``market_data.extra["pre_ipo_anchor"]["discount"]``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import ClassVar

import numpy as np

from ..common.enums import ListingType
from ..common.schemas import (
    ProspectusExtraction,
    SingleModelValuation,
)
from .base import (
    MarketData,
    ValuationModel,
    _citation_from_extraction,
    distribution_from_samples,
)
from .monte_carlo import Distribution, Triangular, run_mc

_DEFAULT_DISCOUNT: Distribution = Triangular(low=-0.20, mode=0.10, high=0.50)


class PreIPOAnchorValuation(ValuationModel):
    """Anchor = last_round_valuation * (1 - discount); discount ~ Triangular."""

    model_name = "pre_ipo_anchor"
    applicable_types: ClassVar[list[ListingType]] = [
        ListingType.CH18C_COMMERCIALIZED,
        ListingType.CH18C_PRE_COMMERCIAL,
        ListingType.CH18A_BIOTECH,
        ListingType.MAINBOARD_TECH,
        ListingType.AH_DUAL,
        ListingType.MAINBOARD_OTHER,
    ]

    async def value(
        self,
        extraction: ProspectusExtraction,
        market_data: MarketData,
    ) -> SingleModelValuation:
        if not self.applies_to(extraction.listing_type):
            return self._not_applicable(
                reason=f"listing_type {extraction.listing_type} unsupported",
                model_name=self.model_name,
            )

        # Prefer extraction-level field, fall back to MarketData.
        anchor_rmb = extraction.pre_ipo_valuation_rmb or market_data.last_round_valuation_rmb
        if anchor_rmb is None or anchor_rmb <= 0:
            return self._not_applicable(
                reason="no pre-IPO last-round valuation available",
                model_name=self.model_name,
            )

        anchor = float(anchor_rmb)
        overrides = (market_data.extra or {}).get("pre_ipo_anchor", {}) or {}
        discount_dist: Distribution = overrides.get("discount", _DEFAULT_DISCOUNT)

        def payoff(s: dict[str, np.ndarray]) -> np.ndarray:
            return anchor * (1.0 - s["discount"])

        samples = run_mc(
            {"discount": discount_dist},
            payoff,
            seed=market_data.extra.get("mc_seed"),
        )
        dist = distribution_from_samples(samples)

        key_assumptions = {
            "anchor_valuation_rmb": anchor,
            "discount_dist": {
                "type": discount_dist.__class__.__name__,
                **{
                    f: getattr(discount_dist, f)
                    for f in discount_dist.__dataclass_fields__  # type: ignore[attr-defined]
                },
            },
            "last_round_date": str(extraction.last_round_date or market_data.last_round_date),
        }

        return SingleModelValuation(
            model_name=self.model_name,
            applicable=True,
            valuation_distribution=dist,
            key_assumptions=key_assumptions,
            citations=_citation_from_extraction(extraction),
        )


__all__ = ("PreIPOAnchorValuation",)


_ = Decimal  # type re-export marker
