"""Industry-specific valuation models.

Phase 4 ships two concrete implementations (``AIARRValuation``,
``SemiconductorValuation``); biotech_18a / ev_battery / robotics are
stubs to be filled in Phase 8 calibration once the industry KB has more
coverage.

Use ``industry_models_for(extraction)`` to pick the best-matching
specialization for an extraction; multiple models may match (the
ensemble will then run them all and let weights decide).
"""

from __future__ import annotations

from collections.abc import Iterable

from ...common.schemas import ProspectusExtraction
from ..base import ValuationModel
from .ai_arr import AIARRValuation
from .semiconductor import SemiconductorValuation


def industry_models() -> tuple[ValuationModel, ...]:
    """All implemented industry-specific valuation models."""
    return (AIARRValuation(), SemiconductorValuation())


def industry_models_for(extraction: ProspectusExtraction) -> Iterable[ValuationModel]:
    """Subset of industry models whose keyword matcher hits this extraction.

    The ensemble can safely include the full ``industry_models()`` set
    because non-matching models return ``applicable=False`` — this helper
    is a perf optimization for code paths that don't want to await them.
    """
    candidates: list[ValuationModel] = []
    for m in industry_models():
        matcher = getattr(m, "_matches_industry", None)
        if callable(matcher) and matcher(extraction):
            candidates.append(m)
    return candidates


__all__ = (
    "AIARRValuation",
    "SemiconductorValuation",
    "industry_models",
    "industry_models_for",
)
