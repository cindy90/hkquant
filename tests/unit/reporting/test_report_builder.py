"""Investment-memo builder + exporter tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from hk_ipo_agent.common.enums import AgentRole, DecisionType, ListingType
from hk_ipo_agent.common.schemas import (
    AgentOutput,
    DebateOutput,
    FinalDecision,
    ProspectusExtraction,
    SingleModelValuation,
    ValuationDistribution,
    ValuationEnsembleOutput,
)
from hk_ipo_agent.prediction_registry.snapshot import build_snapshot
from hk_ipo_agent.reporting import build_memo_markdown, export_docx, export_pdf


def _make_snapshot():
    ext = ProspectusExtraction(
        prospectus_id="P-RPT-1",
        company_name_zh="测试公司",
        listing_type=ListingType.MAINBOARD_TECH,
        industry_code="AI",
        industry_description="AI / SaaS",
        business_model="B2B SaaS",
        extraction_version="0.0.1",
        extracted_at=datetime.now(UTC),
    )
    d = ValuationDistribution(
        p10=Decimal("90"),
        p25=Decimal("95"),
        p50=Decimal("100"),
        p75=Decimal("105"),
        p90=Decimal("110"),
        mean=Decimal("100"),
        std=Decimal("5"),
    )
    val = ValuationEnsembleOutput(
        company_id="P-RPT-1",
        single_models=[
            SingleModelValuation(
                model_name="comparable",
                applicable=True,
                valuation_distribution=d,
            )
        ],
        weights_used={"comparable": 1.0},
        ensemble_distribution=d,
        implied_price_range={
            "low": Decimal("95"),
            "fair": Decimal("100"),
            "high": Decimal("105"),
        },
    )
    decision = FinalDecision(
        decision=DecisionType.PARTIAL,
        confidence=0.7,
        suggested_allocation_pct=0.02,
        price_range_low=Decimal("95"),
        price_range_fair=Decimal("100"),
        price_range_high=Decimal("105"),
        expected_return_6m=d,
        expected_return_12m=d,
        scorecard={"overall": 65.0},
        key_reasons_for=["growth"],
        key_reasons_against=["concentration"],
    )
    return build_snapshot(
        ipo_id=uuid4(),
        extraction=ext,
        agent_outputs={
            "fundamental": AgentOutput(
                agent_role=AgentRole.FUNDAMENTAL,
                scores={"x": 70.0},
                overall_score=70.0,
                runtime_seconds=0.1,
            ),
        },
        valuation=val,
        debate=DebateOutput(final_consensus="balanced"),
        decision=decision,
        total_cost_usd=Decimal("0.05"),
        runtime_seconds=10.0,
    )


def test_build_memo_markdown_contains_company_name() -> None:
    snap = _make_snapshot()
    md = build_memo_markdown(snap)
    assert "测试公司" in md
    assert "Investment Memo" in md
    # DecisionType.PARTIAL.value == "partial"
    assert "partial" in md.lower()
    assert "65" in md  # scorecard overall


def test_export_pdf_returns_bytes() -> None:
    snap = _make_snapshot()
    pdf = export_pdf(snap)
    assert isinstance(pdf, bytes)
    assert len(pdf) > 100
    # Either real PDF or HTML fallback
    assert pdf[:4] == b"%PDF" or pdf[:6] in (b"<!doct", b"<!DOCT")


def test_export_docx_returns_bytes() -> None:
    snap = _make_snapshot()
    docx = export_docx(snap)
    assert isinstance(docx, bytes)
    # DOCX is a zip file → starts with PK
    assert docx[:2] == b"PK"
