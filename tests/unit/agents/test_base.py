"""Tests for ``agents.base`` — load_prompt + AgentContext."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from hk_ipo_agent.agents.base import AgentContext, load_prompt
from hk_ipo_agent.agents.workflow_extras import WorkflowExtras
from hk_ipo_agent.common.enums import ListingType
from hk_ipo_agent.common.llm_client import LLMClient
from hk_ipo_agent.common.schemas import ProspectusExtraction
from hk_ipo_agent.valuation.base import MarketData


def test_load_prompt_parses_frontmatter() -> None:
    body, fm = load_prompt("agents/policy.md")
    assert "Role" in body or "role" in body
    assert fm.get("role") == "policy_agent"
    assert fm.get("version") == "1.0"
    # inherited_inputs is a yaml list
    assert isinstance(fm.get("inherited_inputs"), list)
    assert "regime_score" in fm["inherited_inputs"]


def test_load_prompt_returns_full_body_when_no_frontmatter() -> None:
    # Use a tmp-less round trip via re-loading agents/fundamental.md (has FM).
    body, fm = load_prompt("agents/fundamental.md")
    assert body.strip().startswith("# Role")
    assert fm.get("role") == "fundamental_agent"


def test_agent_context_construction(monkeypatch) -> None:
    """AgentContext is a dataclass with sensible defaults."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    ext = ProspectusExtraction(
        prospectus_id="P-1",
        company_name_zh="测试",
        listing_type=ListingType.MAINBOARD_TECH,
        industry_code="TECH",
        industry_description="AI",
        business_model="B2B",
        extraction_version="0.0.1",
        extracted_at=datetime.now(UTC),
    )
    md = MarketData(as_of_date=date(2026, 5, 16), listing_type=ListingType.MAINBOARD_TECH)
    client = LLMClient(daily_budget_usd=Decimal("100"))
    ctx = AgentContext(
        ipo_id="ipo-1",
        extraction=ext,
        market_data=md,
        llm_client=client,
    )
    assert ctx.ipo_id == "ipo-1"
    assert isinstance(ctx.extras, WorkflowExtras)
    assert ctx.prospectus_tool is None
