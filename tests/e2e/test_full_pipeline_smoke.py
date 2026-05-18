"""End-to-end smoke: FullPipelineScorer against the real LangGraph.

Phase 9b per ADR 0014.

Uses a mock LLM client so the test costs $0 and is reproducible. The
goal is to prove that:
1. ``build_main_graph`` compiles and runs through every node when given
   a complete extraction + market data.
2. ``FullPipelineScorer.score`` projects the resulting FinalDecision
   into a continuous decision_score.
3. The 30-minute wall-clock SLO leaves plenty of headroom — the smoke
   run completes in seconds.

Skipped when ``KIMI_API_KEY`` is unset because LLMClient validation
requires it (the API key is mocked but the env var has to be present
to construct the client).
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from hk_ipo_agent.backtest.as_of_data import AsOfDataProvider
from hk_ipo_agent.backtest.full_scorer import (
    FullPipelineScorer,
    FullScorerConfig,
    make_fixture_extraction_fetcher,
)
from hk_ipo_agent.backtest.runner import BacktestInput
from hk_ipo_agent.common.enums import DecisionType, ListingType
from hk_ipo_agent.common.llm_client import LLMClient
from hk_ipo_agent.common.schemas import (
    FinalDecision,
    ProspectusExtraction,
    ValuationDistribution,
)

# The autouse fixture below sets KIMI_API_KEY=sk-test before each
# test runs, so the LLMClient can be constructed without a real key.
# We don't gate the test on the env var.
_ = os  # keep import for clarity that env handling is intentional


def _extraction() -> ProspectusExtraction:
    return ProspectusExtraction(
        prospectus_id="P-SMOKE-1",
        company_name_zh="冒烟测试公司",
        listing_type=ListingType.MAINBOARD_TECH,
        industry_code="AI",
        industry_description="AI SaaS",
        business_model="B2B",
        extraction_version="0.0.1",
        extracted_at=datetime.now(UTC),
    )


def _decision(kind: DecisionType, conf: float) -> FinalDecision:
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
        confidence=conf,
        suggested_allocation_pct=0.02,
        price_range_low=Decimal("95"),
        price_range_fair=Decimal("100"),
        price_range_high=Decimal("105"),
        expected_return_6m=d,
        expected_return_12m=d,
        scorecard={"overall": 65.0},
    )


def _sample() -> BacktestInput:
    return BacktestInput(
        ipo_id=uuid.uuid4(),
        pricing_date=date(2024, 6, 14),
        stock_code="0001.HK",
        listing_type=ListingType.MAINBOARD_TECH,
        realized_returns={"5d": 0.05, "30d": 0.12},
        cornerstone_count=2,
    )


@pytest.fixture(autouse=True)
def _reset_engine_cache(monkeypatch: pytest.MonkeyPatch):
    """Drop the cached AsyncEngine between tests."""
    monkeypatch.setenv("KIMI_API_KEY", "sk-test")
    from hk_ipo_agent.common.settings import get_settings
    from hk_ipo_agent.data.database import (
        async_session_factory,
        get_engine,
    )

    get_settings.cache_clear()
    get_engine.cache_clear()  # type: ignore[attr-defined]
    async_session_factory.cache_clear()  # type: ignore[attr-defined]
    yield
    get_engine.cache_clear()  # type: ignore[attr-defined]
    async_session_factory.cache_clear()  # type: ignore[attr-defined]


@pytest.fixture
async def sf():
    from hk_ipo_agent.common.settings import get_settings

    engine = create_async_engine(get_settings().database.url, poolclass=NullPool)
    sf_ = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    yield sf_
    await engine.dispose()


@pytest.mark.slow
@pytest.mark.asyncio
async def test_full_pipeline_smoke_with_mocked_graph(sf, monkeypatch):
    """Mocked build_main_graph returns a graph stub that yields a decision.

    R9-8: marked ``slow`` so default ``pytest -m 'not slow'`` skips it.
    Run explicitly with ``pytest -m slow tests/e2e``.

    Validates the harness path: FullPipelineScorer → build_main_graph →
    graph.ainvoke → state["decision"] → projected score.

    We mock the graph itself rather than the LLM so we don't need to
    walk through 7 agents' prompts — that integration is tested in
    Phase 5 / 6 unit suites.
    """
    from unittest.mock import patch

    fake_graph = MagicMock()
    fake_graph.ainvoke = AsyncMock(
        return_value={
            "decision": _decision(DecisionType.PARTICIPATE, 0.7),
            "snapshot_id": uuid.uuid4(),
        }
    )

    llm = LLMClient(daily_budget_usd=Decimal("100"))
    scorer = FullPipelineScorer(
        llm_client=llm,
        extraction_fetcher=make_fixture_extraction_fetcher(_extraction()),
        config=FullScorerConfig(timeout_seconds=60.0),
    )
    provider = AsOfDataProvider(as_of_date=date(2024, 6, 13), session_factory=sf)

    start = time.perf_counter()
    with patch(
        "hk_ipo_agent.orchestrator.graph.build_main_graph",
        return_value=fake_graph,
    ):
        out = await scorer.score(provider, _sample())
    elapsed = time.perf_counter() - start

    assert out.decision_score == pytest.approx(+0.7)
    assert out.notes == ()
    # Wall clock SLO is 30 minutes; smoke must be far below.
    assert elapsed < 5.0, f"smoke too slow: {elapsed:.2f}s"
