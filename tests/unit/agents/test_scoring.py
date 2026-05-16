"""Tests for ``agents.scoring`` helpers + ScoreCards."""

from __future__ import annotations

import pytest

from hk_ipo_agent.agents.scoring import (
    BaseScoreCard,
    CornerstoneScoreCard,
    FundamentalScoreCard,
    IndustryScoreCard,
    LiquidityScoreCard,
    PolicyScoreCard,
    SentimentScoreCard,
    ValuationScoreCard,
    extract_json_block,
    schema_instruction,
    strip_json_blocks,
)


def test_extract_json_block_finds_fenced_block() -> None:
    text = "Some analysis here\n```json\n{\"a\": 1, \"b\": 2}\n```\nAnd more."
    assert extract_json_block(text) == {"a": 1, "b": 2}


def test_extract_json_block_returns_none_when_missing() -> None:
    assert extract_json_block("no json here") is None


def test_extract_json_block_returns_none_on_malformed() -> None:
    text = "```json\n{not valid}\n```"
    assert extract_json_block(text) is None


def test_strip_json_blocks_removes_fences() -> None:
    text = "head\n```json\n{\"a\": 1}\n```\ntail"
    assert "head" in strip_json_blocks(text)
    assert "tail" in strip_json_blocks(text)
    assert "{" not in strip_json_blocks(text)


def test_schema_instruction_for_policy_card() -> None:
    instr = schema_instruction(PolicyScoreCard)
    assert "regime_fit" in instr
    assert "policy_tailwind" in instr
    assert "regime_score" in instr
    assert "```json" in instr


def test_base_score_card_overall_averages_numeric_fields() -> None:
    class _Test(BaseScoreCard):
        a: float = 60.0
        b: float = 80.0

    card = _Test()
    assert card.overall() == pytest.approx(70.0)
    assert card.score_dict() == {"a": 60.0, "b": 80.0}


def test_fundamental_card_validates_bounds() -> None:
    from pydantic import ValidationError  # noqa: PLC0415 — local for ruff B017 fix

    FundamentalScoreCard(business_quality=50.0, financial_health=50.0, governance=50.0)
    with pytest.raises(ValidationError):
        FundamentalScoreCard(business_quality=150.0, financial_health=50.0, governance=50.0)


def test_policy_card_allows_negative_regime_score() -> None:
    card = PolicyScoreCard(regime_fit=70.0, policy_tailwind=60.0, regime_score=-25.0)
    assert card.regime_score == -25.0


def test_all_cards_have_evidence_pages_and_notes() -> None:
    for cls in (
        FundamentalScoreCard,
        IndustryScoreCard,
        ValuationScoreCard,
        PolicyScoreCard,
        LiquidityScoreCard,
        CornerstoneScoreCard,
        SentimentScoreCard,
    ):
        kwargs = {
            name: 50.0
            for name, info in cls.model_fields.items()
            if name not in {"evidence_pages", "notes"}
        }
        inst = cls(**kwargs)
        assert hasattr(inst, "evidence_pages")
        assert hasattr(inst, "notes")
