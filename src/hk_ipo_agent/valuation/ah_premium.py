"""A+H premium model — only applicable to AH dual-listings.

Per PROJECT_SPEC.md §3.7. Algorithm:
1. Take ``a_share_price_at_filing`` (RMB) from extraction.
2. Sample empirically from ``market_data.ah_premium_history_pct``.
3. ``H_price = A_price * (1 - premium_pct)``.

**Convention** (Hang Seng China AH Premium Index): ``premium_pct = (A - H) / A``.
Positive values mean H trades at a discount to A (the typical regime).
Negative values mean H trades at a premium to A.

If ``ah_premium_history_pct`` is empty, fall back to industry baseline
``Triangular(low=0.15, mode=0.30, high=0.40)`` reflecting the common
H-discount-vs-A range observed 2020-2026.

Phase 8 upgrade path (see ADR 0008 Neutral section):
    Spec §3.7 requires a multi-factor regression with at least 6 factors
    (Beta差 / 流通市值差 / 流动性差 / 股息率 / 行业 / AH溢价指数当时点位).
    Phase 4 uses empirical sampling because AH new-listing sample <30 makes
    regression prone to overfitting. Phase 8 calibration will upgrade to the
    full regression once sample size >= 50 and iFind AH premium index data
    is available via ``data/sources/ifind_client.py``.
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
from .monte_carlo import Distribution, FromArray, Triangular, run_mc

_FALLBACK_PREMIUM: Distribution = Triangular(low=0.15, mode=0.30, high=0.40)


class AHPremiumValuation(ValuationModel):
    """For AH duals: ``H_value = A_value * (1 - H_premium)``."""

    model_name = "ah_premium"
    applicable_types: ClassVar[list[ListingType]] = [ListingType.AH_DUAL]

    async def value(
        self,
        extraction: ProspectusExtraction,
        market_data: MarketData,
    ) -> SingleModelValuation:
        if not self.applies_to(extraction.listing_type):
            return self._not_applicable(
                reason="not an AH dual listing",
                model_name=self.model_name,
            )

        a_price = extraction.a_share_price_at_filing or market_data.a_share_price_at_filing
        if a_price is None or a_price <= 0:
            return self._not_applicable(
                reason="no A-share reference price",
                model_name=self.model_name,
            )

        # Choose distribution: empirical history if available, else fallback.
        history = np.asarray(market_data.ah_premium_history_pct, dtype=np.float64)
        history = history[np.isfinite(history)]
        if history.size > 0:
            dist: Distribution = FromArray(values=history)
            premium_source = "empirical_history"
        else:
            dist = _FALLBACK_PREMIUM
            premium_source = "industry_fallback"

        a_price_f = float(a_price)
        # H equity value in RMB = A_price * (1 - premium); share count cancels in ensemble.
        # We model per-share equivalent here; ensemble compares apples-to-apples on per-share.
        def payoff(s: dict[str, np.ndarray]) -> np.ndarray:
            return a_price_f * (1.0 - s["premium"])

        samples = run_mc(
            {"premium": dist},
            payoff,
            seed=market_data.extra.get("mc_seed"),
        )
        valuation_dist = distribution_from_samples(samples)

        key_assumptions = {
            "a_share_price_rmb": a_price_f,
            "premium_source": premium_source,
            "history_sample_size": int(history.size),
            "fx_rmb_to_hkd": market_data.fx_rmb_to_hkd,
        }

        return SingleModelValuation(
            model_name=self.model_name,
            applicable=True,
            valuation_distribution=valuation_dist,
            key_assumptions=key_assumptions,
            citations=_citation_from_extraction(extraction),
        )


__all__ = ("AHPremiumValuation",)


_ = Decimal  # type re-export marker
