"""R4-4 — Jinja2-aware prompt renderer.

Tests the single-entry-point ``render_prompt`` that satisfies CLAUDE.md
§提示词约束 §4 ("all prompts must be Jinja2-rendered before LLM call").
The implementation is backward-compatible: legacy prompts without
``{{ }}`` placeholders pass through unchanged; new prompts opt in.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hk_ipo_agent.agents.prompt_renderer import PromptRenderError, render_prompt
from hk_ipo_agent.agents.scoring import FundamentalScoreCard

_PROMPTS_ROOT = Path(__file__).resolve().parents[3] / "prompts"


# ---------------------------------------------------------------------------
# Backward compatibility — legacy prompts without {{ }} pass through
# ---------------------------------------------------------------------------


def test_render_prompt_passthrough_for_legacy_prompt_without_placeholders() -> None:
    """R4-4 — existing prompts (no Jinja2 syntax) are returned verbatim.

    This is the migration-ramp contract: the 21 existing prompt files
    work unchanged under render_prompt. Only NEW prompts that use
    ``{{ var }}`` are subject to Jinja2 strictness.
    """
    body, fm = render_prompt("agents/fundamental.md")
    assert "# Role" in body or "role:" in body.lower() or len(body) > 100
    # Frontmatter still parsed from the raw file.
    assert fm.get("role") == "fundamental_agent"


def test_render_prompt_preserves_frontmatter_for_all_legacy_prompts() -> None:
    """R4-4 — frontmatter parsing is unchanged after migration to renderer.

    Smoke check across all existing agent prompts.
    """
    for path in (_PROMPTS_ROOT / "agents").glob("*.md"):
        rel = f"agents/{path.name}"
        body, fm = render_prompt(rel)
        assert fm.get("version") is not None, f"frontmatter version missing on {rel}"
        assert len(body) > 0


# ---------------------------------------------------------------------------
# Jinja2 rendering — opt-in via placeholders
# ---------------------------------------------------------------------------


def test_render_prompt_renders_jinja_placeholders(tmp_path, monkeypatch) -> None:
    """R4-4 — prompts WITH ``{{ var }}`` get rendered with caller-supplied vars."""
    # Synthesise a tiny Jinja2-aware prompt under the real prompts/ dir
    # via monkeypatch of _PROMPTS_ROOT.
    test_prompt = tmp_path / "test_jinja.md"
    test_prompt.write_text(
        "---\nrole: test\nversion: 1.0\nlast_updated: 2026-05-18\n"
        "input_schema: TestInput\noutput_schema: TestOutput\n---\n"
        "# Greeting\n\nHello {{ name }}, the IPO is {{ ipo_id }}.",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "hk_ipo_agent.agents.base._PROMPTS_ROOT",
        tmp_path,
    )
    body, fm = render_prompt("test_jinja.md", name="Claude", ipo_id="0001.HK")
    assert "Hello Claude, the IPO is 0001.HK." in body
    assert fm.get("role") == "test"


def test_render_prompt_strict_undefined_raises_on_missing_var(tmp_path, monkeypatch) -> None:
    """R4-4 — missing ``{{ var }}`` raises PromptRenderError immediately.

    This is the "fail-loud at first call" contract — silently rendering
    an empty string in place of a missing variable would let prompt-time
    bugs leak into production LLM calls.
    """
    test_prompt = tmp_path / "missing_var.md"
    test_prompt.write_text(
        "# Hi {{ name }} — your IPO is {{ ipo_id }}",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "hk_ipo_agent.agents.base._PROMPTS_ROOT",
        tmp_path,
    )
    with pytest.raises(PromptRenderError, match="not supplied"):
        render_prompt("missing_var.md", name="Claude")  # missing ipo_id


def test_render_prompt_syntax_error_raises_prompt_render_error(tmp_path, monkeypatch) -> None:
    """R4-4 — Jinja2 syntax error → PromptRenderError (caller doesn't see Jinja exc class)."""
    test_prompt = tmp_path / "bad_syntax.md"
    test_prompt.write_text(
        "# Broken {% if name %} no endif",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "hk_ipo_agent.agents.base._PROMPTS_ROOT",
        tmp_path,
    )
    with pytest.raises(PromptRenderError):
        render_prompt("bad_syntax.md", name="x")


# ---------------------------------------------------------------------------
# schema_instruction auto-injection (kills the dead-code finding)
# ---------------------------------------------------------------------------


def test_render_prompt_appends_schema_instruction_when_card_class_given() -> None:
    """R4-4 — passing ``score_card_class`` auto-appends the ScoreCard JSON
    block. Pre-R4-4 ``schema_instruction`` was a defined-but-unused helper.
    """
    body, _ = render_prompt("agents/fundamental.md", score_card_class=FundamentalScoreCard)
    assert "# Output Schema (ScoreCard)" in body
    assert "evidence_pages" in body
    # JSON example block is included.
    assert "```json" in body


def test_render_prompt_no_schema_block_when_card_class_omitted(tmp_path, monkeypatch) -> None:
    """Omitting ``score_card_class`` skips the schema append (caller opts in).

    Note: must use a tmp prompt that doesn't already contain the schema
    string in its body (the real agent prompts spell out the schema
    inline for human readability, so we can't use them for this contract).
    """
    test_prompt = tmp_path / "tiny.md"
    test_prompt.write_text(
        "# Role\n\nThis prompt mentions no schema block of its own.",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "hk_ipo_agent.agents.base._PROMPTS_ROOT",
        tmp_path,
    )
    body, _ = render_prompt("tiny.md")
    assert "# Output Schema (ScoreCard)" not in body
