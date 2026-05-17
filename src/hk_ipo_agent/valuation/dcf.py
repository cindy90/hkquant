"""DCF valuation — 5y explicit forecast + Gordon terminal value.

Per PROJECT_SPEC.md §3.7. Algorithm reference:
- WACC + UFCF + Gordon TV + EV->Equity bridge follows
  ``D:/自定义工具/投资建议书agent/DCF agent`` ``references/session-f.md`` L120-200
  (UFCF = EBITDA*(1-tax) + DA - CapEx - ΔWC, TV = UFCF_n*(1+g)/(WACC-g)).

The model is driven by a small set of distributions; defaults are reasonable
for a HK tech IPO and can be overridden by passing ``MarketData.extra["dcf"]``
with any of the keys in ``_DEFAULT_DISTRIBUTIONS``.
"""

from __future__ import annotations

from typing import Any, ClassVar

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
from .monte_carlo import (
    Distribution,
    Normal,
    Triangular,
    Uniform,
    run_mc,
)

# Forecast horizon (years) per spec §3.7 typical IPO DCF.
_HORIZON: int = 5

# Default distributions; override via MarketData.extra["dcf"].
_DEFAULT_DISTRIBUTIONS: dict[str, Distribution] = {
    "wacc": Triangular(low=0.09, mode=0.11, high=0.13),
    "terminal_growth": Triangular(low=0.02, mode=0.03, high=0.04),
    "revenue_cagr": Triangular(low=0.15, mode=0.25, high=0.35),
    "ebitda_margin": Triangular(low=0.10, mode=0.18, high=0.25),
    "terminal_margin": Triangular(low=0.15, mode=0.22, high=0.28),
    "tax_rate": Normal(mean=0.16, std=0.02),
    "wc_pct_revenue": Uniform(low=0.02, high=0.06),
    "capex_pct_revenue": Uniform(low=0.03, high=0.08),
    "da_pct_revenue": Uniform(low=0.02, high=0.05),
}


class DCFValuation(ValuationModel):
    """5y DCF with Gordon TV; outputs equity value distribution in **RMB**."""

    model_name = "dcf"
    applicable_types: ClassVar[list[ListingType]] = [
        ListingType.CH18C_COMMERCIALIZED,
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
                reason=f"listing_type {extraction.listing_type} unsupported (need positive earnings track)",
                model_name=self.model_name,
            )

        if not extraction.financials:
            return self._not_applicable(
                reason="no financial snapshot in extraction",
                model_name=self.model_name,
            )

        latest = extraction.financials[-1]
        base_revenue = float(latest.revenue_rmb or 0.0)
        if base_revenue <= 0.0:
            return self._not_applicable(
                reason="non-positive base-year revenue",
                model_name=self.model_name,
            )

        cash = float(latest.cash_balance_rmb or 0.0)
        # No debt info in current extraction schema; treat as zero (conservative).
        debt = 0.0

        overrides: dict[str, Distribution] = (market_data.extra or {}).get("dcf", {}) or {}
        assumptions = {**_DEFAULT_DISTRIBUTIONS, **overrides}

        def payoff(s: dict[str, np.ndarray]) -> np.ndarray:
            wacc = s["wacc"]
            g = s["terminal_growth"]
            cagr = s["revenue_cagr"]
            ebitda_m = s["ebitda_margin"]
            term_m = s["terminal_margin"]
            tax = np.clip(s["tax_rate"], 0.0, 0.35)
            wc_pct = s["wc_pct_revenue"]
            capex_pct = s["capex_pct_revenue"]
            da_pct = s["da_pct_revenue"]

            # Guard wacc - g > 1bp; otherwise PV blows up.
            wacc_minus_g = np.where(wacc - g > 0.001, wacc - g, np.nan)

            # 5y revenue / UFCF projection (Source: DCF agent session-f.md L165)
            pv_explicit = np.zeros_like(wacc)
            revenue_t = np.full_like(wacc, base_revenue)
            ufcf_n = np.zeros_like(wacc)
            for t in range(1, _HORIZON + 1):
                revenue_t = revenue_t * (1.0 + cagr)
                ebitda_t = revenue_t * ebitda_m
                da_t = revenue_t * da_pct
                ebit_t = ebitda_t - da_t
                nopat_t = ebit_t * (1.0 - tax)
                capex_t = revenue_t * capex_pct
                delta_wc_t = revenue_t * wc_pct - (
                    revenue_t / (1.0 + cagr) * wc_pct if t > 1 else base_revenue * wc_pct
                )
                ufcf_t = nopat_t + da_t - capex_t - delta_wc_t
                pv_explicit = pv_explicit + ufcf_t / np.power(1.0 + wacc, t)
                if t == _HORIZON:
                    # Terminal year normalized using term_m (steady-state margin).
                    ebitda_term = revenue_t * term_m
                    nopat_term = (ebitda_term - revenue_t * da_pct) * (1.0 - tax)
                    ufcf_n = (
                        nopat_term
                        + revenue_t * da_pct
                        - revenue_t * capex_pct
                        - revenue_t * wc_pct * cagr  # delta-WC at terminal growth rate
                    )

            # Gordon TV; discount back HORIZON years.
            tv = ufcf_n * (1.0 + g) / wacc_minus_g
            pv_tv = tv / np.power(1.0 + wacc, _HORIZON)

            enterprise_value = pv_explicit + pv_tv
            return enterprise_value - debt + cash  # EV -> Equity bridge

        samples = run_mc(assumptions, payoff, seed=market_data.extra.get("mc_seed"))
        dist = distribution_from_samples(samples)

        key_assumptions = {
            "base_revenue_rmb": base_revenue,
            "horizon_years": _HORIZON,
            "wacc_dist": _describe(assumptions["wacc"]),
            "terminal_growth_dist": _describe(assumptions["terminal_growth"]),
            "revenue_cagr_dist": _describe(assumptions["revenue_cagr"]),
            "terminal_margin_dist": _describe(assumptions["terminal_margin"]),
            "tax_rate_dist": _describe(assumptions["tax_rate"]),
            "cash_balance_rmb": cash,
            "valid_path_count": int(np.isfinite(samples).sum()),
        }

        return SingleModelValuation(
            model_name=self.model_name,
            applicable=True,
            valuation_distribution=dist,
            key_assumptions=key_assumptions,
            citations=_citation_from_extraction(extraction),
        )


def _describe(d: Distribution) -> dict[str, Any]:
    """Compact dict representation of a distribution for key_assumptions."""
    cls = d.__class__.__name__
    fields = {
        f: getattr(d, f)
        for f in d.__dataclass_fields__  # type: ignore[attr-defined]
    }
    return {"type": cls, **fields}


__all__ = ("DCFValuation",)


# Source: DCF agent references/session-f.md L120-200 (UFCF + Gordon TV + EV/Equity bridge).
