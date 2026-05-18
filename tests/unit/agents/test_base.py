"""Tests for ``agents.base`` — load_prompt + AgentContext + ADR 0019 assert."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from hk_ipo_agent.agents.base import (
    AgentContext,
    BaseAgent,
    PromptFrontmatter,
    load_prompt,
)
from hk_ipo_agent.agents.prompt_renderer import render_prompt
from hk_ipo_agent.agents.scoring import PolicyScoreCard
from hk_ipo_agent.agents.workflow_extras import WorkflowExtras
from hk_ipo_agent.common.enums import AgentRole, ListingType
from hk_ipo_agent.common.exceptions import (
    CitationRequiredError,
    MissingInheritedInput,
)
from hk_ipo_agent.common.llm_client import LLMClient
from hk_ipo_agent.common.schemas import AgentOutput, ProspectusExtraction
from hk_ipo_agent.valuation.base import MarketData


def test_load_prompt_parses_frontmatter() -> None:
    body, fm = load_prompt("agents/policy.md")
    assert "Role" in body or "role" in body
    assert fm.get("role") == "policy_agent"
    assert fm.get("version") == "1.3"
    # ADR 0019: requires_extras (hard-asserted) vs inherited_inputs (doc-only)
    assert isinstance(fm.get("requires_extras"), list)
    assert "regime_score" in fm["requires_extras"]
    assert isinstance(fm.get("inherited_inputs"), list)
    assert "regulatory_regime" in fm["inherited_inputs"]


def test_load_prompt_returns_full_body_when_no_frontmatter() -> None:
    # Use a tmp-less round trip via re-loading agents/fundamental.md (has FM).
    body, fm = load_prompt("agents/fundamental.md")
    assert body.strip().startswith("# Role")
    assert fm.get("role") == "fundamental_agent"


def test_pick_extraction_citations_raises_when_no_evidence_anywhere() -> None:
    """R1-3 — base.py sham citation fallback must be removed.

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


def test_render_prompt_jinja_include_renders_agent_common() -> None:
    """ADR 0019 §4: every agents/*.md card includes system/agent_common.md
    via Jinja2 and the public Citation rules show up in rendered body.

    Note: load_prompt returns raw body (no Jinja2). The Jinja2-aware path
    is render_prompt (R4-4) which detects ``{% include %}`` and renders.
    """
    body, _ = render_prompt("agents/fundamental.md")
    assert "Citation 约束（全局强制）" in body


def test_load_prompt_validate_mode_passes_for_all_cards() -> None:
    """validate=True runs PromptFrontmatter Pydantic — should pass for v1.3."""
    for card in [
        "agents/fundamental.md",
        "agents/industry.md",
        "agents/valuation.md",
        "agents/policy.md",
        "agents/liquidity.md",
        "agents/cornerstone_signal.md",
        "agents/sentiment.md",
    ]:
        _, fm = load_prompt(card, validate=True)
        # Sanity: PromptFrontmatter accepted the dict
        PromptFrontmatter.model_validate(fm)


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


# ---------------------------------------------------------------------------
# ADR 0019 §3: `_assert_required_extras` hard edge — must raise
# `MissingInheritedInput` when frontmatter declared key is None on ctx.extras
# ---------------------------------------------------------------------------


class _PolicyAgentForTest(BaseAgent):
    """Minimal BaseAgent subclass for testing _assert_required_extras.

    Bound to `agents/policy.md` which declares `requires_extras: [regime_score]`.
    """

    role = AgentRole.POLICY
    prompt_path = "agents/policy.md"
    score_card_class = PolicyScoreCard

    async def run(self, ctx: AgentContext) -> AgentOutput:
        # Not used in these tests; we only exercise _assert_required_extras.
        raise NotImplementedError


def _make_ctx(monkeypatch, extras: WorkflowExtras | None = None) -> AgentContext:
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
    return AgentContext(
        ipo_id="ipo-test",
        extraction=ext,
        market_data=md,
        llm_client=client,
        extras=extras if extras is not None else WorkflowExtras(),
    )


def test_assert_required_extras_raises_when_missing(monkeypatch) -> None:
    """Per ADR 0019: regime_score=None on ctx.extras must raise
    MissingInheritedInput (not silently degrade)."""
    # Reset class-level frontmatter cache so this test class loads fresh.
    _PolicyAgentForTest._cached_frontmatter = None
    agent = _PolicyAgentForTest()
    ctx = _make_ctx(monkeypatch, extras=WorkflowExtras())  # regime_score=None default

    with pytest.raises(MissingInheritedInput) as exc_info:
        agent._assert_required_extras(ctx)
    assert "regime_score" in str(exc_info.value)
    assert exc_info.value.context.get("agent_role") == "policy"
    assert exc_info.value.context.get("missing_keys") == ["regime_score"]


def test_assert_required_extras_passes_when_present(monkeypatch) -> None:
    """Per ADR 0019: regime_score set on ctx.extras → assertion passes."""
    _PolicyAgentForTest._cached_frontmatter = None
    agent = _PolicyAgentForTest()
    extras = WorkflowExtras()
    extras.regime_score = 0.18  # NACS Regime Gate positive
    ctx = _make_ctx(monkeypatch, extras=extras)

    # Should not raise.
    agent._assert_required_extras(ctx)
