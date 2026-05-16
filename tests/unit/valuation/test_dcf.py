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
from hk_ipo_agent.valuation.monte_carlo import Triangular


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
