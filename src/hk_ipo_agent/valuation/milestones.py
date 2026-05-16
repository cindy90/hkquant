"""Pre-commercial milestone option model (18C-pre / 18A biotech).

Per PROJECT_SPEC.md §3.7. Algorithm: expected NPV across staged
milestones, each with (a) Bernoulli success probability, (b) LogNormal
conditional enterprise value if reached, (c) Triangular discount rate
to PV the conditional value back to today.

Default 4-stage ladder (override via ``market_data.extra["milestones"]["stages"]``):

| Stage    | p_success | cond_EV_median (RMB) | cond_EV_sigma | discount |
|----------|-----------|----------------------|----------------|----------|
| PoC      | 0.55      | 3e9                  | 0.4            | 0.20-0.30 |
| Pilot    | 0.40      | 1e10                 | 0.5            | 0.18-0.25 |
| Commerc. | 0.30      | 4e10                 | 0.6            | 0.15-0.22 |
| Scale    | 0.20      | 1.5e11               | 0.7            | 0.12-0.18 |

Expected NPV per path = sum_stage [ Bernoulli(p) * LogNormal(median, sigma) /
                                     (1+discount)^years_to_milestone ].
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import ClassVar

import numpy as np

from ..common.enums import ListingType
from ..common.schemas import (
    ProspectusExtraction,
    SingleModelValuation,
)
from .base import (
    MarketData,
    ValuationModel,
    _citation_from_extraction,
    distribution_from_samples,
)
from .monte_carlo import Bernoulli, Constant, Distribution, LogNormal, Triangular, run_mc


@dataclass
class MilestoneStage:
    """One stage in the milestone ladder."""

    name: str
    p_success: float
    cond_ev_median_rmb: float
    cond_ev_sigma: float
    discount_low: float
    discount_high: float
    years_to_milestone: float

    def as_assumptions(self) -> dict[str, Distribution]:
        mu = float(np.log(max(self.cond_ev_median_rmb, 1.0)))
        # Triangular requires low < high; degenerate ranges → Constant.
        discount_dist: Distribution
        if self.discount_low >= self.discount_high:
            discount_dist = Constant(value=self.discount_low)
        else:
            discount_dist = Triangular(
                low=self.discount_low,
                mode=(self.discount_low + self.discount_high) / 2.0,
                high=self.discount_high,
            )
        return {
            f"{self.name}__success": Bernoulli(p=self.p_success),
            f"{self.name}__ev": LogNormal(mu=mu, sigma=self.cond_ev_sigma),
            f"{self.name}__discount": discount_dist,
        }


_DEFAULT_LADDER: list[MilestoneStage] = [
    MilestoneStage("poc", 0.55, 3e9, 0.4, 0.20, 0.30, 1.0),
    MilestoneStage("pilot", 0.40, 1e10, 0.5, 0.18, 0.25, 2.0),
    MilestoneStage("commerc", 0.30, 4e10, 0.6, 0.15, 0.22, 3.0),
    MilestoneStage("scale", 0.20, 1.5e11, 0.7, 0.12, 0.18, 5.0),
]


@dataclass
class MilestonesConfig:
    stages: list[MilestoneStage] = field(default_factory=lambda: list(_DEFAULT_LADDER))


class MilestonesValuation(ValuationModel):
    """Sum of staged real-option NPVs. Outputs equity value in RMB."""

    model_name = "milestones"
    applicable_types: ClassVar[list[ListingType]] = [
        ListingType.CH18C_PRE_COMMERCIAL,
        ListingType.CH18A_BIOTECH,
    ]

    async def value(
        self,
        extraction: ProspectusExtraction,
        market_data: MarketData,
    ) -> SingleModelValuation:
        if not self.applies_to(extraction.listing_type):
            return self._not_applicable(
                reason="milestones model only applies to pre-commercial 18C / 18A",
                model_name=self.model_name,
            )

        overrides = (market_data.extra or {}).get("milestones", {}) or {}
        cfg: MilestonesConfig = overrides.get("config", MilestonesConfig())
        if not cfg.stages:
            return self._not_applicable(
                reason="empty milestone ladder",
                model_name=self.model_name,
            )

        assumptions: dict[str, Distribution] = {}
        for stage in cfg.stages:
            assumptions.update(stage.as_assumptions())

        def payoff(s: dict[str, np.ndarray]) -> np.ndarray:
            total = np.zeros_like(next(iter(s.values())))
            for stage in cfg.stages:
                succ = s[f"{stage.name}__success"]
                ev = s[f"{stage.name}__ev"]
                disc = s[f"{stage.name}__discount"]
                pv = succ * ev / np.power(1.0 + disc, stage.years_to_milestone)
                total = total + pv
            return total

        samples = run_mc(assumptions, payoff, seed=market_data.extra.get("mc_seed"))
        dist = distribution_from_samples(samples)

        key_assumptions = {
            "stage_count": len(cfg.stages),
            "stages": [
                {
                    "name": s.name,
                    "p_success": s.p_success,
                    "cond_ev_median_rmb": s.cond_ev_median_rmb,
                    "years": s.years_to_milestone,
                }
                for s in cfg.stages
            ],
        }

        return SingleModelValuation(
            model_name=self.model_name,
            applicable=True,
            valuation_distribution=dist,
            key_assumptions=key_assumptions,
            citations=_citation_from_extraction(extraction),
        )


__all__ = ("MilestoneStage", "MilestonesConfig", "MilestonesValuation")


_ = Decimal  # type re-export marker
