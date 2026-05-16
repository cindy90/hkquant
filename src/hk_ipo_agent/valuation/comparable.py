"""Comparable companies valuation — PS / PE percentile (with outlier filter).

Per PROJECT_SPEC.md §3.7. Algorithm reference:
- Peer multiple percentile selection + EV->Equity bridge follows the
  pattern used in `D:/自定义工具/投资建议书agent/DCF agent` ``references/session-h.md``
  Block 1-3 (PE / PS / EV/EBITDA percentile picks at p25 / median / p75 ;
  outlier filter ``0 < m < 200``).

Inputs from ``ProspectusExtraction``:
- ``financials[-1].revenue_rmb`` (TTM revenue proxy)
- ``financials[-1].net_profit_rmb`` (TTM net profit; may be negative —
  PE valuation skipped if so)

Inputs from ``MarketData.peer_multiples``:
- ``ps_ttm`` list of peer P/S
- ``pe_ttm`` list of peer P/E

Output: ``SingleModelValuation`` whose distribution is in **RMB**.
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
from .monte_carlo import DEFAULT_PATHS, Distribution, FromArray, run_mc

# Outlier guard for raw peer multiples (DCF agent session-h.md convention).
_MULTIPLE_LOW: float = 0.0
_MULTIPLE_HIGH: float = 200.0


def _clean_multiples(arr: list[float]) -> np.ndarray:
    """Drop NaN, non-positive, and >200 outliers."""
    a = np.asarray(arr, dtype=np.float64)
    a = a[np.isfinite(a)]
    return a[(a > _MULTIPLE_LOW) & (a < _MULTIPLE_HIGH)]


class ComparableValuation(ValuationModel):
    """PS-primary, PE-blend-when-profitable comparable model.

    Applicable to every listing type that has peer multiples.
    """

    model_name = "comparable"
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

        if market_data.peer_multiples is None or not market_data.peer_multiples.has_data:
            return self._not_applicable(
                reason="no peer multiples available",
                model_name=self.model_name,
            )

        if not extraction.financials:
            return self._not_applicable(
                reason="no financial snapshot in extraction",
                model_name=self.model_name,
            )

        latest = extraction.financials[-1]
        revenue = float(latest.revenue_rmb or 0.0)
        net_profit = float(latest.net_profit_rmb or 0.0)

        if revenue <= 0.0:
            return self._not_applicable(
                reason="non-positive revenue",
                model_name=self.model_name,
            )

        ps_clean = _clean_multiples(market_data.peer_multiples.ps_ttm)
        pe_clean = _clean_multiples(market_data.peer_multiples.pe_ttm)

        if ps_clean.size == 0 and pe_clean.size == 0:
            return self._not_applicable(
                reason="peer multiples all filtered out as outliers",
                model_name=self.model_name,
            )

        # MC: sample peer multiples empirically; PS always, PE only if profitable.
        assumptions: dict[str, Distribution] = {}
        if ps_clean.size > 0:
            assumptions["ps_multiple"] = FromArray(values=ps_clean)
        if pe_clean.size > 0 and net_profit > 0.0:
            assumptions["pe_multiple"] = FromArray(values=pe_clean)

        def payoff(samples: dict[str, np.ndarray]) -> np.ndarray:
            results: list[np.ndarray] = []
            if "ps_multiple" in samples:
                results.append(samples["ps_multiple"] * revenue)
            if "pe_multiple" in samples:
                results.append(samples["pe_multiple"] * net_profit)
            if not results:
                return np.zeros(DEFAULT_PATHS, dtype=np.float64)
            # 50/50 blend when both available, otherwise straight pass-through.
            return np.mean(np.stack(results, axis=0), axis=0)  # type: ignore[no-any-return]

        samples = run_mc(assumptions, payoff, seed=market_data.extra.get("mc_seed"))

        # Liquidity discount (spec §3.7: 跨市场可比带流动性折价调整).
        # Phase 8 calibration will inject an empirical value from bid-ask spread /
        # turnover ratio / free-float ratio. Until then defaults to 0.0 (no discount).
        liquidity_discount: float = float(market_data.extra.get("liquidity_discount", 0.0))
        if liquidity_discount > 0.0:
            samples = samples * (1.0 - liquidity_discount)

        dist = distribution_from_samples(samples)

        key_assumptions = {
            "ps_sample_size": int(ps_clean.size),
            "pe_sample_size": int(pe_clean.size) if net_profit > 0.0 else 0,
            "revenue_rmb": revenue,
            "net_profit_rmb": net_profit if net_profit > 0.0 else None,
            "ps_p50": float(np.percentile(ps_clean, 50)) if ps_clean.size > 0 else None,
            "pe_p50": float(np.percentile(pe_clean, 50))
            if (pe_clean.size > 0 and net_profit > 0.0)
            else None,
            "outlier_filter": f"{_MULTIPLE_LOW} < m < {_MULTIPLE_HIGH}",
            "liquidity_discount": liquidity_discount,
        }

        return SingleModelValuation(
            model_name=self.model_name,
            applicable=True,
            valuation_distribution=dist,
            key_assumptions=key_assumptions,
            citations=_citation_from_extraction(extraction),
        )


__all__ = ("ComparableValuation",)


# Source: DCF agent references/session-h.md Block 2 (PE outlier filter `0 < m < 200`),
#         Block 1 (PS percentile selection), Block 3 (EV->Equity bridge — applied
#         only where Decimal cash/debt are available; omitted here for the
#         pre-listing PS/PE first-cut where balance-sheet detail is absent).
_ = Decimal  # type re-export marker
