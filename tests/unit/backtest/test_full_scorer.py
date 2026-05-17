"""FullPipelineScorer unit tests — Phase 9b per ADR 0014.

DONE-conditions covered (~8 tests):
- ``_project_decision`` direction × confidence cases
- FullPipelineScorer skips on extraction LookAheadError
- FullPipelineScorer times out → SKIP score when skip_on_timeout=True
- FullPipelineScorer raises when skip_on_timeout=False
- make_fixture_extraction_fetcher returns identity

The full pipeline invocation is exercised in tests/e2e/test_full_pipeline_smoke.py
with a mock LLM client — keeping unit scope narrow here.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hk_ipo_agent.backtest.full_scorer import (
    DEFAULT_TIMEOUT_SECONDS,
    FullPipelineScorer,
    FullScorerConfig,
    _project_decision,
    make_fixture_extraction_fetcher,
)
from hk_ipo_agent.backtest.runner import BacktestInput
from hk_ipo_agent.common.enums import DecisionType, ListingType, RegulatoryRegime
from hk_ipo_agent.common.exceptions import LookAheadError
from hk_ipo_agent.common.schemas import (
    FinalDecision,
    ProspectusExtraction,
    ValuationDistribution,
)


def _extraction() -> ProspectusExtraction:
    return ProspectusExtraction(
        prospectus_id="P-TEST-1",
        company_name_zh="测试",
        listing_type=ListingType.MAINBOARD_TECH,
        industry_code="AI",
        industry_description="AI / SaaS",
        business_model="B2B",
        extraction_version="0.0.1",
        extracted_at=datetime.now(UTC),
    )


def _sample() -> BacktestInput:
    return BacktestInput(
        ipo_id=uuid.uuid4(),
        pricing_date=date(2024, 6, 14),
        stock_code="0001.HK",
        listing_type=ListingType.MAINBOARD_TECH,
        realized_returns={"5d": 0.05},
        cornerstone_count=0,
    )


def _decision(kind: DecisionType, confidence: float) -> FinalDecision:
    d = ValuationDistribution(
        p10=Decimal("90"),
        p25=Decimal("95"),
        p50=Decimal("100"),
        p75=Decimal("105"),
        p90=Decimal("110"),
        mean=Decimal("100"),
        std=Decimal("5"),
    )
    return FinalDecision(
        decision=kind,
        confidence=confidence,
        suggested_allocation_pct=0.02,
        price_range_low=Decimal("95"),
        price_range_fair=Decimal("100"),
        price_range_high=Decimal("105"),
        expected_return_6m=d,
        expected_return_12m=d,
        scorecard={"overall": 65.0},
    )


# ---------------------------------------------------------------------------
# _project_decision
# ---------------------------------------------------------------------------


def test_project_decision_participate_positive() -> None:
    score = _project_decision(_decision(DecisionType.PARTICIPATE, 0.8), [])
    assert score == pytest.approx(+0.8)


def test_project_decision_skip_negative() -> None:
    score = _project_decision(_decision(DecisionType.SKIP, 0.7), [])
    assert score == pytest.approx(-0.7)


def test_project_decision_partial_half_magnitude() -> None:
    score = _project_decision(_decision(DecisionType.PARTIAL, 0.6), [])
    assert score == pytest.approx(+0.30)


def test_project_decision_wait_zero() -> None:
    score = _project_decision(_decision(DecisionType.WAIT_FOR_SIGNAL, 0.9), [])
    assert score == 0.0


def test_project_decision_none_returns_zero_with_note() -> None:
    notes: list[str] = []
    assert _project_decision(None, notes) == 0.0
    assert any("no decision" in n for n in notes)


def test_project_decision_unrecognized_returns_zero_with_note() -> None:
    notes: list[str] = []
    bogus = MagicMock()
    bogus.decision = "not-a-decision-kind"
    assert _project_decision(bogus, notes) == 0.0
    assert any("unrecognized" in n for n in notes)


# ---------------------------------------------------------------------------
# FullScorerConfig
# ---------------------------------------------------------------------------


def test_default_timeout_is_30_minutes() -> None:
    """Per PROJECT_SPEC.md §13 wall-clock SLO."""
    assert DEFAULT_TIMEOUT_SECONDS == 30 * 60


def test_full_scorer_config_defaults_match_constants() -> None:
    cfg = FullScorerConfig()
    assert cfg.timeout_seconds == DEFAULT_TIMEOUT_SECONDS
    assert cfg.skip_on_timeout is True
    assert cfg.use_cache_regime is True


# ---------------------------------------------------------------------------
# make_fixture_extraction_fetcher
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fixture_fetcher_returns_identity() -> None:
    ext = _extraction()
    fetcher = make_fixture_extraction_fetcher(ext)
    # The provider arg is unused; pass None
    result = await fetcher(_sample(), None)  # type: ignore[arg-type]
    assert result is ext


# ---------------------------------------------------------------------------
# FullPipelineScorer.score — extraction LookAhead path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scorer_returns_skip_on_extraction_lookahead(fresh_sf) -> None:
    """Extraction fetcher raises LookAheadError → SKIP score + note."""
    from hk_ipo_agent.backtest.as_of_data import AsOfDataProvider

    async def _raising_fetcher(*args, **kwargs):
        raise LookAheadError("test")

    scorer = FullPipelineScorer(
        llm_client=MagicMock(),
        extraction_fetcher=_raising_fetcher,
    )
    provider = AsOfDataProvider(
        as_of_date=date(2024, 6, 13),
        session_factory=fresh_sf,
    )
    out = await scorer.score(provider, _sample())
    assert out.decision_score == -1.0
    assert any("LookAhead" in n for n in out.notes)


# ---------------------------------------------------------------------------
# FullPipelineScorer.score — timeout path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scorer_skips_on_timeout(fresh_sf) -> None:
    """When graph.ainvoke hangs and skip_on_timeout=True → -1.0 SKIP."""
    from hk_ipo_agent.backtest.as_of_data import AsOfDataProvider

    fake_graph = MagicMock()

    async def _hang(state):
        await asyncio.sleep(10)

    fake_graph.ainvoke = AsyncMock(side_effect=_hang)
    scorer = FullPipelineScorer(
        llm_client=MagicMock(),
        extraction_fetcher=make_fixture_extraction_fetcher(_extraction()),
        config=FullScorerConfig(timeout_seconds=0.05, skip_on_timeout=True),
    )
    provider = AsOfDataProvider(
        as_of_date=date(2024, 6, 13),
        session_factory=fresh_sf,
    )
    with patch(
        "hk_ipo_agent.orchestrator.graph.build_main_graph",
        return_value=fake_graph,
    ):
        out = await scorer.score(provider, _sample())
    assert out.decision_score == -1.0
    assert any("timed out" in n for n in out.notes)


@pytest.mark.asyncio
async def test_scorer_propagates_timeout_when_skip_disabled(fresh_sf) -> None:
    """skip_on_timeout=False → TimeoutError bubbles up."""
    from hk_ipo_agent.backtest.as_of_data import AsOfDataProvider

    fake_graph = MagicMock()

    async def _hang(state):
        await asyncio.sleep(10)

    fake_graph.ainvoke = AsyncMock(side_effect=_hang)
    scorer = FullPipelineScorer(
        llm_client=MagicMock(),
        extraction_fetcher=make_fixture_extraction_fetcher(_extraction()),
        config=FullScorerConfig(timeout_seconds=0.05, skip_on_timeout=False),
    )
    provider = AsOfDataProvider(
        as_of_date=date(2024, 6, 13),
        session_factory=fresh_sf,
    )
    with (
        patch(
            "hk_ipo_agent.orchestrator.graph.build_main_graph",
            return_value=fake_graph,
        ),
        pytest.raises(TimeoutError),
    ):
        await scorer.score(provider, _sample())


@pytest.mark.asyncio
async def test_scorer_extracts_decision_score_from_state(fresh_sf) -> None:
    """Happy path: graph returns a decision → scorer projects to score."""
    from hk_ipo_agent.backtest.as_of_data import AsOfDataProvider

    fake_graph = MagicMock()
    fake_graph.ainvoke = AsyncMock(
        return_value={"decision": _decision(DecisionType.PARTICIPATE, 0.75)}
    )
    scorer = FullPipelineScorer(
        llm_client=MagicMock(),
        extraction_fetcher=make_fixture_extraction_fetcher(_extraction()),
    )
    provider = AsOfDataProvider(
        as_of_date=date(2024, 6, 13),
        session_factory=fresh_sf,
    )
    with patch(
        "hk_ipo_agent.orchestrator.graph.build_main_graph",
        return_value=fake_graph,
    ):
        out = await scorer.score(provider, _sample())
    assert out.decision_score == pytest.approx(+0.75)
    assert out.regulatory_regime in {
        RegulatoryRegime.PRE_20250804,
        RegulatoryRegime.POST_20250804,
    }
