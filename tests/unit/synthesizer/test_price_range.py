"""Tests for synthesizer/price_range.py."""

from __future__ import annotations

from decimal import Decimal

from hk_ipo_agent.common.schemas import (
    SingleModelValuation,
    ValuationDistribution,
    ValuationEnsembleOutput,
)
from hk_ipo_agent.synthesizer.price_range import derive_price_range


def _ensemble(low: int, fair: int, high: int) -> ValuationEnsembleOutput:
    dist = ValuationDistribution(
        p10=Decimal("0"),
        p25=Decimal(str(low)),
        p50=Decimal(str(fair)),
        p75=Decimal(str(high)),
        p90=Decimal("0"),
        mean=Decimal(str(fair)),
        std=Decimal("0"),
    )
    return ValuationEnsembleOutput(
        company_id="C",
        single_models=[
            SingleModelValuation(
                model_name="x",
                applicable=True,
                valuation_distribution=dist,
            )
        ],
        weights_used={"x": 1.0},
        ensemble_distribution=dist,
        implied_price_range={
            "low": Decimal(str(low)),
            "fair": Decimal(str(fair)),
            "high": Decimal(str(high)),
        },
    )


def test_derive_pass_through_when_no_regime() -> None:
    low, fair, high = derive_price_range(_ensemble(90, 100, 110), regime_score=None)
    assert (low, fair, high) == (Decimal("90"), Decimal("100"), Decimal("110"))


def test_derive_negative_regime_zeroes_range() -> None:
    low, fair, high = derive_price_range(_ensemble(90, 100, 110), regime_score=-0.10)
    assert low == fair == high == Decimal("0")


def test_derive_borderline_regime_widens_band() -> None:
    low, fair, high = derive_price_range(_ensemble(100, 100, 100), regime_score=0.02)
    # widen ±10%: low → 90, high → 110, fair unchanged
    assert low == Decimal("90.0000")
    assert fair == Decimal("100")
    assert high == Decimal("110.0000")


def test_derive_positive_far_above_threshold_no_widening() -> None:
    low, fair, high = derive_price_range(_ensemble(90, 100, 110), regime_score=0.15)
    assert (low, fair, high) == (Decimal("90"), Decimal("100"), Decimal("110"))
