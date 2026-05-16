"""Tests for ``policy_agent`` — Regime Gate computation + Agent run."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from hk_ipo_agent.agents.base import AgentContext
from hk_ipo_agent.agents.policy_agent import PolicyAgent, compute_regime_score
from hk_ipo_agent.agents.workflow_extras import WorkflowExtras
from hk_ipo_agent.common.enums import ListingType
from hk_ipo_agent.common.schemas import ProspectusExtraction
from hk_ipo_agent.valuation.base import MarketData


def _ext() -> ProspectusExtraction:
    return ProspectusExtraction(
        prospectus_id="P-POL-1",
        company_name_zh="测试公司",
        listing_type=ListingType.CH18C_COMMERCIALIZED,
        industry_code="AI",
        industry_description="AI / SaaS",
        business_model="B2B",
        extraction_version="0.0.1",
        extracted_at=datetime.now(UTC),
    )


def _ctx(
    *,
    llm_client,
    regime_override: float | None = None,
    pricing_date: date | None = None,
) -> AgentContext:
    md = MarketData(as_of_date=date(2026, 5, 16), listing_type=ListingType.CH18C_COMMERCIALIZED)
    extras = WorkflowExtras(pricing_date=pricing_date)
    if regime_override is not None:
        extras.misc["regime_score_override"] = regime_override
    return AgentContext(
        ipo_id="ipo-1",
        extraction=_ext(),
        market_data=md,
        llm_client=llm_client,
        extras=extras,
    )


@pytest.mark.asyncio
async def test_compute_regime_score_uses_override(mock_llm_client) -> None:
    ctx = _ctx(llm_client=mock_llm_client, regime_override=-0.05)
    result = await compute_regime_score(ctx)
    assert result.score == -0.05


@pytest.mark.asyncio
async def test_compute_regime_score_no_ifind_returns_none(mock_llm_client) -> None:
    ctx = _ctx(llm_client=mock_llm_client, pricing_date=date(2026, 5, 16))
    result = await compute_regime_score(ctx)
    assert result.score is None


@pytest.mark.asyncio
async def test_compute_regime_score_aggregates_from_ifind(mock_llm_client) -> None:
    ctx = _ctx(llm_client=mock_llm_client, pricing_date=date(2026, 5, 16))
    # Inject a fake IFindTool returning 5 IPOs with known 30d returns.
    fake = AsyncMock()
    fake.ipo_history = AsyncMock(
        return_value=[
            {"return_30d": 0.10},
            {"return_30d": -0.05},
            {"return_30d": 0.15},
            {"return_30d": 0.20},
            {"return_30d": -0.10},
        ]
    )
    ctx.ifind_tool = fake
    result = await compute_regime_score(ctx)
    assert result.score == 0.10  # median of [0.10, -0.05, 0.15, 0.20, -0.10] sorted = 0.10
    assert result.sample_size == 5


@pytest.mark.asyncio
async def test_policy_agent_run_writes_regime_score_to_extras(
    mock_llm_client, mock_llm_response
) -> None:
    ctx = _ctx(llm_client=mock_llm_client, regime_override=0.08)
    # Stub LLM to return a parseable scorecard.
    mock_llm_client._client.messages.create = AsyncMock(
        return_value=mock_llm_response(
            text=(
                "Some narrative.\n"
                "```json\n{\"regime_fit\": 70, \"policy_tailwind\": 60, \"regime_score\": 0, "
                "\"evidence_pages\": [1], \"notes\": \"ok\"}\n```"
            )
        )
    )

    out = await PolicyAgent().run(ctx)
    assert ctx.extras.regime_score == 0.08
    assert "regime_score" in out.scores
    # Agent code overrides regime_score to deterministic value × 100.
    assert out.scores["regime_score"] == pytest.approx(8.0)
    assert out.agent_role.value == "policy"


@pytest.mark.asyncio
async def test_policy_agent_negative_regime_finding(
    mock_llm_client, mock_llm_response
) -> None:
    ctx = _ctx(llm_client=mock_llm_client, regime_override=-0.10)
    mock_llm_client._client.messages.create = AsyncMock(
        return_value=mock_llm_response(text="narrative")
    )
    out = await PolicyAgent().run(ctx)
    # Negative regime → at least one Finding should mention BELOW
    assert any("BELOW" in f.statement for f in out.key_findings)


@pytest.mark.asyncio
async def test_policy_agent_runtime_seconds_reported(
    mock_llm_client, mock_llm_response
) -> None:
    ctx = _ctx(llm_client=mock_llm_client, regime_override=0.0)
    mock_llm_client._client.messages.create = AsyncMock(
        return_value=mock_llm_response(text="narrative")
    )
    out = await PolicyAgent().run(ctx)
    assert out.runtime_seconds >= 0
    assert isinstance(out.cost_usd, Decimal)
