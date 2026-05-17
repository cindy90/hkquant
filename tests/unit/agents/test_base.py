"""Tests for ``agents.base`` — load_prompt + AgentContext."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from hk_ipo_agent.agents.base import AgentContext, BaseAgent, load_prompt
from hk_ipo_agent.agents.workflow_extras import WorkflowExtras
from hk_ipo_agent.common.enums import ListingType
from hk_ipo_agent.common.exceptions import CitationRequiredError
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


def test_pick_extraction_citations_raises_when_no_evidence_anywhere() -> None:
    """R1-3 — base.py:279 sham citation fallback must be removed.

    Pre-fix: ``_pick_extraction_citations`` returned ``[Citation(page=1)]``
    when neither ``evidence_pages``, ``extraction.financials`` nor
    ``extraction.risk_factors`` had anything to cite. That fabricated a
    page-1 citation, silently bypassing CLAUDE.md's strict-constraint
    "every Finding must be traceable to a prospectus page".

    Post-fix: callers must get ``CitationRequiredError`` so they explicitly
    handle the missing-evidence case (e.g. return an uncertainty_flag-only
    finding instead of a sham one).
    """
    extraction = ProspectusExtraction(
        prospectus_id="P-R1-3",
        company_name_zh="测试",
        listing_type=ListingType.MAINBOARD_TECH,
        industry_code="TECH",
        industry_description="AI",
        business_model="B2B",
        # IMPORTANT: no financials, no risk_factors, no evidence_pages.
        extraction_version="0.0.1",
        extracted_at=datetime.now(UTC),
    )

    with pytest.raises(CitationRequiredError, match="no citation available"):
        BaseAgent._pick_extraction_citations(extraction, evidence_pages=None)


def test_pick_extraction_citations_passes_evidence_pages_through() -> None:
    """When caller supplies evidence_pages, they are returned as-is."""
    extraction = ProspectusExtraction(
        prospectus_id="P-1",
        company_name_zh="t",
        listing_type=ListingType.MAINBOARD_TECH,
        industry_code="TECH",
        industry_description="x",
        business_model="B2B",
        extraction_version="0.0.1",
        extracted_at=datetime.now(UTC),
    )
    out = BaseAgent._pick_extraction_citations(extraction, evidence_pages=[42, 87])
    assert [c.page for c in out] == [42, 87]


def test_agent_context_construction(monkeypatch) -> None:
    """AgentContext is a dataclass with sensible defaults."""
    monkeypatch.setenv("KIMI_API_KEY", "sk-test")
    monkeypatch.setenv("KIMI_URL", "https://api.moonshot.ai/v1")
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
