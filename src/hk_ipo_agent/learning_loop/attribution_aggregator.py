"""Cross-sample attribution aggregator — Phase 10a per ADR 0015.

Aggregates ``prediction_reviews.attribution_details.primary_attribution``
across recent reviews to find **systemic** bias patterns (a single
attribution that recurs across many snapshots, sliced by listing_type
or agent_role).

Spec §3.12: "find systematic deviation patterns: a certain agent
systematically overestimates a class of company; a valuation model
biased on a certain industry". This module produces the structured
findings that the downstream ``AdjustmentProposer`` turns into
``ProposedAdjustment`` records.

CLAUDE.md "no auto-apply" — pure analysis layer, no mutation.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from ..common.enums import AgentRole, ListingType
from ..common.logging import get_logger

logger = get_logger(__name__)


# Default thresholds: a finding only fires once N reviews share the
# same attribution within a slice — avoids surfacing one-off noise.
DEFAULT_MIN_OCCURRENCES: int = 3
DEFAULT_MIN_SHARE: float = 0.30  # 30% of slice's reviews


@dataclass(frozen=True)
class ReviewRecord:
    """Minimal projection of ``prediction_reviews`` for aggregation.

    Decouples loader from analysis — tests build records in-memory;
    CLI loads from PG. Field names mirror the ORM column names.
    """

    review_id: UUID
    snapshot_id: UUID
    primary_attribution: str   # e.g. "valuation_model" / "agent:fundamental"
    listing_type: ListingType | None
    agent_role: AgentRole | None  # the agent at fault, if any
    created_at: datetime


@dataclass(frozen=True)
class AggregatedFinding:
    """One systemic pattern found by the aggregator."""

    attribution_key: str  # e.g. "valuation_model|18C-COMM"
    primary_attribution: str
    slice_dimension: str  # "listing_type" / "agent_role" / "all"
    slice_value: str
    occurrences: int
    share: float  # occurrences / slice_total
    related_review_ids: tuple[UUID, ...] = field(default_factory=tuple)
    related_snapshot_ids: tuple[UUID, ...] = field(default_factory=tuple)
    severity: str = "info"  # info / warning / critical

    def to_dict(self) -> dict[str, Any]:
        return {
            "attribution_key": self.attribution_key,
            "primary_attribution": self.primary_attribution,
            "slice_dimension": self.slice_dimension,
            "slice_value": self.slice_value,
            "occurrences": self.occurrences,
            "share": self.share,
            "review_count": len(self.related_review_ids),
            "severity": self.severity,
        }


@dataclass(frozen=True)
class AggregatorConfig:
    min_occurrences: int = DEFAULT_MIN_OCCURRENCES
    min_share: float = DEFAULT_MIN_SHARE


class AttributionAggregator:
    """Stateless aggregator — input list of reviews, output findings."""

    def __init__(self, config: AggregatorConfig | None = None) -> None:
        self._cfg = config or AggregatorConfig()

    def aggregate(self, reviews: list[ReviewRecord]) -> list[AggregatedFinding]:
        """Produce findings from the review list.

        Slices: (1) overall (no slicing), (2) per listing_type,
        (3) per agent_role when present.
        """
        if not reviews:
            return []
        findings: list[AggregatedFinding] = []
        findings.extend(self._slice_overall(reviews))
        findings.extend(self._slice_by(reviews, "listing_type"))
        findings.extend(self._slice_by(reviews, "agent_role"))
        return findings

    # ------------------------------------------------------------------
    # Slicing helpers
    # ------------------------------------------------------------------

    def _slice_overall(self, reviews: list[ReviewRecord]) -> list[AggregatedFinding]:
        counter = Counter(r.primary_attribution for r in reviews)
        total = len(reviews)
        out: list[AggregatedFinding] = []
        for attribution, occurrences in counter.most_common():
            share = occurrences / total
            if (
                occurrences < self._cfg.min_occurrences
                or share < self._cfg.min_share
            ):
                continue
            members = [r for r in reviews if r.primary_attribution == attribution]
            out.append(
                AggregatedFinding(
                    attribution_key=f"all|{attribution}",
                    primary_attribution=attribution,
                    slice_dimension="all",
                    slice_value="all",
                    occurrences=occurrences,
                    share=share,
                    related_review_ids=tuple(m.review_id for m in members),
                    related_snapshot_ids=tuple(m.snapshot_id for m in members),
                    severity=_severity(occurrences, share),
                )
            )
        return out

    def _slice_by(
        self, reviews: list[ReviewRecord], dimension: str,
    ) -> list[AggregatedFinding]:
        """Group reviews by the named dimension before counting attributions."""
        groups: dict[str, list[ReviewRecord]] = defaultdict(list)
        for r in reviews:
            value: str | None
            if dimension == "listing_type":
                value = r.listing_type.value if r.listing_type else None
            elif dimension == "agent_role":
                value = r.agent_role.value if r.agent_role else None
            else:
                value = None
            if value is None:
                continue
            groups[value].append(r)

        out: list[AggregatedFinding] = []
        for slice_value, members in groups.items():
            slice_total = len(members)
            counter = Counter(r.primary_attribution for r in members)
            for attribution, occurrences in counter.most_common():
                share = occurrences / slice_total
                if (
                    occurrences < self._cfg.min_occurrences
                    or share < self._cfg.min_share
                ):
                    continue
                rel = [r for r in members if r.primary_attribution == attribution]
                out.append(
                    AggregatedFinding(
                        attribution_key=f"{dimension}={slice_value}|{attribution}",
                        primary_attribution=attribution,
                        slice_dimension=dimension,
                        slice_value=slice_value,
                        occurrences=occurrences,
                        share=share,
                        related_review_ids=tuple(m.review_id for m in rel),
                        related_snapshot_ids=tuple(m.snapshot_id for m in rel),
                        severity=_severity(occurrences, share),
                    )
                )
        return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _severity(occurrences: int, share: float) -> str:
    """Map (occurrences, share) → severity tag for downstream filtering."""
    if occurrences >= 10 and share >= 0.50:
        return "critical"
    if occurrences >= 5 and share >= 0.40:
        return "warning"
    return "info"


__all__ = (
    "DEFAULT_MIN_OCCURRENCES",
    "DEFAULT_MIN_SHARE",
    "AggregatedFinding",
    "AggregatorConfig",
    "AttributionAggregator",
    "ReviewRecord",
)

# Suppress unused-import noise.
_ = (UTC, datetime)
