"""Tests for `hk_ipo_agent.prospectus.extractor` — LLM-based structured extraction."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest

from hk_ipo_agent.common.enums import ListingType
from hk_ipo_agent.common.llm_client import LLMClient
from hk_ipo_agent.prospectus.extractor import (
    ExtractionConfig,
    ExtractionResult,
    ProspectusExtractor,
    _BusinessResponse,
    _Ch18CResponse,
    _FinancialsResponse,
    _RisksResponse,
    _ShareholdersResponse,
)


@pytest.fixture
def extraction_config() -> ExtractionConfig:
    return ExtractionConfig(
        company_name_zh="测试科技",
        listing_type=ListingType.CH18C_COMMERCIALIZED,
        industry_code="TECH",
        industry_description="AI / SaaS",
    )


@pytest.fixture
def mock_llm(monkeypatch: pytest.MonkeyPatch) -> LLMClient:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-extractor")
    return LLMClient(daily_budget_usd=Decimal("100"))


# ---------------------------------------------------------------------------
# Financials extraction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_financials_populates_extraction(
    mock_llm: LLMClient, extraction_config: ExtractionConfig
) -> None:
    """Valid financials response should populate extraction.financials."""
    fake_response = _FinancialsResponse(
        financials_json=[
            {
                "fiscal_year": 2024,
                "fiscal_period": "FY",
                "revenue_rmb": "1234567890.00",
                "gross_margin": 0.37,
                "citation": {"page": 142, "chunk_id": "chunk-fin-1"},
            }
        ],
        needs_review=False,
        notes="",
    )
    mock_llm.acomplete_json = AsyncMock(return_value=fake_response)  # type: ignore[method-assign]

    extractor = ProspectusExtractor(mock_llm, "P-TEST", config=extraction_config)
    chunks_by_section: dict[str, list[dict[str, Any]]] = {
        "financials": [{"text": "Revenue was...", "page": 142, "chunk_id": "chunk-fin-1"}],
    }
    result = await extractor.extract(chunks_by_section)

    assert isinstance(result, ExtractionResult)
    assert len(result.extraction.financials) == 1
    snap = result.extraction.financials[0]
    assert snap.fiscal_year == 2024
    assert snap.revenue_rmb == Decimal("1234567890.00")
    assert snap.gross_margin == pytest.approx(0.37)
    assert snap.citation.page == 142
    assert snap.citation.chunk_id == "chunk-fin-1"


@pytest.mark.asyncio
async def test_extract_financials_invalid_item_marks_review(
    mock_llm: LLMClient, extraction_config: ExtractionConfig
) -> None:
    """Malformed financial item should be skipped and flag needs_human_review."""
    fake_response = _FinancialsResponse(
        financials_json=[
            {"bad_field": "no fiscal_year"},  # missing required fields
        ],
        needs_review=False,
        notes="",
    )
    mock_llm.acomplete_json = AsyncMock(return_value=fake_response)  # type: ignore[method-assign]

    extractor = ProspectusExtractor(mock_llm, "P-TEST", config=extraction_config)
    result = await extractor.extract(
        {"financials": [{"text": "...", "page": 1, "chunk_id": "c-1"}]}
    )

    assert len(result.extraction.financials) == 0
    assert result.extraction.needs_human_review is True
    assert any("financials_parse_error" in r for r in result.extraction.review_reasons)


@pytest.mark.asyncio
async def test_extract_financials_needs_review_propagates(
    mock_llm: LLMClient, extraction_config: ExtractionConfig
) -> None:
    """LLM's needs_review flag + notes are propagated to extraction."""
    fake_response = _FinancialsResponse(
        financials_json=[],
        needs_review=True,
        notes="Unit ambiguous: 千元 vs 万元",
    )
    mock_llm.acomplete_json = AsyncMock(return_value=fake_response)  # type: ignore[method-assign]

    extractor = ProspectusExtractor(mock_llm, "P-TEST", config=extraction_config)
    result = await extractor.extract(
        {"financials": [{"text": "...", "page": 1, "chunk_id": "c-1"}]}
    )

    assert result.extraction.needs_human_review is True
    assert any("financials_note" in r for r in result.extraction.review_reasons)


# ---------------------------------------------------------------------------
# Business extraction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_business_populates_model_and_streams(
    mock_llm: LLMClient, extraction_config: ExtractionConfig
) -> None:
    fake_response = _BusinessResponse(
        business_model="B2B SaaS platform for enterprise HR.",
        revenue_streams=[{"name": "SaaS subscription", "pct": 0.8}],
        customer_concentration=[],
        needs_review=False,
    )
    mock_llm.acomplete_json = AsyncMock(return_value=fake_response)  # type: ignore[method-assign]

    extractor = ProspectusExtractor(mock_llm, "P-TEST", config=extraction_config)
    result = await extractor.extract(
        {"business": [{"text": "Our platform...", "page": 50, "chunk_id": "c-biz"}]}
    )

    assert result.extraction.business_model == "B2B SaaS platform for enterprise HR."
    assert len(result.extraction.revenue_streams) == 1
    assert result.extraction.revenue_streams[0]["name"] == "SaaS subscription"


# ---------------------------------------------------------------------------
# Risks extraction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_risks_populates_risk_factors(
    mock_llm: LLMClient, extraction_config: ExtractionConfig
) -> None:
    fake_response = _RisksResponse(
        risk_factors=[
            {
                "category": "business",
                "description": "Customer concentration risk.",
                "severity": "high",
                "citation": {"page": 80, "chunk_id": "c-risk-1"},
            }
        ],
        needs_review=False,
    )
    mock_llm.acomplete_json = AsyncMock(return_value=fake_response)  # type: ignore[method-assign]

    extractor = ProspectusExtractor(mock_llm, "P-TEST", config=extraction_config)
    result = await extractor.extract(
        {"risks": [{"text": "We depend on...", "page": 80, "chunk_id": "c-risk-1"}]}
    )

    assert len(result.extraction.risk_factors) == 1
    rf = result.extraction.risk_factors[0]
    assert rf.category == "business"
    assert rf.severity == "high"
    assert rf.citation.page == 80


@pytest.mark.asyncio
async def test_extract_risks_invalid_item_skipped(
    mock_llm: LLMClient, extraction_config: ExtractionConfig
) -> None:
    fake_response = _RisksResponse(
        risk_factors=[{"category": "INVALID_CATEGORY", "description": "x", "severity": "high", "citation": {"page": 1}}],
        needs_review=False,
    )
    mock_llm.acomplete_json = AsyncMock(return_value=fake_response)  # type: ignore[method-assign]

    extractor = ProspectusExtractor(mock_llm, "P-TEST", config=extraction_config)
    result = await extractor.extract(
        {"risks": [{"text": "...", "page": 1, "chunk_id": "c-1"}]}
    )

    assert len(result.extraction.risk_factors) == 0
    assert result.extraction.needs_human_review is True


# ---------------------------------------------------------------------------
# Shareholders extraction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_shareholders_populates_data(
    mock_llm: LLMClient, extraction_config: ExtractionConfig
) -> None:
    fake_response = _ShareholdersResponse(
        shareholders=[
            {
                "name": "Founder A",
                "pct_pre_ipo": 0.35,
                "is_controlling": True,
                "is_pre_ipo_investor": False,
                "citation": {"page": 215, "chunk_id": "c-sh-1"},
            }
        ],
        pre_ipo_valuation_rmb="8500000000.00",
        last_round_date="2023-06-15",
        needs_review=False,
    )
    mock_llm.acomplete_json = AsyncMock(return_value=fake_response)  # type: ignore[method-assign]

    extractor = ProspectusExtractor(mock_llm, "P-TEST", config=extraction_config)
    result = await extractor.extract(
        {"shareholders": [{"text": "Shareholders...", "page": 215, "chunk_id": "c-sh-1"}]}
    )

    assert len(result.extraction.shareholders) == 1
    sh = result.extraction.shareholders[0]
    assert sh.name == "Founder A"
    assert sh.pct_pre_ipo == pytest.approx(0.35)
    assert sh.is_controlling is True
    assert result.extraction.pre_ipo_valuation_rmb == Decimal("8500000000.00")
    from datetime import date  # noqa: PLC0415
    assert result.extraction.last_round_date == date(2023, 6, 15)


@pytest.mark.asyncio
async def test_extract_shareholders_bad_valuation_skipped(
    mock_llm: LLMClient, extraction_config: ExtractionConfig
) -> None:
    """Invalid pre_ipo_valuation_rmb string doesn't crash, just logs warning."""
    fake_response = _ShareholdersResponse(
        shareholders=[],
        pre_ipo_valuation_rmb="not-a-number",
        last_round_date="invalid-date",
        needs_review=False,
    )
    mock_llm.acomplete_json = AsyncMock(return_value=fake_response)  # type: ignore[method-assign]

    extractor = ProspectusExtractor(mock_llm, "P-TEST", config=extraction_config)
    result = await extractor.extract(
        {"shareholders": [{"text": "...", "page": 1, "chunk_id": "c-1"}]}
    )

    # Should not crash; values remain None
    assert result.extraction.pre_ipo_valuation_rmb is None
    assert result.extraction.last_round_date is None


# ---------------------------------------------------------------------------
# Ch18C extraction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_ch18c_populates_qualification(
    mock_llm: LLMClient, extraction_config: ExtractionConfig
) -> None:
    fake_response = _Ch18CResponse(
        is_commercialized=True,
        revenue_threshold_met=True,
        rd_intensity_met=False,
        market_cap_threshold_hkd="6000000000",
        lead_investors=["CIC", "Hillhouse"],
    )
    mock_llm.acomplete_json = AsyncMock(return_value=fake_response)  # type: ignore[method-assign]

    extractor = ProspectusExtractor(mock_llm, "P-TEST", config=extraction_config)
    result = await extractor.extract(
        {"ch18c": [{"text": "Chapter 18C...", "page": 300, "chunk_id": "c-18c"}]}
    )

    assert result.extraction.ch18c_qualification is not None
    q = result.extraction.ch18c_qualification
    assert q.is_commercialized is True
    assert q.revenue_threshold_met is True
    assert q.market_cap_threshold_hkd == Decimal("6000000000")
    assert "CIC" in q.lead_investors
    assert q.citation.page == 300


@pytest.mark.asyncio
async def test_extract_ch18c_skipped_for_non_18c_listing(
    mock_llm: LLMClient,
) -> None:
    """Non-18C listing types should skip ch18c extraction entirely."""
    config = ExtractionConfig(
        company_name_zh="普通公司",
        listing_type=ListingType.MAINBOARD_OTHER,
        industry_code="FIN",
        industry_description="Financial services",
    )
    # acomplete_json should never be called for ch18c
    mock_llm.acomplete_json = AsyncMock()  # type: ignore[method-assign]

    extractor = ProspectusExtractor(mock_llm, "P-TEST", config=config)
    result = await extractor.extract(
        {"ch18c": [{"text": "...", "page": 1, "chunk_id": "c-1"}]}
    )

    assert result.extraction.ch18c_qualification is None
    mock_llm.acomplete_json.assert_not_awaited()


# ---------------------------------------------------------------------------
# Multi-section + fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_multiple_sections_records_success_count(
    mock_llm: LLMClient, extraction_config: ExtractionConfig
) -> None:
    """Multiple sections all succeed."""
    mock_llm.acomplete_json = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            _FinancialsResponse(financials_json=[], needs_review=False, notes=""),
            _BusinessResponse(business_model="SaaS", revenue_streams=[], customer_concentration=[], needs_review=False),
        ]
    )

    extractor = ProspectusExtractor(mock_llm, "P-TEST", config=extraction_config)
    result = await extractor.extract({
        "financials": [{"text": "...", "page": 1, "chunk_id": "c-1"}],
        "business": [{"text": "...", "page": 2, "chunk_id": "c-2"}],
    })

    assert result.sections_routed == 2
    assert result.sections_succeeded == 2
    assert result.sections_failed == []


@pytest.mark.asyncio
async def test_extract_section_failure_marks_review(
    mock_llm: LLMClient, extraction_config: ExtractionConfig
) -> None:
    """A section that raises ExtractionError is captured gracefully."""
    from hk_ipo_agent.common.exceptions import ExtractionError  # noqa: PLC0415

    mock_llm.acomplete_json = AsyncMock(  # type: ignore[method-assign]
        side_effect=ExtractionError("LLM timeout")
    )

    extractor = ProspectusExtractor(mock_llm, "P-TEST", config=extraction_config)
    result = await extractor.extract(
        {"financials": [{"text": "...", "page": 1, "chunk_id": "c-1"}]}
    )

    assert result.sections_succeeded == 0
    assert "financials" in result.sections_failed
    assert result.extraction.needs_human_review is True


@pytest.mark.asyncio
async def test_extract_opus_fallback_on_sonnet_failure(
    mock_llm: LLMClient, extraction_config: ExtractionConfig
) -> None:
    """When Sonnet fails, Opus fallback should be attempted."""
    call_count = {"n": 0}

    async def side_effect(**kwargs: Any) -> Any:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise Exception("Sonnet failed")
        return _BusinessResponse(
            business_model="Recovered via Opus",
            revenue_streams=[],
            customer_concentration=[],
            needs_review=False,
        )

    mock_llm.acomplete_json = AsyncMock(side_effect=side_effect)  # type: ignore[method-assign]

    extractor = ProspectusExtractor(mock_llm, "P-TEST", config=extraction_config)
    result = await extractor.extract(
        {"business": [{"text": "...", "page": 1, "chunk_id": "c-1"}]}
    )

    assert result.extraction.business_model == "Recovered via Opus"
    assert call_count["n"] == 2  # first call failed, second succeeded


@pytest.mark.asyncio
async def test_extract_unknown_section_is_skipped(
    mock_llm: LLMClient, extraction_config: ExtractionConfig
) -> None:
    """Unknown section routes are silently skipped (not failures)."""
    mock_llm.acomplete_json = AsyncMock()  # type: ignore[method-assign]

    extractor = ProspectusExtractor(mock_llm, "P-TEST", config=extraction_config)
    result = await extractor.extract(
        {"other": [{"text": "...", "page": 1, "chunk_id": "c-1"}]}
    )

    assert result.sections_succeeded == 1  # "other" dispatched ok (just a no-op)
    assert result.sections_failed == []
    mock_llm.acomplete_json.assert_not_awaited()
