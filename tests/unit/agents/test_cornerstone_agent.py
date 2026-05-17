"""Tests for ``cornerstone_signal_agent`` — ultimate_holder clustering."""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

import pytest

from hk_ipo_agent.agents.base import AgentContext
from hk_ipo_agent.agents.cornerstone_signal_agent import (
    CornerstoneSignalAgent,
    cluster_by_ultimate_holder,
)
from hk_ipo_agent.agents.workflow_extras import WorkflowExtras
from hk_ipo_agent.common.enums import ListingType
from hk_ipo_agent.common.schemas import Citation, ProspectusExtraction, RiskFactor
from hk_ipo_agent.valuation.base import MarketData


# Minimum risk_factor so _pick_extraction_citations has a fallback citation.
# R1-3 enforces "no silent Citation(page=1) fabrication" — real extractions
# always have at least one risk_factor, so test fixtures must too.
_DEFAULT_RISK = [
    RiskFactor(
        category="business",
        description="placeholder risk for fixture",
        severity="medium",
        citation=Citation(page=42),
    )
]


def test_cluster_no_holder_returns_baseline() -> None:
    result = cluster_by_ultimate_holder([])
    assert result.multiplier == 1.0
    assert result.groups == []


def test_cluster_single_holder_no_bonus() -> None:
    cs = [{"name": "A", "ultimate_holder": "X"}]
    result = cluster_by_ultimate_holder(cs)
    assert result.multiplier == 1.0
    assert result.multi_member_groups == 0


def test_cluster_two_same_holder_triggers_bonus() -> None:
    cs = [
        {"name": "A1", "ultimate_holder": "Beijing-State"},
        {"name": "A2", "ultimate_holder": "Beijing-State"},
        {"name": "B", "ultimate_holder": "Other"},
    ]
    result = cluster_by_ultimate_holder(cs)
    assert result.multiplier == pytest.approx(1.10)
    assert result.multi_member_groups == 1
    assert len(result.groups) == 1


def test_cluster_multiple_clusters_max_bonus() -> None:
    cs = [
        {"name": "A1", "ultimate_holder": "Hold-A"},
        {"name": "A2", "ultimate_holder": "Hold-A"},
        {"name": "B1", "ultimate_holder": "Hold-B"},
        {"name": "B2", "ultimate_holder": "Hold-B"},
    ]
    result = cluster_by_ultimate_holder(cs)
    assert result.multiplier == pytest.approx(1.20)
    assert result.multi_member_groups == 2


def test_cluster_unknown_holder_excluded() -> None:
    cs = [
        {"name": "A", "ultimate_holder": "unknown"},
        {"name": "B", "ultimate_holder": ""},
        {"name": "C", "ultimate_holder": None},
    ]
    result = cluster_by_ultimate_holder(cs)
    assert result.multiplier == 1.0


def _ctx(llm_client, cornerstones=None, sponsors=None) -> AgentContext:
    ext = ProspectusExtraction(
        prospectus_id="P-CS-1",
        company_name_zh="测试",
        listing_type=ListingType.CH18C_COMMERCIALIZED,
        industry_code="AI",
        industry_description="x",
        business_model="x",
        risk_factors=list(_DEFAULT_RISK),
        extraction_version="0.0.1",
        extracted_at=datetime.now(UTC),
    )
    md = MarketData(as_of_date=date(2026, 5, 16), listing_type=ListingType.CH18C_COMMERCIALIZED)
    extras = WorkflowExtras(
        cornerstone_profiles=cornerstones or [],
        sponsor_track_records=sponsors or [],
    )
    return AgentContext(
        ipo_id="ipo-1", extraction=ext, market_data=md, llm_client=llm_client, extras=extras
    )


@pytest.mark.asyncio
async def test_cornerstone_agent_writes_multiplier_to_extras(
    mock_llm_client, mock_llm_response
) -> None:
    cornerstones = [
        {"name": "A1", "category": "sovereign", "ultimate_holder": "中投"},
        {"name": "A2", "category": "sovereign", "ultimate_holder": "中投"},
    ]
    ctx = _ctx(mock_llm_client, cornerstones=cornerstones)
    mock_llm_client._client.chat.completions.create = AsyncMock(
        return_value=mock_llm_response(text="narrative")
    )
    out = await CornerstoneSignalAgent().run(ctx)
    assert ctx.extras.cluster_bonus_multiplier == pytest.approx(1.10)
    assert out.scores["cluster_bonus"] == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_cornerstone_agent_no_cluster_zero_bonus(mock_llm_client, mock_llm_response) -> None:
    ctx = _ctx(mock_llm_client, cornerstones=[])
    mock_llm_client._client.chat.completions.create = AsyncMock(
        return_value=mock_llm_response(text="narrative")
    )
    out = await CornerstoneSignalAgent().run(ctx)
    assert ctx.extras.cluster_bonus_multiplier == 1.0
    assert out.scores["cluster_bonus"] == 0.0


@pytest.mark.asyncio
async def test_cornerstone_agent_emits_data_sources(mock_llm_client, mock_llm_response) -> None:
    ctx = _ctx(mock_llm_client)
    mock_llm_client._client.chat.completions.create = AsyncMock(
        return_value=mock_llm_response(text="narrative")
    )
    out = await CornerstoneSignalAgent().run(ctx)
    src_types = {ds.source for ds in out.data_sources_used}
    assert "kb_cornerstones" in src_types
    assert "kb_sponsors" in src_types
