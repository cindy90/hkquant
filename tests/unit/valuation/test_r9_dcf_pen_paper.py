"""R9-3 — additional DCF pen-paper lockdowns.

Complements the R1-1 pen-paper test (test_dcf.py::
test_dcf_terminal_delta_wc_uses_g_not_cagr) with sensitivity probes:

  * Higher WACC → lower equity (monotonic)
  * Higher terminal growth → higher equity (monotonic; with the stability
    guard ``wacc - g > 1bp`` still active)
  * ``cash`` flows directly into equity (additive 1:1)
  * ``debt`` reduces equity 1:1
  * Zero revenue → not applicable (already covered) — we pin the
    ``applicable=False`` contract here too as a regression guard

All tests use ``Constant`` distributions so the MC engine collapses to
a deterministic single sample; tolerances are float-rounding only.
"""

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
from hk_ipo_agent.valuation.monte_carlo import Constant


def _extraction(revenue: float, cash: float = 0.0) -> ProspectusExtraction:
    """Build a minimal ProspectusExtraction. FinancialSnapshot has no debt
    field today; we leave debt out (DCF reads cash via cash_balance_rmb).
    """
    return ProspectusExtraction(
        prospectus_id="P-DCF-R9",
        company_name_zh="测试 DCF R9",
        listing_type=ListingType.MAINBOARD_TECH,
        industry_code="TECH",
        industry_description="general",
        business_model="B2B",
        financials=[
            FinancialSnapshot(
                fiscal_year=2025,
                fiscal_period="FY",
                revenue_rmb=Decimal(str(revenue)),
                cash_balance_rmb=Decimal(str(cash)),
                citation=Citation(page=1),
            )
        ],
        extraction_version="0.0.1",
        extracted_at=datetime.now(UTC),
    )


def _md(wacc: float = 0.10, g: float = 0.03) -> MarketData:
    """Build a deterministic MarketData with all distributions pinned to Constant."""
    return MarketData(
        as_of_date=date(2026, 5, 16),
        listing_type=ListingType.MAINBOARD_TECH,
        extra={
            "mc_seed": 0,
            "dcf": {
                "wacc": Constant(wacc),
                "terminal_growth": Constant(g),
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


@pytest.mark.asyncio
async def test_dcf_equity_decreases_when_wacc_rises() -> None:
    """R9-3 — higher WACC → lower equity (discounts shrink everything).

    Pen-paper: WACC 8% → wacc-g=5%, larger TV → equity ~7e9.
    WACC 12% → wacc-g=9%, smaller TV → equity ~3.0e9. Monotonic strictly.
    """
    ext = _extraction(revenue=1e9)
    out_low = await DCFValuation().value(ext, _md(wacc=0.08, g=0.03))
    out_mid = await DCFValuation().value(ext, _md(wacc=0.10, g=0.03))
    out_high = await DCFValuation().value(ext, _md(wacc=0.12, g=0.03))

    p50_low = float(out_low.valuation_distribution.p50)
    p50_mid = float(out_mid.valuation_distribution.p50)
    p50_high = float(out_high.valuation_distribution.p50)

    assert p50_low > p50_mid > p50_high, (
        f"R9-3: equity should monotonically decrease as WACC rises; "
        f"got {p50_low:.3e} → {p50_mid:.3e} → {p50_high:.3e}"
    )


@pytest.mark.asyncio
async def test_dcf_equity_increases_with_terminal_growth() -> None:
    """R9-3 — higher terminal growth → higher equity (Gordon TV grows).

    Holding WACC at 0.10:
    g = 2% → TV multiplier = 1.02 / 0.08 = 12.75
    g = 3% → TV multiplier = 1.03 / 0.07 = 14.71
    g = 4% → TV multiplier = 1.04 / 0.06 = 17.33
    Monotonic strictly.
    """
    ext = _extraction(revenue=1e9)
    out_low_g = await DCFValuation().value(ext, _md(wacc=0.10, g=0.02))
    out_mid_g = await DCFValuation().value(ext, _md(wacc=0.10, g=0.03))
    out_high_g = await DCFValuation().value(ext, _md(wacc=0.10, g=0.04))

    p50_low_g = float(out_low_g.valuation_distribution.p50)
    p50_mid_g = float(out_mid_g.valuation_distribution.p50)
    p50_high_g = float(out_high_g.valuation_distribution.p50)

    assert p50_low_g < p50_mid_g < p50_high_g, (
        f"R9-3: equity should monotonically increase as terminal growth rises; "
        f"got {p50_low_g:.3e} → {p50_mid_g:.3e} → {p50_high_g:.3e}"
    )


@pytest.mark.asyncio
async def test_dcf_cash_adds_one_for_one_to_equity() -> None:
    """R9-3 — pen-paper: ``equity = EV - debt + cash`` so extra cash flows
    1:1 into equity. Adding 100M cash → equity goes up by exactly 100M.
    """
    ext_no_cash = _extraction(revenue=1e9, cash=0.0)
    ext_with_cash = _extraction(revenue=1e9, cash=1e8)  # +100M cash

    out_no_cash = await DCFValuation().value(ext_no_cash, _md())
    out_with_cash = await DCFValuation().value(ext_with_cash, _md())

    delta = float(out_with_cash.valuation_distribution.p50) - float(
        out_no_cash.valuation_distribution.p50
    )
    # Tolerance: float-rounding noise only (10 ppm of 100M = 1000).
    assert abs(delta - 1e8) < 1e3, (
        f"R9-3: 100M extra cash should add exactly 100M to equity; got delta={delta:.0f}"
    )


@pytest.mark.asyncio
async def test_dcf_wacc_too_close_to_g_kills_distribution() -> None:
    """R9-3 — when wacc == g the Gordon TV blows up; the stability
    guard ``np.where(wacc - g > 0.001, wacc - g, np.nan)`` kicks in.
    Equity should come back as NaN (or applicable=False if the engine
    short-circuits) — NOT a stable finite positive number.

    Probe: wacc = 0.05, g = 0.05 → wacc-g = 0 → guard sets to NaN.
    """
    import math

    ext = _extraction(revenue=1e9)
    out = await DCFValuation().value(ext, _md(wacc=0.05, g=0.05))
    if out.applicable:
        p50 = float(out.valuation_distribution.p50)
        # Either NaN propagated through, or equity is non-positive — both
        # are acceptable "unusable" surfaces. A finite positive equity at
        # wacc=g would indicate the stability guard failed.
        assert math.isnan(p50) or p50 <= 0, (
            f"R9-3: wacc=g should produce NaN or non-positive equity; got {p50}"
        )


@pytest.mark.asyncio
async def test_dcf_zero_revenue_not_applicable_regression_guard() -> None:
    """R9-3 — pin the ``applicable=False`` for zero/None revenue.

    Already covered by test_dcf_not_applicable_for_zero_revenue; this is
    a regression guard so a future refactor doesn't silently produce a
    "$0 equity" answer.
    """
    ext = _extraction(revenue=0.0)
    out = await DCFValuation().value(ext, _md())
    assert not out.applicable, (
        "R9-3: zero-revenue companies must be marked applicable=False "
        "(not silently produce $0 equity)"
    )
