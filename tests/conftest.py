"""Shared pytest fixtures per PROJECT_SPEC.md §9.

Available fixtures:
- ``mock_llm_client``       — `LLMClient` whose OpenAI AsyncClient is mocked.
- ``mock_llm_response``     — factory producing fake OpenAI ChatCompletion responses.
- ``sample_citation``       — a simple `Citation(page=42)`.
- ``sample_finding``        — a single `Finding` with citation.
- ``sample_extraction``     — minimal valid `ProspectusExtraction`.
- ``sample_ipo_event``      — un-persisted `IPOEvent` ORM instance.
- ``sample_cornerstone``    — un-persisted `CornerstoneInvestor` ORM instance.
- ``sample_decision``       — minimal valid `FinalDecision`.
- ``sample_valuation_distribution`` — `ValuationDistribution` stub.
- ``sample_agent_output``   — minimal valid `AgentOutput`.
- ``settings_override``     — factory that overrides `get_settings()` for one test.
- ``frozen_now``            — pin `utcnow()` to a fixed timestamp for deterministic tests.

Live DB / Qdrant / Redis fixtures arrive in Phase 2 (data layer) and Phase 3
(prospectus pipeline) — see ADR 0005 §4 and ADR 0006 §Progress.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from hk_ipo_agent.common import utils as utils_mod
from hk_ipo_agent.common.enums import (
    AgentRole,
    Confidence,
    DecisionType,
    ListingType,
)
from hk_ipo_agent.common.llm_client import LLMClient
from hk_ipo_agent.common.schemas import (
    AgentOutput,
    Citation,
    DebateOutput,
    FinalDecision,
    Finding,
    ProspectusExtraction,
    ValuationDistribution,
    ValuationEnsembleOutput,
)
from hk_ipo_agent.common.settings import Settings, get_settings
from hk_ipo_agent.data.models import CornerstoneInvestor, IPOEvent

# ---------------------------------------------------------------------------
# LLM mocks
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_llm_response() -> Callable[..., MagicMock]:
    """Factory: build a mock OpenAI ChatCompletion response object.

    Example:
        resp = mock_llm_response(text="hello", in_tokens=10, out_tokens=20)
    """

    def _factory(
        *,
        text: str = "ok",
        in_tokens: int = 10,
        out_tokens: int = 20,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
        stop_reason: str = "stop",
        request_id: str = "chatcmpl-test",
    ) -> MagicMock:
        response = MagicMock()
        response.id = request_id
        response.usage = MagicMock(
            prompt_tokens=in_tokens,
            completion_tokens=out_tokens,
        )
        message = MagicMock()
        message.content = text
        choice = MagicMock()
        choice.message = message
        choice.finish_reason = stop_reason
        response.choices = [choice]
        return response

    return _factory


@pytest.fixture
def mock_llm_client(
    monkeypatch: pytest.MonkeyPatch,
    mock_llm_response: Callable[..., MagicMock],
) -> Iterator[LLMClient]:
    """A `LLMClient` with `chat.completions.create` patched to a no-op AsyncMock.

    Tests should override `client._client.chat.completions.create` to a custom AsyncMock
    when they need to control the response.

    R9-9: yields (instead of plain return) so a teardown step explicitly
    resets the cost log on the returned client — pre-fix a leaked
    in-memory cost from one test could survive into another via the
    LLMClient's process-wide cost_log singleton if a future refactor
    moves it module-level. ``monkeypatch.setenv`` already auto-restores
    env vars at teardown; the yield gives us a place to also reset any
    client-side mutable state we install during construction.
    """
    monkeypatch.setenv("KIMI_API_KEY", "sk-test-fixture")
    monkeypatch.setenv("KIMI_URL", "https://api.moonshot.ai/v1")
    client = LLMClient(daily_budget_usd=Decimal("100"))
    default_create: Any = AsyncMock(return_value=mock_llm_response())
    client._client.chat.completions.create = default_create  # type: ignore[attr-defined]
    try:
        yield client
    finally:
        # R9-9: reset any in-test mutations to client state. The cost_log
        # is the load-bearing instance attribute downstream tests read;
        # zero it out so no leaked spend hangs around.
        client.cost_log.records.clear()


# ---------------------------------------------------------------------------
# Domain Pydantic stubs
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_citation() -> Citation:
    return Citation(page=42, section="财务摘要", chunk_id="c-1")


@pytest.fixture
def sample_finding(sample_citation: Citation) -> Finding:
    return Finding(
        statement="Revenue grew 50% YoY",
        evidence="Page 42, financial highlights",
        citations=[sample_citation],
        confidence=Confidence.HIGH,
    )


@pytest.fixture
def sample_valuation_distribution() -> ValuationDistribution:
    return ValuationDistribution(
        p10=Decimal("10.00"),
        p25=Decimal("11.50"),
        p50=Decimal("13.00"),
        p75=Decimal("14.50"),
        p90=Decimal("16.00"),
        mean=Decimal("13.10"),
        std=Decimal("1.80"),
    )


@pytest.fixture
def sample_agent_output(sample_finding: Finding) -> AgentOutput:
    return AgentOutput(
        agent_role=AgentRole.FUNDAMENTAL,
        scores={"business_quality": 75.0, "financial_health": 68.0},
        overall_score=72.0,
        key_findings=[sample_finding],
        cost_usd=Decimal("0.02"),
        runtime_seconds=3.5,
    )


@pytest.fixture
def sample_decision(
    sample_valuation_distribution: ValuationDistribution,
) -> FinalDecision:
    return FinalDecision(
        decision=DecisionType.PARTICIPATE,
        confidence=0.78,
        suggested_allocation_pct=0.04,
        price_range_low=Decimal("10.00"),
        price_range_fair=Decimal("13.00"),
        price_range_high=Decimal("16.00"),
        expected_return_6m=sample_valuation_distribution,
        expected_return_12m=sample_valuation_distribution,
        key_reasons_for=["Strong fundamentals", "Quality cornerstone roster"],
        key_reasons_against=["High customer concentration"],
    )


@pytest.fixture
def sample_valuation_ensemble(
    sample_valuation_distribution: ValuationDistribution,
) -> ValuationEnsembleOutput:
    return ValuationEnsembleOutput(
        company_id="C-TEST",
        single_models=[],
        weights_used={"comparable": 0.5, "dcf": 0.5},
        ensemble_distribution=sample_valuation_distribution,
        implied_price_range={
            "low": Decimal("10.00"),
            "fair": Decimal("13.00"),
            "high": Decimal("16.00"),
        },
    )


@pytest.fixture
def sample_debate_output() -> DebateOutput:
    return DebateOutput(final_consensus="Net positive; participate at floor.")


@pytest.fixture
def sample_extraction() -> ProspectusExtraction:
    """A minimal valid ProspectusExtraction for unit / integration tests."""
    return ProspectusExtraction(
        prospectus_id="P-FIXTURE-1",
        company_name_zh="测试科技有限公司",
        company_name_en="Test Tech Co., Ltd.",
        listing_type=ListingType.CH18C_COMMERCIALIZED,
        industry_code="TECH",
        industry_description="AI / SaaS",
        business_model="B2B SaaS subscription with usage-based add-ons.",
        extraction_version="0.0.1",
        extracted_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# ORM stubs (un-persisted; safe to use in unit tests without a session)
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_ipo_event() -> IPOEvent:
    return IPOEvent(
        stock_code="2228.HK",
        company_name_zh="晶泰控股",
        company_name_en="QuantumPharm Inc.",
        listing_type=ListingType.CH18C_COMMERCIALIZED.value,
        industry_code="BIOTECH-AI",
        a1_filing_date=date(2023, 8, 30),
        hearing_date=date(2024, 5, 28),
        pricing_date=date(2024, 6, 6),
        listing_date=date(2024, 6, 13),
        issue_size_hkd=Decimal("1000000000"),
        regulatory_regime="pre_new_pricing",
        is_18c_pre_commercial=False,
    )


@pytest.fixture
def sample_cornerstone() -> CornerstoneInvestor:
    return CornerstoneInvestor(
        name_zh="中国国有企业混合所有制改革基金",
        name_en="SOE Mixed Ownership Reform Fund",
        category="sovereign",
        parent_org="中国国新控股",
        ultimate_holder="国资委",
        home_country="CN",
        signal_strength_score=Decimal("85.0"),
    )


# ---------------------------------------------------------------------------
# Settings overrides
# ---------------------------------------------------------------------------


@pytest.fixture
def settings_override(monkeypatch: pytest.MonkeyPatch) -> Callable[..., Settings]:
    """Factory that builds a `Settings` instance with overrides and patches the global cache.

    Example:
        s = settings_override(database__host="test.example.com")
        assert get_settings().database.host == "test.example.com"
    """

    def _apply(**overrides: Any) -> Settings:
        # Convert nested dotted kwargs into env vars (HK_IPO__SECTION__KEY)
        for key, value in overrides.items():
            env_key = "HK_IPO__" + key.upper().replace(".", "__")
            monkeypatch.setenv(env_key, str(value))
        get_settings.cache_clear()  # type: ignore[attr-defined]
        return get_settings()

    return _apply


# ---------------------------------------------------------------------------
# Determinism helpers
# ---------------------------------------------------------------------------


_FROZEN_TS = datetime(2026, 5, 16, 14, 30, 0, tzinfo=UTC)


@pytest.fixture
def frozen_now(monkeypatch: pytest.MonkeyPatch) -> Iterator[datetime]:
    """Pin `hk_ipo_agent.common.utils.utcnow()` to a fixed timestamp."""
    monkeypatch.setattr(utils_mod, "utcnow", lambda: _FROZEN_TS)
    yield _FROZEN_TS
