"""Tests for ``MilestonesValuation`` (real-option NPV)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from hk_ipo_agent.common.enums import ListingType
from hk_ipo_agent.common.schemas import ProspectusExtraction
from hk_ipo_agent.valuation.base import MarketData
from hk_ipo_agent.valuation.milestones import (
    MilestonesConfig,
    MilestoneStage,
    MilestonesValuation,
)


def _ext_pre(
    lt: ListingType = ListingType.CH18C_PRE_COMMERCIAL,
) -> ProspectusExtraction:
    return ProspectusExtraction(
        prospectus_id="P-MS-1",
        company_name_zh="测试",
        listing_type=lt,
        industry_code="BIOTECH-AI",
        industry_description="pre-commercial",
        business_model="platform",
        extraction_version="0.0.1",
        extracted_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_milestones_runs_with_defaults() -> None:
    ext = _ext_pre()
    md = MarketData(
        as_of_date=date(2026, 5, 16),
        listing_type=ListingType.CH18C_PRE_COMMERCIAL,
        extra={"mc_seed": 0},
    )
    out = await MilestonesValuation().value(ext, md)
    assert out.applicable
    # 4-stage default ladder; expected NPV is positive.
    assert float(out.valuation_distribution.p50) > 0
    assert out.key_assumptions["stage_count"] == 4


@pytest.mark.asyncio
async def test_milestones_not_applicable_for_commercial() -> None:
    ext = _ext_pre(lt=ListingType.CH18C_COMMERCIALIZED)
    md = MarketData(
        as_of_date=date(2026, 5, 16),
        listing_type=ListingType.CH18C_COMMERCIALIZED,
        extra={"mc_seed": 0},
    )
    out = await MilestonesValuation().value(ext, md)
    assert not out.applicable


@pytest.mark.asyncio
async def test_milestones_custom_ladder() -> None:
    ext = _ext_pre()
    cfg = MilestonesConfig(
        stages=[
            MilestoneStage("only", 1.0, 1e9, 0.0001, 0.10, 0.10, 0.0),
        ]
    )
    md = MarketData(
        as_of_date=date(2026, 5, 16),
        listing_type=ListingType.CH18C_PRE_COMMERCIAL,
        extra={"mc_seed": 0, "milestones": {"config": cfg}},
    )
    out = await MilestonesValuation().value(ext, md)
    assert out.applicable
    # p=1.0, EV=1e9, disc=0, years=0 → expected ≈ 1e9.
    assert 0.9e9 < float(out.valuation_distribution.p50) < 1.1e9


@pytest.mark.asyncio
async def test_milestones_empty_ladder_not_applicable() -> None:
    ext = _ext_pre()
    md = MarketData(
        as_of_date=date(2026, 5, 16),
        listing_type=ListingType.CH18C_PRE_COMMERCIAL,
        extra={"mc_seed": 0, "milestones": {"config": MilestonesConfig(stages=[])}},
    )
    out = await MilestonesValuation().value(ext, md)
    assert not out.applicable
