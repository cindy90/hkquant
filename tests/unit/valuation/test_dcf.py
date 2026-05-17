"""Tests for ``DCFValuation`` (5y forecast + Gordon TV)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from hk_ipo_agent.common.enums import ListingType
from hk_ipo_agent.common.schemas import (
    Citation,
    FinancialSnapshot,
    ProspectusExtraction,
)
from hk_ipo_agent.valuation.base import MarketData
from hk_ipo_agent.valuation.dcf import DCFValuation
from hk_ipo_agent.valuation.monte_carlo import Constant, Triangular


def _extraction(
    revenue: float, cash: float = 0.0, lt: ListingType = ListingType.MAINBOARD_TECH
) -> ProspectusExtraction:
    return ProspectusExtraction(
        prospectus_id="P-DCF-1",
        company_name_zh="测试",
        listing_type=lt,
        industry_code="TECH",
        industry_description="general",
        business_model="B2B",
        financials=[
            FinancialSnapshot(
                fiscal_year=2025,
                fiscal_period="FY",
                revenue_rmb=Decimal(str(revenue)),
                cash_balance_rmb=Decimal(str(cash)),
                citation=Citation(page=10),
            )
        ],
        extraction_version="0.0.1",
        extracted_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_dcf_produces_positive_distribution_with_defaults() -> None:
    ext = _extraction(revenue=5e8, cash=1e8)
    md = MarketData(
        as_of_date=date(2026, 5, 16),
        listing_type=ListingType.MAINBOARD_TECH,
        extra={"mc_seed": 7},
    )
    out = await DCFValuation().value(ext, md)
    assert out.applicable
    assert float(out.valuation_distribution.p50) > 0
    assert float(out.valuation_distribution.p10) <= float(out.valuation_distribution.p90)
    assert out.key_assumptions["horizon_years"] == 5


@pytest.mark.asyncio
async def test_dcf_not_applicable_for_zero_revenue() -> None:
    ext = _extraction(revenue=0)
    md = MarketData(
        as_of_date=date(2026, 5, 16),
        listing_type=ListingType.MAINBOARD_TECH,
        extra={"mc_seed": 0},
    )
    out = await DCFValuation().value(ext, md)
    assert not out.applicable


@pytest.mark.asyncio
async def test_dcf_not_applicable_for_18a_biotech() -> None:
    ext = _extraction(revenue=1e9, lt=ListingType.CH18A_BIOTECH)
    md = MarketData(
        as_of_date=date(2026, 5, 16),
        listing_type=ListingType.CH18A_BIOTECH,
        extra={"mc_seed": 0},
    )
    out = await DCFValuation().value(ext, md)
    assert not out.applicable
    assert "unsupported" in out.key_assumptions["not_applicable_reason"]


@pytest.mark.asyncio
async def test_dcf_terminal_delta_wc_uses_g_not_cagr() -> None:
    """R1-1 — DCF 终值 ΔWC 系数 pen-paper lock.

    Per PROJECT_SPEC.md §3.7 + Gordon-TV steady-state semantics:
    terminal-year ΔWC scales with the terminal growth rate ``g``, not the
    explicit-forecast revenue CAGR. This test pins all distributions to
    constants and asserts the resulting equity value matches a hand
    calculation.

    Inputs (deterministic):
        base_revenue = 1e9, cash = 0, debt = 0
        cagr = 0.25, g = 0.03, wacc = 0.10
        ebitda_m = 0.20, term_m = 0.22, tax = 0.16
        wc_pct = 0.04, capex_pct = 0.06, da_pct = 0.04

    Hand calculation (5y explicit + Gordon TV):
        revenue_5  = 1e9 × 1.25^5             = 3.0517578125e9
        ebitda_t   = revenue_5 × 0.22         = 6.71386718e8
        nopat_t    = (ebitda_t − rev_5×0.04) × 0.84 = 4.61425781e8

        # FIXED terminal ΔWC uses g:
        ufcf_n     = nopat_t
                   + rev_5 × 0.04         = +1.22070312e8
                   − rev_5 × 0.06         = −1.83105468e8
                   − rev_5 × 0.04 × g     = −3.66210937e6    ← g, not cagr
                   = 3.96728515e8

        # BUGGY (pre-R1-1) used cagr=0.25 → ufcf_n = 3.69702148e8
        # Diff = revenue_5 × wc_pct × (cagr − g)
        #      = 3.05e9 × 0.04 × 0.22 = 2.6855e7
        # ⇒ buggy formula UNDER-estimates ufcf_n (and hence equity)
        # ⇒ this test FAILS before R1-1 fix is applied.

        tv         = ufcf_n × (1 + g) / (wacc − g)
                   = 3.96728515e8 × 1.03 / 0.07
                   = 5.83757672e9
        pv_tv      = tv / 1.10^5 = 3.62469523e9
        pv_explicit (sum of 5 discounted UFCFs) ≈ 7.9348e8
        equity     ≈ pv_explicit + pv_tv = ≈ 4.4182e9

    Tolerance: ±0.5% relative (MC noise is zero under Constant; only
    float-rounding remains).
    """
    ext = _extraction(revenue=1e9, cash=0.0)
    md = MarketData(
        as_of_date=date(2026, 5, 16),
        listing_type=ListingType.MAINBOARD_TECH,
        extra={
            "mc_seed": 0,
            "dcf": {
                "wacc": Constant(0.10),
                "terminal_growth": Constant(0.03),
                "revenue_cagr": Constant(0.25),
                "ebitda_margin": Constant(0.20),
                "terminal_margin": Constant(0.22),
                "tax_rate": Constant(0.16),
                "wc_pct_revenue": Constant(0.04),
                "capex_pct_revenue": Constant(0.06),
                "da_pct_revenue": Constant(0.04),
            },
        },
    )
    out = await DCFValuation().value(ext, md)
    assert out.applicable

    # All paths identical under Constant distributions → p10 == p50 == p90.
    p50 = float(out.valuation_distribution.p50)
    p10 = float(out.valuation_distribution.p10)
    p90 = float(out.valuation_distribution.p90)
    assert abs(p10 - p50) / p50 < 1e-9, "Constant distributions should yield zero spread"
    assert abs(p90 - p50) / p50 < 1e-9

    # Hand-computed equity value with FIXED terminal ΔWC (using g, not cagr).
    expected_equity = 4.4182e9
    assert abs(p50 - expected_equity) / expected_equity < 5e-3, (
        f"DCF equity value {p50:.4e} != hand-computed {expected_equity:.4e} "
        f"(rel err {abs(p50 - expected_equity) / expected_equity:.4%}). "
        f"This usually means the terminal ΔWC is still scaled by cagr (R1-1 not applied)."
    )

    # The fix must also record the basis in key_assumptions for audit.
    assert "delta_wc_terminal_basis" in out.key_assumptions, (
        "key_assumptions must record which growth rate scales terminal ΔWC (R1-1 audit trail)"
    )
    assert "g" in str(out.key_assumptions["delta_wc_terminal_basis"]).lower()


@pytest.mark.asyncio
async def test_dcf_overrides_via_market_data_extra() -> None:
    ext = _extraction(revenue=1e9)
    # Force extremely conservative WACC / CAGR → equity value should drop.
    md = MarketData(
        as_of_date=date(2026, 5, 16),
        listing_type=ListingType.MAINBOARD_TECH,
        extra={
            "mc_seed": 0,
            "dcf": {
                "wacc": Triangular(0.18, 0.20, 0.22),
                "revenue_cagr": Triangular(0.0, 0.02, 0.04),
            },
        },
    )
    out = await DCFValuation().value(ext, md)
    assert out.applicable
    # Sanity check: with high wacc + low growth, equity value should be much
    # smaller than the base case.
    assert float(out.valuation_distribution.p50) < 5e10
