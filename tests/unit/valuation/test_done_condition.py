"""Phase 4 DONE-condition smoke test.

Verifies the end-to-end flow stated in PROJECT_SPEC.md §3.7 and
CLAUDE.md「Phase 4 DONE 条件」:

    ProspectusExtraction → 4+ independent models → 10k MC → weighted
    ensemble → ValuationEnsembleOutput with implied_price_range

Also verifies the Regime Gate hard gate (ADR 0005 §2) integrates with
the full model lineup.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from hk_ipo_agent.common.enums import ListingType
from hk_ipo_agent.common.schemas import (
    Citation,
    FinancialSnapshot,
    ProspectusExtraction,
    ValuationEnsembleOutput,
)
from hk_ipo_agent.valuation import (
    AHPremiumValuation,
    ComparableValuation,
    DCFValuation,
    MarketData,
    MilestonesValuation,
    PeerMultiples,
    PreIPOAnchorValuation,
    run_ensemble,
)
from hk_ipo_agent.valuation.industry import industry_models


def _make_full_extraction(
    listing_type: ListingType = ListingType.CH18C_COMMERCIALIZED,
) -> ProspectusExtraction:
    return ProspectusExtraction(
        prospectus_id="P-DONE-1",
        company_name_zh="测试AI公司",
        company_name_en="Test AI Co.",
        listing_type=listing_type,
        industry_code="AI",
        industry_description="AI / SaaS / 人工智能",
        business_model="B2B AI subscription with enterprise tier",
        financials=[
            FinancialSnapshot(
                fiscal_year=2025,
                fiscal_period="FY",
                revenue_rmb=Decimal("800000000"),
                net_profit_rmb=Decimal("80000000"),
                cash_balance_rmb=Decimal("200000000"),
                citation=Citation(page=42, section="财务摘要"),
            )
        ],
        pre_ipo_valuation_rmb=Decimal("5000000000"),
        last_round_date=date(2024, 6, 1),
        extraction_version="0.0.1",
        extracted_at=datetime.now(UTC),
    )


def _make_market_data(
    *,
    regime: float | None = 0.3,
    listing_type: ListingType = ListingType.CH18C_COMMERCIALIZED,
) -> MarketData:
    return MarketData(
        as_of_date=date(2026, 5, 16),
        listing_type=listing_type,
        peer_multiples=PeerMultiples(
            ps_ttm=[4.0, 6.0, 8.0, 10.0, 12.0],
            pe_ttm=[20.0, 25.0, 30.0, 35.0, 40.0],
            sample_size=5,
        ),
        regime_score=regime,
        extra={"mc_seed": 42},
    )


@pytest.mark.asyncio
async def test_done_condition_18c_commercial_full_pipeline() -> None:
    """End-to-end: 5 models + industry → ensemble → price range."""
    extraction = _make_full_extraction()
    market_data = _make_market_data()

    models = [
        ComparableValuation(),
        DCFValuation(),
        PreIPOAnchorValuation(),
        AHPremiumValuation(),  # will report not_applicable for non-AH
        MilestonesValuation(),  # not applicable to COMMERCIALIZED (only PRE/18A)
        *industry_models(),  # AI matcher should fire on industry_code="AI"
    ]

    output = await run_ensemble(extraction, market_data, models)

    # Output type check
    assert isinstance(output, ValuationEnsembleOutput)
    assert output.company_id == "P-DONE-1"

    # All models recorded (applicable or not)
    assert len(output.single_models) == len(models)

    # >= 4 independent applicable models for COMM listing
    applicable = [m for m in output.single_models if m.applicable]
    assert len(applicable) >= 4, (
        f"Phase 4 DONE requires 4+ independent models; got {len(applicable)}: "
        f"{[m.model_name for m in applicable]}"
    )

    # AH and Milestones must NOT apply to COMMERCIALIZED non-AH listing.
    inapplicable_names = {m.model_name for m in output.single_models if not m.applicable}
    assert "ah_premium" in inapplicable_names
    assert "milestones" in inapplicable_names

    # Weights normalize to 1.0
    assert sum(output.weights_used.values()) == pytest.approx(1.0, abs=1e-6)

    # Ensemble distribution non-degenerate
    assert output.ensemble_distribution.p50 > Decimal("0")
    assert output.ensemble_distribution.p10 <= output.ensemble_distribution.p90
    assert output.ensemble_distribution.std > Decimal("0")

    # Implied price range is populated and ordered
    pr = output.implied_price_range
    assert pr["low"] > Decimal("0")
    assert pr["fair"] >= pr["low"]
    assert pr["high"] >= pr["fair"]


@pytest.mark.asyncio
async def test_done_condition_regime_gate_forces_skip_in_full_pipeline() -> None:
    """Regime Gate (ADR 0005 §2) hard gate integrates with full lineup."""
    extraction = _make_full_extraction()
    market_data = _make_market_data(regime=-0.5)  # adverse regime

    models = [
        ComparableValuation(),
        DCFValuation(),
        PreIPOAnchorValuation(),
        *industry_models(),
    ]

    output = await run_ensemble(extraction, market_data, models)

    # Hard gate zeroes the price range regardless of underlying values.
    assert output.implied_price_range["low"] == Decimal("0")
    assert output.implied_price_range["fair"] == Decimal("0")
    assert output.implied_price_range["high"] == Decimal("0")

    # But raw ensemble distribution is kept for diagnostics.
    assert output.ensemble_distribution.p50 > Decimal("0")

    # Note documents the gate hit.
    assert any("Regime Gate triggered" in n for n in output.notes)


@pytest.mark.asyncio
async def test_done_condition_pre_commercial_uses_milestones() -> None:
    """Pre-commercial 18C / 18A listings → milestones model becomes applicable."""
    extraction = _make_full_extraction(listing_type=ListingType.CH18C_PRE_COMMERCIAL)
    market_data = _make_market_data(listing_type=ListingType.CH18C_PRE_COMMERCIAL)

    models = [
        ComparableValuation(),
        DCFValuation(),  # not applicable to pre-commercial
        PreIPOAnchorValuation(),
        MilestonesValuation(),
        *industry_models(),
    ]

    output = await run_ensemble(extraction, market_data, models)

    applicable_names = {m.model_name for m in output.single_models if m.applicable}
    assert "milestones" in applicable_names
    assert "dcf" not in applicable_names  # DCF excludes pre-commercial
    assert sum(output.weights_used.values()) == pytest.approx(1.0, abs=1e-6)
