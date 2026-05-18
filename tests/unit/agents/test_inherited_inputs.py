"""R4-7 — inherited_inputs frontmatter contract enforcement.

Pre-R4-7 the agent prompt frontmatter listed e.g.
``inherited_inputs: [regime_score, regulatory_regime]`` but BaseAgent
never validated those keys were actually populated by upstream tools
before the LLM call. R4-7 adds ``_verify_inherited_inputs`` so a missing
upstream dispatch fails loud (MissingInheritedInputError) instead of
silently producing a low-quality LLM call.

See docs/PLAN_post_v1.0.md §6 R4-7.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import MagicMock

import pytest

from hk_ipo_agent.agents.base import AgentContext, BaseAgent
from hk_ipo_agent.agents.workflow_extras import WorkflowExtras
from hk_ipo_agent.common.enums import ListingType
from hk_ipo_agent.common.exceptions import MissingInheritedInputError
from hk_ipo_agent.common.schemas import ProspectusExtraction
from hk_ipo_agent.valuation.base import MarketData


def _ctx(extras: WorkflowExtras, kb_tool: object | None = None) -> AgentContext:
    """Construct a minimal AgentContext for the verify helper.

    Uses a MagicMock for llm_client to avoid LLMClient.__init__ requiring
    KIMI_API_KEY — this test exercises only the verify_inherited_inputs
    helper which never touches the LLM.
    """
    ext = ProspectusExtraction(
        prospectus_id="P-IH-1",
        company_name_zh="测试",
        listing_type=ListingType.MAINBOARD_TECH,
        industry_code="TECH",
        industry_description="AI",
        business_model="B2B",
        extraction_version="0.0.1",
        extracted_at=datetime.now(UTC),
    )
    md = MarketData(as_of_date=date(2026, 5, 18), listing_type=ListingType.MAINBOARD_TECH)
    ctx = AgentContext(
        ipo_id="ipo-1",
        extraction=ext,
        market_data=md,
        llm_client=MagicMock(),  # _verify_inherited_inputs never calls LLM
        extras=extras,
    )
    if kb_tool is not None:
        ctx.kb_tool = kb_tool
    return ctx


def test_verify_inherited_inputs_no_op_when_frontmatter_lacks_field() -> None:
    """R4-7 — agents without inherited_inputs declared in frontmatter pass."""
    extras = WorkflowExtras()
    BaseAgent._verify_inherited_inputs({}, _ctx(extras))
    BaseAgent._verify_inherited_inputs({"role": "x"}, _ctx(extras))  # other keys irrelevant
    BaseAgent._verify_inherited_inputs({"inherited_inputs": []}, _ctx(extras))


def test_verify_inherited_inputs_passes_when_typed_field_populated() -> None:
    """R4-7 — declared input resolved via WorkflowExtras typed field."""
    extras = WorkflowExtras(regime_score=0.12)
    BaseAgent._verify_inherited_inputs(
        {"inherited_inputs": ["regime_score"]},
        _ctx(extras),
    )


def test_verify_inherited_inputs_raises_when_typed_field_none() -> None:
    """R4-7 — declared input None on extras → MissingInheritedInputError."""
    extras = WorkflowExtras()  # regime_score defaults to None
    with pytest.raises(MissingInheritedInputError, match="regime_score"):
        BaseAgent._verify_inherited_inputs(
            {"inherited_inputs": ["regime_score"]},
            _ctx(extras),
        )


def test_verify_inherited_inputs_raises_when_list_empty() -> None:
    """R4-7 — empty list is "missing" (the agent intends to consume data,
    so an empty list means upstream didn't populate)."""
    extras = WorkflowExtras()  # cornerstone_profiles defaults to []
    with pytest.raises(MissingInheritedInputError, match="cornerstone_profiles"):
        BaseAgent._verify_inherited_inputs(
            {"inherited_inputs": ["cornerstone_profiles"]},
            _ctx(extras),
        )


def test_verify_inherited_inputs_uses_alias_for_renamed_fields() -> None:
    """R4-7 — frontmatter declares 'sponsor_track_record' (singular) but
    extras has plural 'sponsor_track_records'; alias map bridges the gap."""
    extras = WorkflowExtras(
        sponsor_track_records=[{"sponsor": "A", "win_rate_24m": 0.6}],
    )
    BaseAgent._verify_inherited_inputs(
        {"inherited_inputs": ["sponsor_track_record"]},
        _ctx(extras),
    )


def test_verify_inherited_inputs_uses_alias_for_ai_gilding_signal() -> None:
    """R4-7 — frontmatter declares 'ai_gilding_signal'; extras has 'ai_gilding_flag'.

    Note: ai_gilding_flag is a bool defaulting to False. The alias mapping
    lets the verify helper see "the upstream signal was produced" (even
    if value is False — that's a valid populated state).
    """
    extras = WorkflowExtras(ai_gilding_flag=True)
    BaseAgent._verify_inherited_inputs(
        {"inherited_inputs": ["ai_gilding_signal"]},
        _ctx(extras),
    )


def test_verify_inherited_inputs_falls_back_to_misc_dict() -> None:
    """R4-7 — keys not in typed WorkflowExtras may live in ``misc``."""
    extras = WorkflowExtras()
    extras.set("regulatory_regime", "pre-2025-08-04")
    BaseAgent._verify_inherited_inputs(
        {"inherited_inputs": ["regulatory_regime"]},
        _ctx(extras),
    )


def test_verify_inherited_inputs_falls_back_to_kb_tool() -> None:
    """R4-7 — keys may resolve via ``ctx.kb_tool`` (e.g. theme_history_30d)."""
    extras = WorkflowExtras()
    kb = MagicMock()
    kb.theme_history_30d = {"some": "history"}
    BaseAgent._verify_inherited_inputs(
        {"inherited_inputs": ["theme_history_30d"]},
        _ctx(extras, kb_tool=kb),
    )


def test_verify_inherited_inputs_reports_all_missing_at_once() -> None:
    """R4-7 — error message lists ALL missing keys (not just the first)."""
    extras = WorkflowExtras()  # all NACS signals are None / empty
    with pytest.raises(MissingInheritedInputError) as exc_info:
        BaseAgent._verify_inherited_inputs(
            {
                "inherited_inputs": [
                    "regime_score",
                    "cluster_bonus_multiplier",
                    "theme_heat",
                ]
            },
            _ctx(extras),
        )
    msg = str(exc_info.value)
    assert "regime_score" in msg
    assert "cluster_bonus_multiplier" in msg
    assert "theme_heat" in msg


def test_verify_inherited_inputs_real_policy_prompt() -> None:
    """R4-7 — running the verify against the real policy prompt's
    frontmatter requires regime_score + regulatory_regime to be present.
    With both populated, no raise."""
    from hk_ipo_agent.agents.base import load_prompt

    _body, fm = load_prompt("agents/policy.md")
    # Sanity: real frontmatter has both keys.
    inputs = fm.get("inherited_inputs") or []
    assert "regime_score" in inputs
    assert "regulatory_regime" in inputs

    extras = WorkflowExtras(regime_score=0.05)
    extras.set("regulatory_regime", "post-2025-08-04")
    BaseAgent._verify_inherited_inputs(fm, _ctx(extras))


def test_verify_inherited_inputs_real_policy_prompt_raises_on_missing() -> None:
    """R4-7 — same prompt but neither extras nor kb_tool populated → raise."""
    from hk_ipo_agent.agents.base import load_prompt

    _body, fm = load_prompt("agents/policy.md")
    with pytest.raises(MissingInheritedInputError, match="regime_score"):
        BaseAgent._verify_inherited_inputs(fm, _ctx(WorkflowExtras()))
