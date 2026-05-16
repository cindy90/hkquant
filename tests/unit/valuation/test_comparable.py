"""Tests for ``ComparableValuation`` (PS-primary, PE-blend)."""

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
from hk_ipo_agent.valuation.base import MarketData, PeerMultiples
from hk_ipo_agent.valuation.comparable import ComparableValuation


def _extraction_with_financials(
    revenue: float,
    net_profit: float,
    listing_type: ListingType = ListingType.MAINBOARD_TECH,
) -> ProspectusExtraction:
    return ProspectusExtraction(
        prospectus_id="P-CMP-1",
        company_name_zh="测试",
        listing_type=listing_type,
        industry_code="TECH",
        industry_description="AI / SaaS",
        business_model="B2B SaaS",
        financials=[
            FinancialSnapshot(
                fiscal_year=2025,
                fiscal_period="FY",
                revenue_rmb=Decimal(str(revenue)),
                net_profit_rmb=Decimal(str(net_profit)),
                citation=Citation(page=42),
            )
        ],
        extraction_version="0.0.1",
        extracted_at=datetime.now(UTC),
    )


def _md_with_peers(
    ps_ttm: list[float] | None = None,
    pe_ttm: list[float] | None = None,
) -> MarketData:
    return MarketData(
        as_of_date=date(2026, 5, 16),
        listing_type=ListingType.MAINBOARD_TECH,
        peer_multiples=PeerMultiples(
            ps_ttm=ps_ttm or [],
            pe_ttm=pe_ttm or [],
            sample_size=max(len(ps_ttm or []), len(pe_ttm or [])),
        ),
        extra={"mc_seed": 0},
    )


@pytest.mark.asyncio
async def test_comparable_ps_only_when_unprofitable() -> None:
    ext = _extraction_with_financials(revenue=1e9, net_profit=-2e8)
    md = _md_with_peers(ps_ttm=[3.0, 5.0, 7.0], pe_ttm=[15.0, 20.0, 30.0])
    out = await ComparableValuation().value(ext, md)
    assert out.applicable
    # PS-only path; expected revenue * median(3,5,7)=5x → ~5e9.
    assert 3e9 < float(out.valuation_distribution.p50) < 7e9
    assert out.key_assumptions["pe_sample_size"] == 0


@pytest.mark.asyncio
async def test_comparable_blends_ps_and_pe_when_profitable() -> None:
    ext = _extraction_with_financials(revenue=1e9, net_profit=2e8)
    md = _md_with_peers(ps_ttm=[5.0], pe_ttm=[20.0])
    out = await ComparableValuation().value(ext, md)
    assert out.applicable
    # blend: 0.5 * (5 * 1e9) + 0.5 * (20 * 2e8) = 0.5 * 5e9 + 0.5 * 4e9 = 4.5e9
    assert 4.4e9 < float(out.valuation_distribution.p50) < 4.6e9


@pytest.mark.asyncio
async def test_comparable_filters_outlier_multiples() -> None:
    ext = _extraction_with_financials(revenue=1e9, net_profit=0)
    md = _md_with_peers(ps_ttm=[5.0, -1.0, 250.0])  # -1 and 250 filtered
    out = await ComparableValuation().value(ext, md)
    assert out.applicable
    assert out.key_assumptions["ps_sample_size"] == 1


@pytest.mark.asyncio
async def test_comparable_no_peers_not_applicable() -> None:
    ext = _extraction_with_financials(revenue=1e9, net_profit=2e8)
    md = _md_with_peers()
    out = await ComparableValuation().value(ext, md)
    assert not out.applicable


@pytest.mark.asyncio
async def test_comparable_zero_revenue_not_applicable() -> None:
    ext = _extraction_with_financials(revenue=0, net_profit=2e8)
    md = _md_with_peers(ps_ttm=[5.0])
    out = await ComparableValuation().value(ext, md)
    assert not out.applicable
