"""Phase 6 DONE-condition: full LangGraph ainvoke through 7 agents → snapshot → report.

Mocked LLM responses; verifies the pipeline ordering invariant + final
state shape.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from hk_ipo_agent.agents.workflow_extras import WorkflowExtras
from hk_ipo_agent.common.enums import ListingType
from hk_ipo_agent.common.schemas import (
    Citation,
    FinancialSnapshot,
    ProspectusExtraction,
)
from hk_ipo_agent.common.settings import get_settings
from hk_ipo_agent.orchestrator.graph import build_main_graph
from hk_ipo_agent.orchestrator.states import AnalysisState
from hk_ipo_agent.prediction_registry.registry import get_registry, reset_registry
from hk_ipo_agent.valuation.base import MarketData, PeerMultiples


def _full_extraction() -> ProspectusExtraction:
    return ProspectusExtraction(
        prospectus_id="P-DONE-6",
        company_name_zh="测试AI",
        listing_type=ListingType.MAINBOARD_TECH,
        industry_code="AI",
        industry_description="AI / SaaS",
        business_model="B2B SaaS",
        financials=[
            FinancialSnapshot(
                fiscal_year=2024,
                fiscal_period="FY",
                revenue_rmb=Decimal("500000000"),
                net_profit_rmb=Decimal("50000000"),
                gross_margin=0.45,
                citation=Citation(page=42),
            ),
            FinancialSnapshot(
                fiscal_year=2025,
                fiscal_period="FY",
                revenue_rmb=Decimal("800000000"),
                net_profit_rmb=Decimal("80000000"),
                gross_margin=0.50,
                cash_balance_rmb=Decimal("200000000"),
                citation=Citation(page=42),
            ),
        ],
        extraction_version="0.0.1",
        extracted_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_done_condition_full_pipeline(
    mock_llm_client, mock_llm_response, monkeypatch
) -> None:
    """Run the full LangGraph; verify all key transitions happen."""
    reset_registry()
    # Mock every LLM call to return parseable JSON for both narrative and
    # synthesizer-typed responses.
    json_blob = (
        '{"confidence": 0.7, "key_reasons_for": ["growth"], '
        '"key_reasons_against": ["concentration"], "narrative": "ok", '
        '"allocation_pct_suggested": 0.04}'
    )
    mock_llm_client._client.chat.completions.create = AsyncMock(
        return_value=mock_llm_response(text=f"narrative\n```json\n{json_blob}\n```")
    )

    monkeypatch.setenv("HK_IPO__ORCHESTRATOR__ENABLE_HITL", "false")
    get_settings.cache_clear()

    md = MarketData(
        as_of_date=date(2026, 5, 16),
        listing_type=ListingType.MAINBOARD_TECH,
        peer_multiples=PeerMultiples(
            ps_ttm=[4.0, 6.0, 8.0, 10.0],
            pe_ttm=[20.0, 25.0, 30.0],
            sample_size=4,
        ),
        regime_score=0.05,
        extra={"mc_seed": 7},
    )
    extras = WorkflowExtras(
        pricing_date=date(2026, 5, 16),
        cornerstone_profiles=[
            {"name": "A1", "category": "sovereign", "ultimate_holder": "中投"},
            {"name": "A2", "category": "sovereign", "ultimate_holder": "中投"},
        ],
        peer_multiples={"ps_ttm": [4.0, 6.0, 8.0]},
    )
    extras.misc["regime_score_override"] = 0.08

    initial: AnalysisState = {
        "ipo_id": "ipo-done-6",
        "prospectus_id": "P-DONE-6",
        "as_of_date": date(2026, 5, 16),
        "extraction": _full_extraction(),
        "extras": extras,
    }

    graph = build_main_graph(llm_client=mock_llm_client, market_data=md, use_checkpointer=False)
    final = await graph.ainvoke(initial)

    # 1. 6 NACS-aware fanout agents + valuation_agent → 7 outputs
    assert len(final["agent_outputs"]) == 7
    assert set(final["agent_outputs"].keys()) == {
        "fundamental",
        "industry",
        "policy",
        "liquidity",
        "cornerstone_signal",
        "sentiment",
        "valuation",
    }
    # 2. valuation produced
    assert "valuation_output" in final
    # 3. debate ran
    assert "debate_output" in final
    assert len(final["debate_output"].rounds) >= 1
    # 4. cross_check notes captured
    assert "cross_check_notes" in final
    # 5. decision present, type valid
    assert "decision" in final
    # 6. snapshot created (HARD invariant)
    assert "snapshot_id" in final
    reg = get_registry()
    fetched = await reg.get_snapshot(final["snapshot_id"])
    assert fetched.id == final["snapshot_id"]
    # 7. NACS extras populated via reducer
    assert final["extras"].regime_score == 0.08
    assert final["extras"].cluster_bonus_multiplier == 1.10

    reset_registry()
