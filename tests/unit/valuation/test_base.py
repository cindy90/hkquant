"""Tests for ``hk_ipo_agent.valuation.base`` helpers + MarketData + ABC."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import ClassVar

import numpy as np

from hk_ipo_agent.common.enums import ListingType
from hk_ipo_agent.common.schemas import ProspectusExtraction
from hk_ipo_agent.valuation.base import (
    MarketData,
    PeerMultiples,
    ValuationModel,
    _citation_from_extraction,
    distribution_from_samples,
)


def _make_extraction(listing_type: ListingType = ListingType.MAINBOARD_TECH) -> ProspectusExtraction:
    return ProspectusExtraction(
        prospectus_id="P-T-1",
        company_name_zh="测试",
        listing_type=listing_type,
        industry_code="TECH",
        industry_description="AI / SaaS",
        business_model="B2B SaaS",
        extraction_version="0.0.1",
        extracted_at=datetime.now(UTC),
    )


def test_distribution_from_samples_returns_zeros_when_empty() -> None:
    dist = distribution_from_samples(np.array([np.nan, np.inf, -np.inf]))
    assert dist.p10 == Decimal("0")
    assert dist.p90 == Decimal("0")
    assert dist.mean == Decimal("0")


def test_distribution_from_samples_percentiles_monotonic() -> None:
    arr = np.arange(0.0, 1001.0, 1.0)  # 0..1000
    dist = distribution_from_samples(arr)
    assert dist.p10 < dist.p25 < dist.p50 < dist.p75 < dist.p90
    # Approximate percentiles of 0..1000 → ~100/250/500/750/900.
    assert abs(float(dist.p50) - 500.0) < 1.0
    assert dist.std > 0


def test_distribution_from_samples_filters_non_finite() -> None:
    arr = np.array([1.0, 2.0, 3.0, np.nan, np.inf, 4.0])
    dist = distribution_from_samples(arr)
    # mean of {1,2,3,4} = 2.5
    assert abs(float(dist.mean) - 2.5) < 0.01


def test_peer_multiples_has_data_flag() -> None:
    pm = PeerMultiples()
    assert pm.has_data is False
    pm = PeerMultiples(ps_ttm=[3.0, 5.0], sample_size=2)
    assert pm.has_data is True


def test_market_data_defaults() -> None:
    md = MarketData(as_of_date=date(2026, 5, 16), listing_type=ListingType.MAINBOARD_TECH)
    assert md.peer_multiples is None
    assert md.regime_score is None
    assert md.risk_free_rate == 0.025


def test_valuation_model_applies_to() -> None:
    class M(ValuationModel):
        model_name = "x"
        applicable_types: ClassVar[list[ListingType]] = [ListingType.AH_DUAL]

        async def value(self, extraction, market_data):  # type: ignore[no-untyped-def]
            raise NotImplementedError

    m = M()
    assert m.applies_to(ListingType.AH_DUAL) is True
    assert m.applies_to(ListingType.MAINBOARD_TECH) is False


def test_not_applicable_helper() -> None:
    out = ValuationModel._not_applicable(reason="r1", model_name="m1")
    assert out.applicable is False
    assert out.model_name == "m1"
    assert out.key_assumptions["not_applicable_reason"] == "r1"
    assert out.valuation_distribution.p50 == Decimal("0")


def test_citation_from_extraction_falls_back_to_page_1() -> None:
    ext = _make_extraction()
    cites = _citation_from_extraction(ext)
    assert len(cites) == 1
    assert cites[0].page == 1
