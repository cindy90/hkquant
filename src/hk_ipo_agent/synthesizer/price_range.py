"""Price-range derivation from the valuation ensemble.

Per PROJECT_SPEC.md §3.7 / §7. The ensemble already exports
``implied_price_range = {low, fair, high}`` keyed off P25 / P50 / P75
of the blended distribution (Phase 4 convention). This module:

1. Re-exports the triplet typed as ``Decimal``.
2. Applies a regime-aware widening if regime_score is borderline (in
   ``[-0.05, +0.05]``) — widens by ±10% to flag uncertainty.
3. Forces all three to 0 when Regime Gate has been triggered (defense in
   depth — already done by ensemble.py but we re-check here per ADR 0005 §2
   "defense-in-depth").

Returns ``(low, fair, high)`` Decimals.
"""

from __future__ import annotations

from decimal import Decimal

from ..common.schemas import ValuationEnsembleOutput

_REGIME_GATE_THRESHOLD: float = 0.0


def derive_price_range(
    ensemble: ValuationEnsembleOutput,
    *,
    regime_score: float | None,
) -> tuple[Decimal, Decimal, Decimal]:
    """Return ``(low, fair, high)`` price triplet for the final decision."""
    pr = ensemble.implied_price_range
    low = pr.get("low", Decimal("0"))
    fair = pr.get("fair", Decimal("0"))
    high = pr.get("high", Decimal("0"))

    # Defense-in-depth: if regime gate negative, force zero (synthesizer
    # downstream will still emit DecisionType.SKIP).
    if regime_score is not None and regime_score < _REGIME_GATE_THRESHOLD:
        zero = Decimal("0")
        return zero, zero, zero

    # Borderline regime widens the band by ±10%.
    if regime_score is not None and abs(regime_score) <= 0.05:
        widen = Decimal("0.10")
        low = (low * (Decimal("1") - widen)).quantize(Decimal("0.0001"))
        high = (high * (Decimal("1") + widen)).quantize(Decimal("0.0001"))

    return low, fair, high


__all__ = ("derive_price_range",)
