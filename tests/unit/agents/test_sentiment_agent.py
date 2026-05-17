"""Tests for ``sentiment_agent`` — theme heat + AI gilding."""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

import pytest

from hk_ipo_agent.agents.base import AgentContext
from hk_ipo_agent.agents.sentiment_agent import (
    SentimentAgent,
    detect_ai_gilding,
    lookup_ai_revenue,
)
from hk_ipo_agent.agents.workflow_extras import WorkflowExtras
from hk_ipo_agent.common.enums import ListingType
from hk_ipo_agent.common.schemas import ProspectusExtraction
from hk_ipo_agent.valuation.base import MarketData


def test_detect_ai_gilding_true_when_claims_but_low_revenue() -> None:
    assert detect_ai_gilding(
        extraction_industry="AI 智能",
        extraction_business="AI-powered analytics",
        ai_revenue_pct=0.05,
    )


def test_detect_ai_gilding_false_when_no_claim() -> None:
    assert not detect_ai_gilding(
        extraction_industry="Retail",
        extraction_business="grocery chain",
        ai_revenue_pct=0.01,
    )


def test_detect_ai_gilding_false_when_revenue_unknown() -> None:
    assert not detect_ai_gilding(
        extraction_industry="AI",
        extraction_business="AI startup",
        ai_revenue_pct=None,
    )


def test_detect_ai_gilding_false_when_revenue_high() -> None:
    assert not detect_ai_gilding(
        extraction_industry="AI",
        extraction_business="AI core",
        ai_revenue_pct=0.50,
    )


def test_lookup_ai_revenue_finds_sample() -> None:
    payload = {
        "samples": [
            {"code": "00992.HK", "ai_revenue_pct": 0.30, "needs_review": False},
        ]
    }
    assert lookup_ai_revenue("00992.HK", payload) == 0.30


def test_lookup_ai_revenue_skips_needs_review() -> None:
    payload = {
        "samples": [
            {"code": "00992.HK", "ai_revenue_pct": 0.30, "needs_review": True},
        ]
    }
    assert lookup_ai_revenue("00992.HK", payload) is None


def _ctx(llm_client, *, industry="AI", company="测试", stock_code="09999.HK") -> AgentContext:
    # R1-3 — extractions in production always have ≥1 risk_factor; fixture must too.
    from hk_ipo_agent.common.schemas import Citation, RiskFactor

    ext = ProspectusExtraction(
        prospectus_id="P-S-1",
        company_name_zh=company,
        listing_type=ListingType.MAINBOARD_TECH,
        industry_code=industry,
        industry_description="general",
        business_model="AI core",
        stock_code=stock_code,
        risk_factors=[
            RiskFactor(
                category="business",
                description="sentiment placeholder",
                severity="low",
                citation=Citation(page=42),
            )
        ],
        extraction_version="0.0.1",
        extracted_at=datetime.now(UTC),
    )
    md = MarketData(as_of_date=date(2026, 5, 16), listing_type=ListingType.MAINBOARD_TECH)
    return AgentContext(
        ipo_id="ipo-1",
        extraction=ext,
        market_data=md,
        llm_client=llm_client,
        extras=WorkflowExtras(),
    )


@pytest.mark.asyncio
async def test_sentiment_agent_fallback_path_preserves_full_prompt(
    mock_llm_client, mock_llm_response
) -> None:
    """R1-4 — f-string ternary precedence bug at sentiment_agent.py:140-149.

    With theme_heat_pct=None (no KB tool), buggy code returned ONLY
    ``"- theme_heat: N/A\\n"`` as the entire user_msg, swallowing the
    # Target IPO / # Computed signals / # Task headers behind the ternary.
    """
    ctx = _ctx(mock_llm_client)  # no kb_tool → theme_heat_pct = None

    captured: dict[str, str] = {}

    async def _capture(*, model: str, messages: list[dict], **kw):  # noqa: ARG001
        captured["user"] = next(m["content"] for m in messages if m["role"] == "user")
        return mock_llm_response(text="narrative")

    mock_llm_client._client.chat.completions.create = AsyncMock(side_effect=_capture)

    await SentimentAgent().run(ctx)

    user_prompt = captured.get("user", "")
    assert "# Target IPO" in user_prompt, (
        f"# Target IPO header missing — sentiment_agent fallback bug not fixed.\n"
        f"Got user_prompt: {user_prompt[:200]}"
    )
    assert "# Computed signals" in user_prompt
    assert "# Task" in user_prompt
    assert "theme_heat" in user_prompt  # either value or "N/A"


@pytest.mark.asyncio
async def test_sentiment_agent_runs_without_kb(mock_llm_client, mock_llm_response) -> None:
    ctx = _ctx(mock_llm_client)
    mock_llm_client._client.chat.completions.create = AsyncMock(
        return_value=mock_llm_response(text="narrative")
    )
    out = await SentimentAgent().run(ctx)
    assert out.scores["theme_heat"] == 0.0  # no KB → 0
    assert out.agent_role.value == "sentiment"


@pytest.mark.asyncio
async def test_sentiment_agent_with_kb_writes_theme_heat(
    mock_llm_client, mock_llm_response
) -> None:
    ctx = _ctx(mock_llm_client, industry="AI 服务器")

    class _KB:
        def themes_heat(self):
            return {"themes": {"ai_server": {"heat_score": 72}}}

        def theme_definitions(self):
            return {
                "themes": {"ai_server": {"label": "AI 服务器", "keywords": ["AI", "AI 服务器"]}}
            }

        def ai_revenue_manual(self):
            return {"samples": []}

        def match_themes(self, *, industry_code, company_name):
            return ["ai_server"]

    ctx.kb_tool = _KB()
    mock_llm_client._client.chat.completions.create = AsyncMock(
        return_value=mock_llm_response(text="narrative")
    )
    out = await SentimentAgent().run(ctx)
    assert ctx.extras.theme_heat == pytest.approx(0.72)
    assert ctx.extras.theme_matched == "ai_server"
    assert out.scores["theme_heat"] == pytest.approx(72.0)
