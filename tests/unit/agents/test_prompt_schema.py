"""Prompt frontmatter + handwritten-JSON-vs-Pydantic-ScoreCard consistency
tests per ADR 0019.

Three categories of assertions:

1. ``test_frontmatter_validates_for_all_agent_prompts``
   — every `prompts/agents/*.md` parses through `PromptFrontmatter` Pydantic
2. ``test_score_card_handwritten_json_matches_pydantic``
   — the fenced JSON in the `# Output Schema (ScoreCard)` block of each card
     has exactly the same field set as the declared `BaseScoreCard` subclass
3. ``test_requires_extras_match_workflow_extras_fields``
   — every `requires_extras:` key is a real `WorkflowExtras` dataclass field
     (defense in depth — the Pydantic validator already enforces this on
     load, but we add a dedicated test for visibility / regression)
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

import pytest

from hk_ipo_agent.agents import scoring
from hk_ipo_agent.agents.base import PromptFrontmatter, load_prompt
from hk_ipo_agent.agents.scoring import BaseScoreCard
from hk_ipo_agent.agents.workflow_extras import WorkflowExtras

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PROMPTS_AGENTS = _REPO_ROOT / "prompts" / "agents"

# Frontmatter `score_card:` value → Pydantic class lookup. Source of truth
# is scoring.py module attributes.
_SCORE_CARD_CLASS: dict[str, type[BaseScoreCard]] = {
    "FundamentalScoreCard": scoring.FundamentalScoreCard,
    "IndustryScoreCard": scoring.IndustryScoreCard,
    "ValuationScoreCard": scoring.ValuationScoreCard,
    "PolicyScoreCard": scoring.PolicyScoreCard,
    "LiquidityScoreCard": scoring.LiquidityScoreCard,
    "CornerstoneScoreCard": scoring.CornerstoneScoreCard,
    "SentimentScoreCard": scoring.SentimentScoreCard,
}


def _agent_prompt_paths() -> list[str]:
    """Return relative-to-prompts/ paths of every agents/*.md card."""
    return [f"agents/{p.name}" for p in sorted(_PROMPTS_AGENTS.glob("*.md"))]


_FENCED_JSON_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def _first_json_block(text: str) -> dict | None:
    """Return the first fenced ```json``` block parsed as dict, or None."""
    m = _FENCED_JSON_RE.search(text)
    if not m:
        return None
    try:
        result = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    return result if isinstance(result, dict) else None


# ---------------------------------------------------------------------------
# Test 1: frontmatter Pydantic validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prompt_path", _agent_prompt_paths())
def test_frontmatter_validates_for_all_agent_prompts(prompt_path: str) -> None:
    """Each agents/*.md frontmatter passes PromptFrontmatter Pydantic schema."""
    _, fm = load_prompt(prompt_path, validate=True)
    # validate=True raises on failure; if we get here, it passed.
    # Sanity: required fields present and shape correct.
    parsed = PromptFrontmatter.model_validate(fm)
    assert parsed.role
    assert parsed.version
    assert parsed.input_schema == "AgentContext"
    assert parsed.output_schema == "AgentOutput"


# ---------------------------------------------------------------------------
# Test 2: handwritten JSON example matches Pydantic ScoreCard field set
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prompt_path", _agent_prompt_paths())
def test_score_card_handwritten_json_matches_pydantic(prompt_path: str) -> None:
    """Each card's `# Output Schema (ScoreCard)` JSON example uses exactly
    the same field set as its declared Pydantic ScoreCard class.

    Catches drift: if scoring.py adds/removes a ScoreCard field, the prompt
    `.md` JSON example must be updated in lockstep (ADR 0019 §5).
    """
    body, fm = load_prompt(prompt_path, validate=True)
    score_card_name = fm.get("score_card")
    assert score_card_name, f"{prompt_path}: missing `score_card:` frontmatter"
    card_cls = _SCORE_CARD_CLASS.get(score_card_name)
    assert card_cls is not None, f"{prompt_path}: unknown score_card {score_card_name!r}"

    # Find the first JSON block (the schema example; later Few-shot example
    # blocks are sometimes present but the FIRST one is the schema canonical).
    example = _first_json_block(body)
    assert example is not None, f"{prompt_path}: no fenced ```json``` block found in body"

    expected_keys = set(card_cls.model_fields.keys())
    actual_keys = set(example.keys())

    missing_in_example = expected_keys - actual_keys
    extra_in_example = actual_keys - expected_keys
    assert not missing_in_example, (
        f"{prompt_path}: ScoreCard JSON example is missing fields "
        f"{missing_in_example} declared on {score_card_name}"
    )
    assert not extra_in_example, (
        f"{prompt_path}: ScoreCard JSON example has unknown fields "
        f"{extra_in_example} not on {score_card_name}"
    )


# ---------------------------------------------------------------------------
# Test 3: requires_extras keys must be real WorkflowExtras fields
# ---------------------------------------------------------------------------


def _workflow_extras_field_names() -> set[str]:
    return {f.name for f in dataclasses.fields(WorkflowExtras)}


@pytest.mark.parametrize("prompt_path", _agent_prompt_paths())
def test_requires_extras_match_workflow_extras_fields(prompt_path: str) -> None:
    """Each `requires_extras:` key is a real `WorkflowExtras` dataclass field
    (defense in depth — Pydantic validator already enforces this, but a
    dedicated test gives visibility on regression)."""
    _, fm = load_prompt(prompt_path, validate=True)
    required: list[str] = fm.get("requires_extras", []) or []
    valid = _workflow_extras_field_names()
    unknown = [k for k in required if k not in valid]
    assert not unknown, (
        f"{prompt_path}: requires_extras contains keys not on WorkflowExtras: "
        f"{unknown}. Valid keys: {sorted(valid)}"
    )


# ---------------------------------------------------------------------------
# Test 4: agent_common include actually rendered (smoke test)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prompt_path", _agent_prompt_paths())
def test_agent_common_include_rendered(prompt_path: str) -> None:
    """Every card uses `{% include "system/agent_common.md" %}` and the
    public Citation rules show up in the rendered body. Per ADR 0019 §4."""
    body, _ = load_prompt(prompt_path, validate=True)
    # Marker from prompts/system/agent_common.md
    assert "Citation 约束（全局强制）" in body, (
        f"{prompt_path}: agent_common.md include did not render"
    )
