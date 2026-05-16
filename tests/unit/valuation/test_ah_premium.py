"""Tests for ``AHPremiumValuation``."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from hk_ipo_agent.common.enums import ListingType
from hk_ipo_agent.common.schemas import ProspectusExtraction
from hk_ipo_agent.valuation.ah_premium import AHPremiumValuation
from hk_ipo_agent.valuation.base import MarketData


def _ext_ah(a_price: Decimal | None = Decimal("50.0")) -> ProspectusExtraction:
    return ProspectusExtraction(
        prospectus_id="P-AH-1",
        company_name_zh="测试AH",
        listing_type=ListingType.AH_DUAL,
        industry_code="FIN",
        industry_description="banking",
        business_model="commercial bank",
        a_share_code="600000",
        a_share_price_at_filing=a_price,
        extraction_version="0.0.1",
        extracted_at=datetime.now(UTC),
    )


def _ext_non_ah() -> ProspectusExtraction:
    return ProspectusExtraction(
        prospectus_id="P-NONAH-1",
        company_name_zh="测试",
        listing_type=ListingType.MAINBOARD_TECH,
        industry_code="TECH",
        industry_description="x",
        business_model="x",
        extraction_version="0.0.1",
        extracted_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_ah_premium_uses_empirical_history() -> None:
    ext = _ext_ah(a_price=Decimal("100"))
    md = MarketData(
        as_of_date=date(2026, 5, 16),
        listing_type=ListingType.AH_DUAL,
        ah_premium_history_pct=[0.30, 0.25, 0.35],  # A trades 25-35% above H (H discount vs A)
        extra={"mc_seed": 0},
    )
    out = await AHPremiumValuation().value(ext, md)
    assert out.applicable
    # 100 * (1 - 0.30) = 70 typical
    assert 60 < float(out.valuation_distribution.p50) < 80
    assert out.key_assumptions["premium_source"] == "empirical_history"


@pytest.mark.asyncio
async def test_ah_premium_fallback_when_no_history() -> None:
    ext = _ext_ah(a_price=Decimal("100"))
    md = MarketData(
        as_of_date=date(2026, 5, 16),
        listing_type=ListingType.AH_DUAL,
        ah_premium_history_pct=[],
        extra={"mc_seed": 0},
    )
    out = await AHPremiumValuation().value(ext, md)
    assert out.applicable
    assert out.key_assumptions["premium_source"] == "industry_fallback"


@pytest.mark.asyncio
async def test_ah_premium_not_applicable_for_non_ah() -> None:
    ext = _ext_non_ah()
    md = MarketData(
        as_of_date=date(2026, 5, 16),
        listing_type=ListingType.MAINBOARD_TECH,
        extra={"mc_seed": 0},
    )
    out = await AHPremiumValuation().value(ext, md)
    assert not out.applicable


@pytest.mark.asyncio
async def test_ah_premium_not_applicable_without_a_price() -> None:
    ext = _ext_ah(a_price=None)
    md = MarketData(
        as_of_date=date(2026, 5, 16),
        listing_type=ListingType.AH_DUAL,
        extra={"mc_seed": 0},
    )
    out = await AHPremiumValuation().value(ext, md)
    assert not out.applicable
