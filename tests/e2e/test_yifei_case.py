"""E2E regression: 翼菲智能 (6871.HK) — fixture-driven, no real LLM.

Per ADR 0016 §Decision third class: the former hard-coded
``scripts/run_e2e_test.py`` retires here as a parametrised pytest case
that runs against the same 翼菲智能 PDF (when it's present on disk) but
mocks the LLM so the test stays cost-free and reproducible.

The non-mocked surface this test actually exercises:
  - ``pipelines.pdf_to_snapshot.PipelineConfig`` construction
  - ``parse_prospectus`` (PyMuPDF) against a real ~24MB 456-page HK PDF
  - ``chunk_document`` over a real document
  - The section classifier (``_classify_chunk``) over real prospectus text

What is mocked:
  - ``ProspectusExtractor.extract`` — returns a deterministic fixture
    so the test doesn't burn 5+ minutes of KIMI calls.
  - ``build_main_graph(...).ainvoke`` — returns a fixture final_state
    with a Decision + snapshot_id.

The actual LLM-driven pipeline correctness is covered by
``test_full_pipeline_smoke.py`` (mock graph) and run end-to-end manually
via ``scripts/analyze_pdf.py`` when the user provides KIMI_API_KEY.

Skip behaviour: the PDF lives in a gitignored ``测试案例/`` folder per
spec §11 data-safety rules; if it's not on disk this test skips rather
than failing.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from hk_ipo_agent.common.enums import DecisionType, ListingType
from hk_ipo_agent.common.llm_client import LLMClient
from hk_ipo_agent.common.schemas import (
    FinalDecision,
    ProspectusExtraction,
    ValuationDistribution,
)
from hk_ipo_agent.pipelines.pdf_to_snapshot import (
    PipelineConfig,
    _classify_chunk,
    _group_chunks_by_section,
    run_pdf_to_snapshot,
)
from hk_ipo_agent.valuation.base import MarketData, PeerMultiples

_ROOT = Path(__file__).resolve().parents[2]
YIFEI_PDF = (
    _ROOT
    / "测试案例"
    / "2026-04-20-6871.HK-翼菲智能-20260420浙江翼菲智能科技股份有限公司聆訊後資料集（第一次呈交）全文檔案.pdf"
)

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not YIFEI_PDF.exists(),
        reason=f"翼菲智能 PDF not on disk (gitignored per spec §11): {YIFEI_PDF.name}",
    ),
]


# ---------------------------------------------------------------------------
# Lightweight fixtures (replace expensive LLM-driven steps)
# ---------------------------------------------------------------------------


def _fixture_extraction() -> ProspectusExtraction:
    return ProspectusExtraction(
        prospectus_id="6871-hk-yifei-fixture",
        company_name_zh="浙江翼菲智能科技股份有限公司",
        listing_type=ListingType.CH18C_COMMERCIALIZED,
        industry_code="machinery_robotics",
        industry_description="工业机器人与智能制造",
        business_model="并联机器人 + 智能产线集成",
        extraction_version="0.0.1",
        extracted_at=datetime.now(UTC),
    )


def _fixture_decision() -> FinalDecision:
    d = ValuationDistribution(
        p10=Decimal("90"), p25=Decimal("95"), p50=Decimal("100"),
        p75=Decimal("105"), p90=Decimal("110"),
        mean=Decimal("100"), std=Decimal("5"),
    )
    return FinalDecision(
        decision=DecisionType.SKIP,
        confidence=0.65,
        suggested_allocation_pct=0.0,
        price_range_low=Decimal("95"),
        price_range_fair=Decimal("100"),
        price_range_high=Decimal("105"),
        expected_return_6m=d,
        expected_return_12m=d,
        scorecard={"overall": 55.0},
        key_reasons_for=["fixture reason for"],
        key_reasons_against=["fixture reason against"],
    )


def _market_data() -> MarketData:
    return MarketData(
        as_of_date=date.today(),
        listing_type=ListingType.CH18C_COMMERCIALIZED,
        peer_multiples=PeerMultiples(
            pe_ttm=[50.0, 65.0, 80.0, 120.0, 150.0],
            ps_ttm=[8.0, 12.0, 15.0, 20.0, 30.0],
            pb_latest=[3.0, 5.0, 7.0, 10.0, 15.0],
            ev_ebitda=[25.0, 35.0, 45.0, 60.0, 80.0],
            sample_size=5,
        ),
        regime_score=0.3,
        risk_free_rate=0.025,
        equity_risk_premium=0.07,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_classify_chunk_recognises_each_section() -> None:
    """Sanity-check the content-based section classifier on real CJK phrases.

    These keywords are load-bearing for HK prospectus parsing because
    structural section headers are unreliable (TOC entries trip them).
    """
    assert _classify_chunk("本公司主要收入來自於並聯機器人銷售") == "financials"
    assert _classify_chunk("Risk factors related to single-customer concentration") == "risks"
    assert _classify_chunk("我們的客戶主要為汽車製造業") == "business"
    assert _classify_chunk("公司基石投資者包括三家香港家族辦公室") == "shareholders"
    assert _classify_chunk("Generic boilerplate text with no signal") == "other"


def test_group_chunks_by_section_skips_other() -> None:
    """``other``-classified chunks must not be routed to the extractor —
    they'd burn tokens with no extractable structured output.
    """

    class _Chunk:
        def __init__(self, text: str, page: int, chunk_id: str) -> None:
            self.text = text
            self.page = page
            self.chunk_id = chunk_id

    chunks = [
        _Chunk("本公司主要收入", 1, "c1"),
        _Chunk("Generic boilerplate", 2, "c2"),
        _Chunk("基石投資者列表", 3, "c3"),
    ]
    groups = _group_chunks_by_section(chunks)
    assert set(groups.keys()) == {"financials", "shareholders"}
    assert "other" not in groups
    assert groups["financials"][0]["chunk_id"] == "c1"
    assert groups["shareholders"][0]["chunk_id"] == "c3"


@pytest.fixture(autouse=True)
def _kimi_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLMClient validation requires KIMI_API_KEY at construction time,
    even though we never invoke real API calls in this test.
    """
    monkeypatch.setenv("KIMI_API_KEY", "sk-test")
    _ = os  # keep import meaningful


@pytest.mark.asyncio
async def test_yifei_pdf_parse_chunk_through_pipeline_with_mocks() -> None:
    """End-to-end pipeline run against the real 翼菲智能 PDF.

    Parse + chunk are real (PyMuPDF on disk). Extract + graph are mocked
    so the test completes in seconds without consuming LLM tokens.
    """
    config = PipelineConfig(
        pdf_path=YIFEI_PDF,
        ipo_id="6871.HK",
        prospectus_id="6871-hk-yifei",
        company_name_zh="浙江翼菲智能科技股份有限公司",
        listing_type=ListingType.CH18C_COMMERCIALIZED,
        industry_code="machinery_robotics",
        industry_description="工业机器人与智能制造",
        max_pages=50,  # cap for test speed; production CLI defaults to 500
        max_chunks_per_section=3,
        write_report=False,  # avoid touching outputs/
    )
    market_data = _market_data()

    # ---- Mocks --------------------------------------------------------
    fixture_extraction = _fixture_extraction()
    fixture_decision = _fixture_decision()

    class _MockExtractionResult:
        extraction = fixture_extraction
        sections_routed = 4
        sections_succeeded = 4
        sections_failed = 0
        total_cost_usd = Decimal("0.00")

    mock_extractor_extract = AsyncMock(return_value=_MockExtractionResult())

    fixture_snapshot_id = str(uuid.uuid4())

    class _MockGraph:
        async def ainvoke(self, state: dict, *args: object, **kwargs: object) -> dict:
            return {
                **state,
                "decision": fixture_decision,
                "agent_outputs": {},
                "snapshot_id": fixture_snapshot_id,
            }

    llm = LLMClient(daily_budget_usd=Decimal("0.10"))

    with (
        patch(
            "hk_ipo_agent.pipelines.pdf_to_snapshot.ProspectusExtractor.extract",
            mock_extractor_extract,
        ),
        patch(
            "hk_ipo_agent.pipelines.pdf_to_snapshot.build_main_graph",
            return_value=_MockGraph(),
        ),
    ):
        result = await run_pdf_to_snapshot(
            config, market_data, llm_client=llm, log=lambda _msg: None
        )

    # ---- Assertions ---------------------------------------------------
    # Parse step actually executed against a real 456-page PDF.
    assert result.parsed_doc.page_count > 0
    assert len(result.parsed_doc.blocks) > 0
    assert len(result.parsed_doc.full_text) > 1000

    # Chunk step actually executed.
    assert len(result.chunks) > 0

    # Mocked extract + graph wired correctly.
    assert result.extraction_result.extraction.company_name_zh == "浙江翼菲智能科技股份有限公司"
    assert result.snapshot_id == fixture_snapshot_id
    assert result.final_state["decision"].decision is DecisionType.SKIP

    # Step timings recorded for each pipeline step.
    assert set(result.step_timings_s.keys()) >= {"parse", "chunk", "extract", "graph"}
    # 30-min SLO has 1000x headroom on a fixture-mocked run.
    assert result.total_elapsed_s < 60.0
