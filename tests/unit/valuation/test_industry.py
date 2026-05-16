"""Tests for ``industry/`` specializations (ai_arr, semiconductor)."""

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
from hk_ipo_agent.valuation.industry import (
    AIARRValuation,
    SemiconductorValuation,
    industry_models,
    industry_models_for,
)


def _ext(
    industry_code: str,
    industry_description: str = "",
    revenue: float = 1e9,
    cash: float = 0.0,
    lt: ListingType = ListingType.MAINBOARD_TECH,
) -> ProspectusExtraction:
    return ProspectusExtraction(
        prospectus_id="P-IND-1",
        company_name_zh="测试",
        listing_type=lt,
        industry_code=industry_code,
        industry_description=industry_description,
        business_model="x",
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


def _md(extra: dict | None = None) -> MarketData:
    return MarketData(
        as_of_date=date(2026, 5, 16),
        listing_type=ListingType.MAINBOARD_TECH,
        extra={"mc_seed": 0, **(extra or {})},
    )


@pytest.mark.asyncio
async def test_ai_arr_matches_saas() -> None:
    ext = _ext("SaaS", "Cloud SaaS")
    out = await AIARRValuation().value(ext, _md())
    assert out.applicable
    # ARR 1e9 * median multiple 7 → 7e9; LogNormal sigma=0.4 keeps p50 in band.
    assert 5e9 < float(out.valuation_distribution.p50) < 1e10


@pytest.mark.asyncio
async def test_ai_arr_rejects_non_ai_industry() -> None:
    ext = _ext("FOOD-RETAIL", "consumer")
    out = await AIARRValuation().value(ext, _md())
    assert not out.applicable


@pytest.mark.asyncio
async def test_ai_arr_zh_keyword_match() -> None:
    ext = _ext("人工智能", "AI-powered analytics")
    out = await AIARRValuation().value(ext, _md())
    assert out.applicable


@pytest.mark.asyncio
async def test_semiconductor_cycle_phase_shifts_median() -> None:
    ext = _ext("Semiconductor", "IC Design")
    out_trough = await SemiconductorValuation().value(
        ext, _md({"semiconductor": {"cycle_phase": "trough"}})
    )
    out_peak = await SemiconductorValuation().value(
        ext, _md({"semiconductor": {"cycle_phase": "peak"}})
    )
    assert out_trough.applicable and out_peak.applicable
    # Peak median should be > trough median by ~85% (1.30/0.70 ≈ 1.86).
    assert float(out_peak.valuation_distribution.p50) > float(out_trough.valuation_distribution.p50)


@pytest.mark.asyncio
async def test_semiconductor_rejects_unrelated_industry() -> None:
    ext = _ext("Real Estate", "REIT")
    out = await SemiconductorValuation().value(ext, _md())
    assert not out.applicable


def test_industry_models_returns_both() -> None:
    models = industry_models()
    names = [type(m).__name__ for m in models]
    assert "AIARRValuation" in names
    assert "SemiconductorValuation" in names


def test_industry_models_for_filters_correctly() -> None:
    ai_ext = _ext("AI", "Artificial Intelligence")
    semi_ext = _ext("Semiconductor", "IC Design")
    other_ext = _ext("Real Estate")

    ai_matches = [type(m).__name__ for m in industry_models_for(ai_ext)]
    semi_matches = [type(m).__name__ for m in industry_models_for(semi_ext)]
    other_matches = list(industry_models_for(other_ext))

    assert "AIARRValuation" in ai_matches
    assert "SemiconductorValuation" in semi_matches
    assert other_matches == []
