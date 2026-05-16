"""Phase 4 valuation layer — public surface.

Concrete models implement ``ValuationModel``; ``run_ensemble`` blends them
per ``config/valuation_weights.yaml`` and applies the Regime Gate
(ADR 0005 §2). ``MarketData`` is the runtime context every model receives.

Typical usage::

    from hk_ipo_agent.valuation import (
        ComparableValuation, DCFValuation, PreIPOAnchorValuation,
        AHPremiumValuation, MilestonesValuation, MarketData, run_ensemble,
    )
    from hk_ipo_agent.valuation.industry import industry_models

    models = (
        ComparableValuation(),
        DCFValuation(),
        PreIPOAnchorValuation(),
        AHPremiumValuation(),
        MilestonesValuation(),
        *industry_models(),
    )
    output = await run_ensemble(extraction, market_data, models)
"""

from __future__ import annotations

from .ah_premium import AHPremiumValuation
from .base import (
    MarketData,
    PeerMultiples,
    ValuationModel,
    distribution_from_samples,
)
from .comparable import ComparableValuation
from .dcf import DCFValuation
from .ensemble import REGIME_GATE_THRESHOLD, run_ensemble
from .milestones import MilestonesConfig, MilestoneStage, MilestonesValuation
from .monte_carlo import (
    DEFAULT_PATHS,
    Bernoulli,
    Constant,
    Distribution,
    FromArray,
    LogNormal,
    Normal,
    Triangular,
    Uniform,
    run_mc,
    sample_assumptions,
)
from .pre_ipo_anchor import PreIPOAnchorValuation

__all__ = (
    "DEFAULT_PATHS",
    "REGIME_GATE_THRESHOLD",
    "AHPremiumValuation",
    "Bernoulli",
    "ComparableValuation",
    "Constant",
    "DCFValuation",
    "Distribution",
    "FromArray",
    "LogNormal",
    "MarketData",
    "MilestoneStage",
    "MilestonesConfig",
    "MilestonesValuation",
    "Normal",
    "PeerMultiples",
    "PreIPOAnchorValuation",
    "Triangular",
    "Uniform",
    "ValuationModel",
    "distribution_from_samples",
    "run_ensemble",
    "run_mc",
    "sample_assumptions",
)
