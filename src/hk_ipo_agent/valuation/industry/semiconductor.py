"""Semiconductor industry specialization — EV/Sales with cycle adjustment.

Per PROJECT_SPEC.md §3.7. Semis show strong cyclicality; reasonable
proxy is EV/Sales TTM with industry-typical range 2-12x, median ~5x
(LogNormal-distributed). Cycle phase can be passed via
``market_data.extra["semiconductor"]["cycle_phase"]`` in ``{"trough", "mid", "peak"}``
to shift the median multiple ±30%.
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

SEMI_INDUSTRY_KEYWORDS: tuple[str, ...] = (
    "Semiconductor",
    "半导体",
    "芯片",
    "IC Design",
    "晶圆",
    "集成电路",
)

_CYCLE_MULTIPLIER: dict[str, float] = {
    "trough": 0.70,
    "mid": 1.00,
    "peak": 1.30,
}


class SemiconductorValuation(ValuationModel):
    """EV/Sales valuation with cycle-phase-adjusted median multiple."""

    model_name = "industry"
    applicable_types: ClassVar[list[ListingType]] = [
        ListingType.CH18C_COMMERCIALIZED,
        ListingType.MAINBOARD_TECH,
        ListingType.AH_DUAL,
        ListingType.MAINBOARD_OTHER,
    ]

    def _matches_industry(self, extraction: ProspectusExtraction) -> bool:
        ind = (extraction.industry_code or "") + " " + (
            extraction.industry_description or ""
        )
        for k in SEMI_INDUSTRY_KEYWORDS:
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
                reason=f"industry {extraction.industry_code!r} not semiconductor",
                model_name=self.model_name,
            )

        if not extraction.financials:
            return self._not_applicable(
                reason="no financial snapshot",
                model_name=self.model_name,
            )

        revenue = float(extraction.financials[-1].revenue_rmb or 0.0)
        if revenue <= 0:
            return self._not_applicable(
                reason="non-positive revenue",
                model_name=self.model_name,
            )

        overrides = (market_data.extra or {}).get("semiconductor", {}) or {}
        cycle_phase: str = overrides.get("cycle_phase", "mid")
        cycle_mult = _CYCLE_MULTIPLIER.get(cycle_phase, 1.0)
        base_median: float = overrides.get("median_multiple", 5.0)
        median = base_median * cycle_mult
        sigma: float = overrides.get("sigma", 0.5)

        dist = LogNormal(mu=float(np.log(median)), sigma=sigma)

        cash = float(extraction.financials[-1].cash_balance_rmb or 0.0)

        def payoff(s: dict[str, np.ndarray]) -> np.ndarray:
            ev = revenue * s["multiple"]
            return ev + cash  # EV -> Equity bridge (debt assumed 0)

        samples = run_mc({"multiple": dist}, payoff, seed=market_data.extra.get("mc_seed"))
        valuation_dist = distribution_from_samples(samples)

        key_assumptions = {
            "revenue_rmb": revenue,
            "cycle_phase": cycle_phase,
            "cycle_multiplier": cycle_mult,
            "median_multiple_adjusted": median,
            "sigma": sigma,
            "cash_balance_rmb": cash,
        }

        return SingleModelValuation(
            model_name=self.model_name,
            applicable=True,
            valuation_distribution=valuation_dist,
            key_assumptions=key_assumptions,
            citations=_citation_from_extraction(extraction),
        )


__all__ = ("SEMI_INDUSTRY_KEYWORDS", "SemiconductorValuation")


_ = Decimal  # type re-export marker
