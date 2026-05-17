"""Tests for the Bull-Bear-Devil debate + Jaccard early-stop."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from hk_ipo_agent.common.enums import AgentRole, Confidence
from hk_ipo_agent.common.schemas import (
    AgentOutput,
    Citation,
    Finding,
    SingleModelValuation,
    ValuationDistribution,
    ValuationEnsembleOutput,
)
from hk_ipo_agent.critic.debate_graph import jaccard, run_debate, tokenize


def test_tokenize_ascii() -> None:
    tokens = tokenize("AI growth strong revenue")
    assert tokens == {"ai", "growth", "strong", "revenue"}


def test_tokenize_cjk_splits_chars() -> None:
    tokens = tokenize("人工智能")
    assert tokens == {"人", "工", "智", "能"}


def test_tokenize_mixed() -> None:
    tokens = tokenize("AI 智能 SaaS")
    assert "ai" in tokens
    assert "saas" in tokens
    assert "智" in tokens
    assert "能" in tokens


def test_jaccard_identical() -> None:
    assert jaccard("hello world", "hello world") == 1.0


def test_jaccard_disjoint() -> None:
    assert jaccard("abc def", "ghi jkl") == 0.0


def test_jaccard_partial_overlap() -> None:
    sim = jaccard("AI growth strong", "AI risk weak")
    # tokens: {ai, growth, strong} vs {ai, risk, weak}; intersect=1, union=5
    assert sim == pytest.approx(0.2)


def _stub_valuation() -> ValuationEnsembleOutput:
    dist = ValuationDistribution(
        p10=Decimal("100"),
        p25=Decimal("110"),
        p50=Decimal("120"),
        p75=Decimal("130"),
        p90=Decimal("140"),
        mean=Decimal("120"),
        std=Decimal("10"),
    )
    return ValuationEnsembleOutput(
        company_id="C-T",
        single_models=[
            SingleModelValuation(
                model_name="comparable",
                applicable=True,
                valuation_distribution=dist,
            )
        ],
        weights_used={"comparable": 1.0},
        ensemble_distribution=dist,
        implied_price_range={
            "low": Decimal("110"),
            "fair": Decimal("120"),
            "high": Decimal("130"),
        },
    )


def _stub_agent_outputs() -> dict[str, AgentOutput]:
    return {
        "fundamental": AgentOutput(
            agent_role=AgentRole.FUNDAMENTAL,
            scores={"business_quality": 75.0},
            overall_score=75.0,
            key_findings=[
                Finding(
                    statement="Strong CAGR",
                    evidence="35% over 3 years",
                    citations=[Citation(page=10)],
                    confidence=Confidence.HIGH,
                )
            ],
            runtime_seconds=1.0,
        ),
    }


@pytest.mark.asyncio
async def test_run_debate_converges_at_round_2(mock_llm_client, mock_llm_response) -> None:
    """R1-5: with high Jaccard, debate must run ≥ 2 rounds before early-stop.

    Pre-fix the loop body had ``converged = sim >= threshold and r >= 1``;
    ``r >= 1`` is always True, so any sim ≥ threshold halted at round 1.
    ADR 0010 §1 specifies "at least one bull/bear exchange" — exposing
    Bull's position to Bear's challenge requires r ≥ 2.
    """
    mock_llm_client._client.chat.completions.create = AsyncMock(
        return_value=mock_llm_response(text="同样的论点，AI growth 强劲，估值合理")
    )

    debate_out, cost = await run_debate(
        mock_llm_client,
        agent_outputs=_stub_agent_outputs(),
        valuation=_stub_valuation(),
        ipo_id="ipo-test",
        max_rounds=3,
        jaccard_threshold=0.6,
    )
    # FIXED behaviour: identical Bull/Bear → converge at round 2 (not 1).
    assert len(debate_out.rounds) == 2, (
        f"Expected debate to stop at round 2 (after one full exchange), got "
        f"{len(debate_out.rounds)}. If 1, R1-5 round-≥2 guard is missing."
    )
    assert debate_out.rounds[-1].resolution is not None
    assert debate_out.final_consensus
    assert cost >= Decimal("0")


@pytest.mark.asyncio
async def test_run_debate_does_not_stop_at_round_1_even_with_full_jaccard(
    mock_llm_client, mock_llm_response
) -> None:
    """R1-5 — explicit guard: round-1 cannot trigger convergence."""
    mock_llm_client._client.chat.completions.create = AsyncMock(
        return_value=mock_llm_response(text="完全一致的文本")
    )
    debate_out, _ = await run_debate(
        mock_llm_client,
        agent_outputs=_stub_agent_outputs(),
        valuation=_stub_valuation(),
        ipo_id="ipo-test",
        max_rounds=5,
        jaccard_threshold=0.6,
    )
    # First round: no resolution (because not converged yet).
    assert debate_out.rounds[0].resolution is None, (
        "Round 1 should not be marked as converged — even with perfect "
        "Jaccard, R1-5 requires r ≥ 2 before early-stop."
    )


@pytest.mark.asyncio
async def test_run_debate_clamps_max_rounds_to_spec_upper_bound(
    mock_llm_client, mock_llm_response
) -> None:
    """R1-5 — max_rounds is clamped to settings.debate_max_rounds (= 3)
    so callers cannot bypass the spec's hard 3-round upper bound.
    """
    counter = {"i": 0}

    def side_effect(*args, **kwargs):
        counter["i"] += 1
        return mock_llm_response(text=f"distinct_text_{counter['i']}_aaa_bbb_ccc_ddd_eee_fff")

    mock_llm_client._client.chat.completions.create = AsyncMock(side_effect=side_effect)

    debate_out, _ = await run_debate(
        mock_llm_client,
        agent_outputs=_stub_agent_outputs(),
        valuation=_stub_valuation(),
        ipo_id="ipo-test",
        max_rounds=10,  # caller tries to push past the spec cap
        jaccard_threshold=0.95,  # high → never converge organically
    )
    # Must be clamped to 3 even when caller asks for 10.
    assert len(debate_out.rounds) <= 3, (
        f"max_rounds clamp missing: requested 10, ran {len(debate_out.rounds)}. "
        f"R1-5 requires clamp to settings.debate_max_rounds (= 3)."
    )


@pytest.mark.asyncio
async def test_run_debate_runs_max_rounds_without_convergence(
    mock_llm_client, mock_llm_response
) -> None:
    """Bull / Bear diverge → no convergence → full max_rounds."""
    counter = {"i": 0}

    def side_effect(*args, **kwargs):
        counter["i"] += 1
        # Alternate completely different texts.
        return mock_llm_response(text=f"unique_response_{counter['i']}_xyz_qwerty_alpha_bravo")

    mock_llm_client._client.chat.completions.create = AsyncMock(side_effect=side_effect)
    debate_out, _cost = await run_debate(
        mock_llm_client,
        agent_outputs=_stub_agent_outputs(),
        valuation=_stub_valuation(),
        ipo_id="ipo-test",
        max_rounds=2,
        jaccard_threshold=0.9,
    )
    assert len(debate_out.rounds) == 2
    # Last round has resolution (max rounds reached → forced)
    assert debate_out.rounds[-1].resolution is not None


@pytest.mark.asyncio
async def test_run_debate_empty_max_rounds_zero(mock_llm_client, mock_llm_response) -> None:
    """max_rounds=0 produces empty debate."""
    mock_llm_client._client.chat.completions.create = AsyncMock(
        return_value=mock_llm_response(text="ignored")
    )
    debate_out, _cost = await run_debate(
        mock_llm_client,
        agent_outputs=_stub_agent_outputs(),
        valuation=_stub_valuation(),
        ipo_id="ipo-test",
        max_rounds=0,
        jaccard_threshold=0.6,
    )
    assert debate_out.rounds == []
    assert "No rounds" in debate_out.final_consensus
