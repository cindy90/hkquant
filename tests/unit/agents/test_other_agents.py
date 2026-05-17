"""Unit tests for the 4 non-NACS agents:
fundamental / industry / valuation / liquidity."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from hk_ipo_agent.agents.base import AgentContext
from hk_ipo_agent.agents.fundamental_agent import (
    FundamentalAgent,
    customer_concentration_top1,
    gross_margin_trend,
    revenue_cagr,
)
from hk_ipo_agent.agents.industry_agent import IndustryAgent, peer_multiple_summary
from hk_ipo_agent.agents.liquidity_agent import (
    LiquidityAgent,
    buyback_clause_count,
    controlling_share_pct,
)
from hk_ipo_agent.agents.valuation_agent import ValuationAgent
from hk_ipo_agent.agents.workflow_extras import WorkflowExtras
from hk_ipo_agent.common.enums import ListingType
from hk_ipo_agent.common.schemas import (
    Citation,
    CustomerConcentration,
    FinancialSnapshot,
    ProspectusExtraction,
    ShareholderEntry,
)
from hk_ipo_agent.valuation.base import MarketData, PeerMultiples


def _ext(
    *,
    revenue_periods: list[float] | None = None,
    gross_margins: list[float | None] | None = None,
    top1: float | None = None,
    controlling: float | None = None,
    has_buyback: bool = False,
    listing_type: ListingType = ListingType.MAINBOARD_TECH,
) -> ProspectusExtraction:
    fin = []
    revenue_periods = revenue_periods or []
    gross_margins = gross_margins or [None] * len(revenue_periods)
    for i, rev in enumerate(revenue_periods):
        fin.append(
            FinancialSnapshot(
                fiscal_year=2023 + i,
                fiscal_period="FY",
                revenue_rmb=Decimal(str(rev)),
                gross_margin=gross_margins[i],
                citation=Citation(page=10),
            )
        )
    cc = (
        [
            CustomerConcentration(
                fiscal_year=2025,
                top1_pct=top1,
                top5_pct=top1 + 0.1 if top1 else 0.0,
                citation=Citation(page=20),
            )
        ]
        if top1 is not None
        else []
    )
    sh = []
    if controlling is not None:
        sh.append(
            ShareholderEntry(
                name="Founder",
                pct_pre_ipo=controlling,
                is_controlling=True,
                is_pre_ipo_investor=False,
                has_buyback_clause=False,
                citation=Citation(page=88),
            )
        )
    if has_buyback:
        sh.append(
            ShareholderEntry(
                name="PE-1",
                pct_pre_ipo=0.10,
                is_controlling=False,
                is_pre_ipo_investor=True,
                has_buyback_clause=True,
                citation=Citation(page=89),
            )
        )

    return ProspectusExtraction(
        prospectus_id="P-OTH-1",
        company_name_zh="测试",
        listing_type=listing_type,
        industry_code="TECH",
        industry_description="general",
        business_model="B2B",
        financials=fin,
        customer_concentration=cc,
        shareholders=sh,
        extraction_version="0.0.1",
        extracted_at=datetime.now(UTC),
    )


# --------------------------------------------------------------------- fundamental


def test_revenue_cagr_two_periods() -> None:
    ext = _ext(revenue_periods=[100, 144])
    cagr = revenue_cagr(ext.financials)
    assert cagr == pytest.approx(0.44, rel=0.01)  # 44% YoY


def test_revenue_cagr_returns_none_when_one_period() -> None:
    ext = _ext(revenue_periods=[100])
    assert revenue_cagr(ext.financials) is None


def test_gross_margin_trend_filters_none() -> None:
    ext = _ext(revenue_periods=[100, 200, 300], gross_margins=[0.30, None, 0.40])
    assert gross_margin_trend(ext.financials) == [0.30, 0.40]


def test_customer_concentration_top1() -> None:
    ext = _ext(revenue_periods=[100], top1=0.35)
    assert customer_concentration_top1(ext.customer_concentration) == 0.35


@pytest.mark.asyncio
async def test_fundamental_agent_fallback_path_preserves_full_prompt(
    mock_llm_client, mock_llm_response
) -> None:
    """R1-4 — f-string ternary precedence bug at fundamental_agent.py:105-115.

    With cagr=None (one-period extraction), the buggy code returned ONLY
    "- Revenue CAGR: insufficient periods\\n" as the entire user_msg
    because the `if/else` ternary bound to the whole parenthesized concat.
    The subsequent `user_msg += ...` appended onto a single-line string,
    losing the # Target IPO / # Computed primitives / # Task sections.

    The LLM then received a malformed prompt and silently produced low-
    quality output. This test pins the prompt-completeness contract.
    """
    ext = _ext(revenue_periods=[100])  # one period → cagr=None → fallback path
    md = MarketData(as_of_date=date(2026, 5, 16), listing_type=ListingType.MAINBOARD_TECH)
    ctx = AgentContext(
        ipo_id="ipo-1",
        extraction=ext,
        market_data=md,
        llm_client=mock_llm_client,
        extras=WorkflowExtras(),
    )

    captured: dict[str, str] = {}

    async def _capture(*, model: str, messages: list[dict], **kw):  # noqa: ARG001
        captured["user"] = next(m["content"] for m in messages if m["role"] == "user")
        return mock_llm_response(text="narrative")

    mock_llm_client._client.chat.completions.create = AsyncMock(side_effect=_capture)

    await FundamentalAgent().run(ctx)

    user_prompt = captured.get("user", "")
    # All four section headers must survive the fallback path.
    assert "# Target IPO" in user_prompt, (
        f"# Target IPO header missing — fundamental_agent fallback bug not fixed.\n"
        f"Got user_prompt: {user_prompt[:200]}"
    )
    assert "# Computed primitives" in user_prompt
    assert "# Financials snapshot" in user_prompt
    assert "# Task" in user_prompt
    # The fallback line itself should be present
    assert "insufficient periods" in user_prompt


@pytest.mark.asyncio
async def test_liquidity_agent_fallback_path_preserves_full_prompt(
    mock_llm_client, mock_llm_response
) -> None:
    """R1-4 — same precedence bug at liquidity_agent.py:64-72.

    With controlling=None (empty shareholders), buggy code lost most of
    the prompt to the ``if/else`` branch.
    """
    ext = _ext(revenue_periods=[100, 144], top1=0.40)  # no controlling shareholder
    md = MarketData(as_of_date=date(2026, 5, 16), listing_type=ListingType.MAINBOARD_TECH)
    ctx = AgentContext(
        ipo_id="ipo-1",
        extraction=ext,
        market_data=md,
        llm_client=mock_llm_client,
        extras=WorkflowExtras(),
    )

    captured: dict[str, str] = {}

    async def _capture(*, model: str, messages: list[dict], **kw):  # noqa: ARG001
        captured["user"] = next(m["content"] for m in messages if m["role"] == "user")
        return mock_llm_response(text="narrative")

    mock_llm_client._client.chat.completions.create = AsyncMock(side_effect=_capture)

    await LiquidityAgent().run(ctx)

    user_prompt = captured.get("user", "")
    assert "# Target IPO" in user_prompt, (
        f"# Target IPO header missing — liquidity_agent fallback bug not fixed.\n"
        f"Got user_prompt: {user_prompt[:200]}"
    )
    assert "# Computed primitives" in user_prompt
    assert "# Task" in user_prompt
    # Either way the controlling-shareholder line should appear (probably n/a)
    assert "Controlling shareholder" in user_prompt


@pytest.mark.asyncio
async def test_fundamental_agent_run_returns_output(mock_llm_client, mock_llm_response) -> None:
    ext = _ext(revenue_periods=[100, 144], gross_margins=[0.30, 0.40], top1=0.40)
    md = MarketData(as_of_date=date(2026, 5, 16), listing_type=ListingType.MAINBOARD_TECH)
    ctx = AgentContext(
        ipo_id="ipo-1",
        extraction=ext,
        market_data=md,
        llm_client=mock_llm_client,
        extras=WorkflowExtras(),
    )
    mock_llm_client._client.chat.completions.create = AsyncMock(
        return_value=mock_llm_response(text="narrative")
    )
    out = await FundamentalAgent().run(ctx)
    assert out.agent_role.value == "fundamental"
    # Top-1 > 0.30 should produce a finding
    assert any("concentration" in f.statement.lower() for f in out.key_findings)


# --------------------------------------------------------------------- industry


def test_peer_multiple_summary_basic() -> None:
    res = peer_multiple_summary([3.0, 5.0, 7.0, 10.0, 12.0])
    assert res["n"] == 5
    assert res["p50"] == 7.0


def test_peer_multiple_summary_empty() -> None:
    assert peer_multiple_summary([]) == {"n": 0}


@pytest.mark.asyncio
async def test_industry_agent_emits_data_source(mock_llm_client, mock_llm_response) -> None:
    ext = _ext(revenue_periods=[100, 144])
    md = MarketData(as_of_date=date(2026, 5, 16), listing_type=ListingType.MAINBOARD_TECH)
    extras = WorkflowExtras(peer_multiples={"ps_ttm": [3.0, 5.0, 7.0], "pe_ttm": [20.0]})
    ctx = AgentContext(
        ipo_id="ipo-1",
        extraction=ext,
        market_data=md,
        llm_client=mock_llm_client,
        extras=extras,
    )
    mock_llm_client._client.chat.completions.create = AsyncMock(
        return_value=mock_llm_response(text="narrative")
    )
    out = await IndustryAgent().run(ctx)
    assert {ds.source for ds in out.data_sources_used} >= {"ifind", "prospectus"}


# --------------------------------------------------------------------- valuation


@pytest.mark.asyncio
async def test_valuation_agent_drives_ensemble(mock_llm_client, mock_llm_response) -> None:
    ext = _ext(revenue_periods=[800_000_000.0, 1_000_000_000.0])
    md = MarketData(
        as_of_date=date(2026, 5, 16),
        listing_type=ListingType.MAINBOARD_TECH,
        peer_multiples=PeerMultiples(ps_ttm=[3.0, 5.0, 7.0], sample_size=3),
        extra={"mc_seed": 0},
    )
    ctx = AgentContext(
        ipo_id="ipo-1",
        extraction=ext,
        market_data=md,
        llm_client=mock_llm_client,
        extras=WorkflowExtras(),
    )
    mock_llm_client._client.chat.completions.create = AsyncMock(
        return_value=mock_llm_response(text="narrative")
    )
    out = await ValuationAgent().run(ctx)
    # Valuation agent stashes the ensemble output on extras.misc
    assert "valuation_output" in ctx.extras.misc
    assert "method_fit" in out.scores


# --------------------------------------------------------------------- liquidity


def test_controlling_share_pct() -> None:
    ext = _ext(revenue_periods=[100], controlling=0.65)
    assert controlling_share_pct(ext.shareholders) == pytest.approx(0.65)


def test_buyback_clause_count() -> None:
    ext = _ext(revenue_periods=[100], controlling=0.50, has_buyback=True)
    assert buyback_clause_count(ext.shareholders) == 1


@pytest.mark.asyncio
async def test_liquidity_agent_emits_southbound_from_tier(
    mock_llm_client, mock_llm_response
) -> None:
    ext = _ext(revenue_periods=[100], controlling=0.55, listing_type=ListingType.AH_DUAL)
    md = MarketData(as_of_date=date(2026, 5, 16), listing_type=ListingType.AH_DUAL)
    ctx = AgentContext(
        ipo_id="ipo-1",
        extraction=ext,
        market_data=md,
        llm_client=mock_llm_client,
        extras=WorkflowExtras(),
    )
    mock_llm_client._client.chat.completions.create = AsyncMock(
        return_value=mock_llm_response(text="narrative")
    )
    out = await LiquidityAgent().run(ctx)
    # AH listing should get the highest southbound tier
    assert out.scores["southbound_eligibility"] == 90.0
