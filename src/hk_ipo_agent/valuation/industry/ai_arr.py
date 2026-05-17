"""AI / SaaS ARR multiple valuation.

Per PROJECT_SPEC.md §3.7. Industry specialization for AI-as-a-service /
SaaS where ARR (annual recurring revenue) is the natural KPI rather than
TTM revenue. Default ARR multiple band reflects 2024-2026 HK observed
range: 3-15x with median ~7x (LogNormal centred there).

Drives off ``extraction.financials[-1].revenue_rmb`` as the ARR proxy
(IPO prospectuses rarely break out pure ARR for new listings; the
Fundamental agent can override ``market_data.extra["ai_arr"]["arr_rmb"]``
when a cleaner ARR figure is in the extraction).
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import ClassVar

import numpy as np

from ...common.enums import ListingType
from ...common.schemas import (
    ProspectusExtraction,
    SingleModelValuation,
)
from ..base import (
    MarketData,
    ValuationModel,
    _citation_from_extraction,
    distribution_from_samples,
)
from ..monte_carlo import LogNormal, run_mc

# Industry codes (loose match against extraction.industry_code) that
# trigger AI/SaaS ARR specialization. The Fundamental agent normalizes
# industry_code upstream; matching is case-insensitive substring.
AI_INDUSTRY_KEYWORDS: tuple[str, ...] = ("AI", "SaaS", "软件", "人工智能", "云", "Cloud")


class AIARRValuation(ValuationModel):
    """ARR * Multiple, multiple ~ LogNormal(median=7x, sigma=0.4)."""

    model_name = "industry"  # registered as the generic "industry" slot
    applicable_types: ClassVar[list[ListingType]] = [
        ListingType.CH18C_COMMERCIALIZED,
        ListingType.CH18C_PRE_COMMERCIAL,
        ListingType.MAINBOARD_TECH,
    ]

    def _matches_industry(self, extraction: ProspectusExtraction) -> bool:
        ind = (extraction.industry_code or "") + " " + (extraction.industry_description or "")
        for k in AI_INDUSTRY_KEYWORDS:
            # ASCII keywords use word-boundary matching to avoid substrings
            # like "AI" matching "retail"; CJK keywords use substring match
            # since Chinese has no word boundaries.
            if k.isascii():
                if re.search(rf"\b{re.escape(k)}\b", ind, flags=re.IGNORECASE):
                    return True
            elif k in ind:
                return True
        return False

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

        if not self._matches_industry(extraction):
            return self._not_applicable(
                reason=f"industry {extraction.industry_code!r} not AI/SaaS",
                model_name=self.model_name,
            )

        overrides = (market_data.extra or {}).get("ai_arr", {}) or {}
        arr_rmb = overrides.get("arr_rmb")
        if arr_rmb is None:
            if not extraction.financials:
                return self._not_applicable(
                    reason="no ARR override and no financials",
                    model_name=self.model_name,
                )
            arr_rmb = float(extraction.financials[-1].revenue_rmb or 0.0)
        if arr_rmb <= 0:
            return self._not_applicable(
                reason="non-positive ARR",
                model_name=self.model_name,
            )

        # LogNormal centred at multiple_median.
        median_mult: float = overrides.get("median_multiple", 7.0)
        sigma: float = overrides.get("sigma", 0.4)
        dist = LogNormal(mu=float(np.log(median_mult)), sigma=sigma)

        def payoff(s: dict[str, np.ndarray]) -> np.ndarray:
            return arr_rmb * s["multiple"]

        samples = run_mc({"multiple": dist}, payoff, seed=market_data.extra.get("mc_seed"))
        valuation_dist = distribution_from_samples(samples)

        key_assumptions = {
            "arr_rmb": arr_rmb,
            "multiple_median": median_mult,
            "multiple_sigma": sigma,
            "industry_code": extraction.industry_code,
        }

        return SingleModelValuation(
            model_name=self.model_name,
            applicable=True,
            valuation_distribution=valuation_dist,
            key_assumptions=key_assumptions,
            citations=_citation_from_extraction(extraction),
        )


__all__ = ("AI_INDUSTRY_KEYWORDS", "AIARRValuation")


_ = Decimal  # type re-export marker
