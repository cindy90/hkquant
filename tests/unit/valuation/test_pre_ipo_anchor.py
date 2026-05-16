"""Tests for ``PreIPOAnchorValuation``."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from hk_ipo_agent.common.enums import ListingType
from hk_ipo_agent.common.schemas import ProspectusExtraction
from hk_ipo_agent.valuation.base import MarketData
from hk_ipo_agent.valuation.monte_carlo import Triangular
from hk_ipo_agent.valuation.pre_ipo_anchor import PreIPOAnchorValuation


def _ext(
    anchor: Decimal | None = None,
    lt: ListingType = ListingType.MAINBOARD_TECH,
) -> ProspectusExtraction:
    return ProspectusExtraction(
        prospectus_id="P-ANC-1",
        company_name_zh="测试",
        listing_type=lt,
        industry_code="TECH",
        industry_description="general",
        business_model="B2B",
        pre_ipo_valuation_rmb=anchor,
        extraction_version="0.0.1",
        extracted_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_anchor_from_extraction() -> None:
    ext = _ext(anchor=Decimal("1000000000"))
    md = MarketData(
        as_of_date=date(2026, 5, 16),
        listing_type=ListingType.MAINBOARD_TECH,
        extra={"mc_seed": 1},
    )
    out = await PreIPOAnchorValuation().value(ext, md)
    assert out.applicable
    # Default discount Triangular(-0.20, 0.10, 0.50) → mode = 1e9 * (1 - 0.10) = 9e8.
    # Mean of triangular ≈ 0.133; expected value ≈ 1e9 * (1 - 0.133) ≈ 8.67e8.
    assert 6e8 < float(out.valuation_distribution.p50) < 1.2e9


@pytest.mark.asyncio
async def test_anchor_falls_back_to_market_data() -> None:
    ext = _ext(anchor=None)
    md = MarketData(
        as_of_date=date(2026, 5, 16),
        listing_type=ListingType.MAINBOARD_TECH,
        last_round_valuation_rmb=Decimal("5e8"),
        extra={"mc_seed": 1},
    )
    out = await PreIPOAnchorValuation().value(ext, md)
    assert out.applicable
    assert float(out.key_assumptions["anchor_valuation_rmb"]) == 5e8


@pytest.mark.asyncio
async def test_anchor_no_data_not_applicable() -> None:
    ext = _ext(anchor=None)
    md = MarketData(
        as_of_date=date(2026, 5, 16),
        listing_type=ListingType.MAINBOARD_TECH,
        extra={"mc_seed": 0},
    )
    out = await PreIPOAnchorValuation().value(ext, md)
    assert not out.applicable


@pytest.mark.asyncio
async def test_anchor_override_discount_distribution() -> None:
    ext = _ext(anchor=Decimal("1e9"))
    # Force heavy discount (40%-60%) → P50 ≈ 5e8.
    md = MarketData(
        as_of_date=date(2026, 5, 16),
        listing_type=ListingType.MAINBOARD_TECH,
        extra={
            "mc_seed": 1,
            "pre_ipo_anchor": {"discount": Triangular(0.4, 0.5, 0.6)},
        },
    )
    out = await PreIPOAnchorValuation().value(ext, md)
    assert out.applicable
    assert 4e8 < float(out.valuation_distribution.p50) < 6e8
