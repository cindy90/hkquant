"""Tests for cross_checker historical matching."""

from __future__ import annotations

from hk_ipo_agent.common.enums import ListingType
from hk_ipo_agent.critic.cross_checker import cross_check


def test_cross_check_no_records() -> None:
    result = cross_check(
        listing_type=ListingType.MAINBOARD_TECH,
        industry_code="AI",
        historical_records=None,
    )
    assert result.sample_size == 0
    assert result.median_60d_return is None
    assert any("no historical" in n for n in result.notes)


def test_cross_check_no_matches() -> None:
    records = [
        {
            "listing_type": ListingType.CH18A_BIOTECH.value,
            "industry_code": "BIOTECH",
            "return_60d": 0.10,
            "max_drawdown_60d": 0.20,
        }
    ]
    result = cross_check(
        listing_type=ListingType.MAINBOARD_TECH,
        industry_code="AI",
        historical_records=records,
    )
    assert result.sample_size == 0
    assert any("no historical IPO matched" in n for n in result.notes)


def test_cross_check_matches() -> None:
    records = [
        {
            "listing_type": ListingType.MAINBOARD_TECH.value,
            "industry_code": "AI",
            "return_60d": 0.10,
            "max_drawdown_60d": 0.15,
        },
        {
            "listing_type": ListingType.MAINBOARD_TECH.value,
            "industry_code": "AI-SaaS",
            "return_60d": 0.30,
            "max_drawdown_60d": 0.25,
        },
        {
            "listing_type": ListingType.CH18C_COMMERCIALIZED.value,
            "industry_code": "AI",
            "return_60d": 0.50,
            "max_drawdown_60d": 0.40,
        },  # different listing type → excluded
    ]
    result = cross_check(
        listing_type=ListingType.MAINBOARD_TECH,
        industry_code="AI",
        historical_records=records,
    )
    assert result.sample_size == 2
    # median(0.10, 0.30) = 0.20
    assert result.median_60d_return == 0.20
    assert result.median_drawdown == 0.20
