"""Tests for ``run_ensemble`` — weighted blend + Regime Gate hard gate."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from hk_ipo_agent.common.enums import ListingType
from hk_ipo_agent.common.schemas import (
    Citation,
    FinancialSnapshot,
    ProspectusExtraction,
    SingleModelValuation,
    ValuationDistribution,
)
from hk_ipo_agent.valuation.base import MarketData, PeerMultiples, ValuationModel
from hk_ipo_agent.valuation.ensemble import REGIME_GATE_THRESHOLD, run_ensemble


class _StubModel(ValuationModel):
    """Returns a fixed distribution; useful for deterministic ensemble tests."""

    def __init__(
        self,
        name: str,
        p50: Decimal,
        *,
        applicable: bool = True,
        types: list[ListingType] | None = None,
    ) -> None:
        # Per-instance override of ClassVar (test stub only) — fine because each
        # _StubModel(...) is a fresh instance and Python's lookup falls back
        # to the class attr if instance attr is unset.
        self.model_name = name  # type: ignore[misc]
        self.applicable_types = types or list(ListingType)  # type: ignore[misc]
        self._p50 = p50
        self._applicable = applicable

    async def value(
        self,
        extraction: ProspectusExtraction,
        market_data: MarketData,
    ) -> SingleModelValuation:
        if not self._applicable:
            return self._not_applicable(reason="stub disabled", model_name=self.model_name)
        return SingleModelValuation(
            model_name=self.model_name,
            applicable=True,
            valuation_distribution=ValuationDistribution(
                p10=self._p50 * Decimal("0.8"),
                p25=self._p50 * Decimal("0.9"),
                p50=self._p50,
                p75=self._p50 * Decimal("1.1"),
                p90=self._p50 * Decimal("1.2"),
                mean=self._p50,
                std=self._p50 * Decimal("0.1"),
            ),
        )


def _ext(lt: ListingType = ListingType.MAINBOARD_TECH) -> ProspectusExtraction:
    return ProspectusExtraction(
        prospectus_id="P-ENS-1",
        company_name_zh="测试",
        listing_type=lt,
        industry_code="TECH",
        industry_description="x",
        business_model="x",
        financials=[
            FinancialSnapshot(
                fiscal_year=2025,
                fiscal_period="FY",
                revenue_rmb=Decimal("1e9"),
                citation=Citation(page=1),
            )
        ],
        extraction_version="0.0.1",
        extracted_at=datetime.now(UTC),
    )


def _md(regime: float | None = None) -> MarketData:
    return MarketData(
        as_of_date=date(2026, 5, 16),
        listing_type=ListingType.MAINBOARD_TECH,
        peer_multiples=PeerMultiples(ps_ttm=[3.0], sample_size=1),
        regime_score=regime,
        extra={"mc_seed": 0},
    )


@pytest.mark.asyncio
async def test_ensemble_blends_two_models_equal_weight() -> None:
    out = await run_ensemble(
        _ext(),
        _md(),
        models=[_StubModel("comparable", Decimal("1000")), _StubModel("dcf", Decimal("3000"))],
        weights_override={"comparable": 0.5, "dcf": 0.5},
    )
    # Expected blended p50 = 0.5 * 1000 + 0.5 * 3000 = 2000.
    assert float(out.ensemble_distribution.p50) == pytest.approx(2000.0, abs=1.0)
    assert out.weights_used == {"comparable": 0.5, "dcf": 0.5}
    assert "low" in out.implied_price_range


@pytest.mark.asyncio
async def test_ensemble_renormalizes_when_some_models_inapplicable() -> None:
    out = await run_ensemble(
        _ext(),
        _md(),
        models=[
            _StubModel("comparable", Decimal("1000")),
            _StubModel("dcf", Decimal("3000"), applicable=False),
        ],
        weights_override={"comparable": 0.4, "dcf": 0.6},
    )
    # dcf dropped → comparable carries full weight 1.0.
    assert out.weights_used == {"comparable": 1.0}
    assert float(out.ensemble_distribution.p50) == pytest.approx(1000.0, abs=1.0)


@pytest.mark.asyncio
async def test_ensemble_equal_weight_fallback() -> None:
    out = await run_ensemble(
        _ext(),
        _md(),
        models=[_StubModel("comparable", Decimal("1000")), _StubModel("dcf", Decimal("2000"))],
        weights_override=None,  # forces YAML lookup — may be empty in test env
    )
    # Regardless of YAML state, weights must sum to ~1 and produce blended >0.
    assert sum(out.weights_used.values()) == pytest.approx(1.0, abs=1e-6)
    assert float(out.ensemble_distribution.p50) > 0


@pytest.mark.asyncio
async def test_ensemble_regime_gate_forces_skip() -> None:
    out = await run_ensemble(
        _ext(),
        _md(regime=-0.1),  # negative → hard gate
        models=[_StubModel("comparable", Decimal("1000"))],
        weights_override={"comparable": 1.0},
    )
    # Hard gate triggered: price range zeroed.
    assert out.implied_price_range["low"] == Decimal("0")
    assert out.implied_price_range["fair"] == Decimal("0")
    assert out.implied_price_range["high"] == Decimal("0")
    assert any("Regime Gate triggered" in n for n in out.notes)


@pytest.mark.asyncio
async def test_ensemble_no_applicable_models_returns_empty() -> None:
    out = await run_ensemble(
        _ext(),
        _md(),
        models=[_StubModel("comparable", Decimal("1000"), applicable=False)],
    )
    assert out.weights_used == {}
    assert out.ensemble_distribution.p50 == Decimal("0")
    assert any("no models applicable" in n for n in out.notes)


@pytest.mark.asyncio
async def test_regime_gate_threshold_constant() -> None:
    assert REGIME_GATE_THRESHOLD == 0.0


@pytest.mark.asyncio
async def test_ensemble_regime_zero_does_not_trigger_gate() -> None:
    out = await run_ensemble(
        _ext(),
        _md(regime=0.0),  # boundary — NOT below threshold
        models=[_StubModel("comparable", Decimal("1000"))],
        weights_override={"comparable": 1.0},
    )
    assert out.implied_price_range["fair"] > Decimal("0")
    assert not any("Regime Gate" in n for n in out.notes)
