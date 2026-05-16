"""ValuationModel ABC + shared helpers per PROJECT_SPEC.md §3.7.

Every concrete model (DCF, Comparable, AH Premium, ...) returns a
``SingleModelValuation`` whose ``valuation_distribution`` is a 7-point
percentile summary built from 10k Monte Carlo samples. The ensemble
(``valuation/ensemble.py``) then weight-blends those distributions.

Inputs:
- ``ProspectusExtraction`` — Phase 3 output (structured prospectus data)
- ``MarketData`` — runtime-injected market context (peer multiples,
  benchmark prices, regime score, FX rates). Defined here as a dataclass
  because it's a Phase 4 internal coordination type; not in spec §6.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, ClassVar

import numpy as np

from ..common.enums import ListingType
from ..common.schemas import (
    Citation,
    ProspectusExtraction,
    SingleModelValuation,
    ValuationDistribution,
)

# ---------------------------------------------------------------------------
# MarketData — runtime context for valuation
# ---------------------------------------------------------------------------


@dataclass
class PeerMultiples:
    """Industry peer-set distribution snapshot used by ComparableValuation."""

    pe_ttm: list[float] = field(default_factory=list)
    ps_ttm: list[float] = field(default_factory=list)
    pb_latest: list[float] = field(default_factory=list)
    ev_ebitda: list[float] = field(default_factory=list)
    sample_size: int = 0

    @property
    def has_data(self) -> bool:
        return self.sample_size > 0


@dataclass
class MarketData:
    """Phase 4 runtime market context.

    All fields are optional so individual models can degrade gracefully when
    a particular signal is unavailable (e.g. no AH pair for non-AH IPOs).
    """

    as_of_date: Any  # datetime.date — kept loose to avoid circular imports
    listing_type: ListingType

    # Peers + industry multiples
    peer_multiples: PeerMultiples | None = None

    # Risk-free + ERP for WACC
    risk_free_rate: float = 0.025  # 25 bps default per HK gov bond
    equity_risk_premium: float = 0.07

    # Regime Gate signal (ADR 0005 §2). Negative -> ensemble forces SKIP.
    regime_score: float | None = None

    # AH-pair context (only populated for AH IPOs)
    a_share_price_at_filing: Decimal | None = None
    ah_premium_history_pct: list[float] = field(default_factory=list)

    # Pre-IPO anchor
    last_round_valuation_rmb: Decimal | None = None
    last_round_date: Any = None  # datetime.date

    # FX (HKD->RMB or RMB->HKD as needed)
    fx_hkd_to_rmb: float = 0.91
    fx_rmb_to_hkd: float = 1.10

    # Free-form extra context (industry-specific, MC seeds, etc.)
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Sample -> ValuationDistribution helpers
# ---------------------------------------------------------------------------


def distribution_from_samples(samples: np.ndarray) -> ValuationDistribution:
    """Build a 7-point percentile distribution from MC samples.

    Out-of-range / NaN samples are dropped. If the cleaned array is empty
    the distribution returns zeros (caller can mark the model as not
    applicable).
    """
    arr = samples[np.isfinite(samples)]
    if arr.size == 0:
        zero = Decimal("0")
        return ValuationDistribution(
            p10=zero, p25=zero, p50=zero, p75=zero, p90=zero, mean=zero, std=zero
        )
    pcts = np.percentile(arr, [10, 25, 50, 75, 90])
    return ValuationDistribution(
        p10=_dec(pcts[0]),
        p25=_dec(pcts[1]),
        p50=_dec(pcts[2]),
        p75=_dec(pcts[3]),
        p90=_dec(pcts[4]),
        mean=_dec(float(arr.mean())),
        std=_dec(float(arr.std(ddof=1) if arr.size > 1 else 0.0)),
    )


def _dec(x: float) -> Decimal:
    """Round to 4 decimal places for stable Pydantic comparison."""
    return Decimal(f"{x:.4f}")


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class ValuationModel(ABC):
    """Abstract base for every concrete valuation model.

    Subclasses MUST set ``model_name`` (str) and ``applicable_types``
    (list[ListingType]); the orchestrator uses ``applicable_types`` to
    skip models that don't apply (e.g. AH model for a non-AH listing).
    """

    model_name: ClassVar[str]
    applicable_types: ClassVar[list[ListingType]]

    def applies_to(self, listing_type: ListingType) -> bool:
        return listing_type in self.applicable_types

    @abstractmethod
    async def value(
        self,
        extraction: ProspectusExtraction,
        market_data: MarketData,
    ) -> SingleModelValuation:
        """Produce a SingleModelValuation for one IPO.

        Implementations should:
        1. Verify ``applies_to(extraction.listing_type)`` and either return a
           ``SingleModelValuation(applicable=False, ...)`` or proceed.
        2. Sample assumptions from documented distributions.
        3. Run ``monte_carlo.run_mc(...)`` to materialize 10k valuations.
        4. Convert samples to ``ValuationDistribution`` via
           ``distribution_from_samples``.
        5. Attach key assumptions + citations (from prospectus extraction).
        """

    @staticmethod
    def _not_applicable(
        *,
        reason: str,
        model_name: str,
    ) -> SingleModelValuation:
        zero = Decimal("0")
        return SingleModelValuation(
            model_name=model_name,
            applicable=False,
            valuation_distribution=ValuationDistribution(
                p10=zero, p25=zero, p50=zero, p75=zero, p90=zero, mean=zero, std=zero
            ),
            key_assumptions={"not_applicable_reason": reason},
            citations=[],
        )


__all__ = (
    "MarketData",
    "PeerMultiples",
    "ValuationModel",
    "distribution_from_samples",
)


def _citation_from_extraction(
    extraction: ProspectusExtraction, *, fallback_page: int = 1
) -> list[Citation]:
    """Best-effort page citation from any cited element in the extraction.

    Phase 4 valuation has limited per-cell citation needs — most models
    cite the financial snapshot or the listing rule sections globally.
    """
    citations: list[Citation] = []
    if extraction.financials:
        citations.append(extraction.financials[0].citation)
    elif extraction.risk_factors:
        citations.append(extraction.risk_factors[0].citation)
    else:
        citations.append(Citation(page=fallback_page))
    return citations
