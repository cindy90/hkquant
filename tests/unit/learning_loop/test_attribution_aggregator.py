"""attribution_aggregator.py tests — Phase 10a per ADR 0015."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from hk_ipo_agent.common.enums import AgentRole, ListingType
from hk_ipo_agent.learning_loop.attribution_aggregator import (
    AggregatorConfig,
    AttributionAggregator,
    ReviewRecord,
)


def _review(
    primary: str,
    *,
    listing_type: ListingType | None = ListingType.MAINBOARD_TECH,
    agent_role: AgentRole | None = None,
) -> ReviewRecord:
    return ReviewRecord(
        review_id=uuid.uuid4(),
        snapshot_id=uuid.uuid4(),
        primary_attribution=primary,
        listing_type=listing_type,
        agent_role=agent_role,
        created_at=datetime.now(UTC),
    )


def test_aggregator_empty_input_returns_empty() -> None:
    agg = AttributionAggregator()
    assert agg.aggregate([]) == []


def test_aggregator_below_threshold_returns_empty() -> None:
    agg = AttributionAggregator(AggregatorConfig(min_occurrences=3, min_share=0.30))
    # 2 reviews share an attribution — below min_occurrences=3
    reviews = [_review("valuation_model") for _ in range(2)]
    reviews += [_review("agent:bull") for _ in range(3)]
    findings = agg.aggregate(reviews)
    # valuation_model not surfaced (occurrences=2 < 3); agent:bull is.
    assert all("valuation_model" not in f.attribution_key for f in findings)


def test_aggregator_overall_slice_fires_on_dominant_attribution() -> None:
    agg = AttributionAggregator(AggregatorConfig(min_occurrences=3, min_share=0.30))
    reviews = [_review("valuation_model") for _ in range(5)]
    reviews += [_review("policy_change") for _ in range(2)]
    findings = agg.aggregate(reviews)
    overall = [f for f in findings if f.slice_dimension == "all"]
    assert any(f.primary_attribution == "valuation_model" for f in overall)


def test_aggregator_slice_by_listing_type() -> None:
    """One attribution that's only common in one listing_type → slice fires."""
    agg = AttributionAggregator(AggregatorConfig(min_occurrences=3, min_share=0.50))
    reviews: list[ReviewRecord] = []
    # MB-TECH: 5 reviews, all attributed to valuation_model
    for _ in range(5):
        reviews.append(_review("valuation_model", listing_type=ListingType.MAINBOARD_TECH))
    # 18A: 5 reviews, attributed to sentiment_overreaction
    for _ in range(5):
        reviews.append(_review("sentiment_overreaction", listing_type=ListingType.CH18A_BIOTECH))

    findings = agg.aggregate(reviews)
    lt_findings = [f for f in findings if f.slice_dimension == "listing_type"]
    mb_tech = [f for f in lt_findings if f.slice_value == "MB-TECH"]
    biotech = [f for f in lt_findings if f.slice_value == "18A"]
    assert len(mb_tech) == 1
    assert mb_tech[0].primary_attribution == "valuation_model"
    assert biotech[0].primary_attribution == "sentiment_overreaction"


def test_aggregator_slice_by_agent_role() -> None:
    agg = AttributionAggregator(AggregatorConfig(min_occurrences=3, min_share=0.40))
    reviews = [_review("over_optimism", agent_role=AgentRole.FUNDAMENTAL) for _ in range(5)]
    reviews += [_review("missed_clue", agent_role=AgentRole.SENTIMENT) for _ in range(2)]
    findings = agg.aggregate(reviews)
    agent_findings = [f for f in findings if f.slice_dimension == "agent_role"]
    assert any(f.slice_value == "fundamental" for f in agent_findings)


def test_aggregator_severity_escalates_with_occurrences() -> None:
    """≥10 occurrences AND ≥50% share → critical."""
    agg = AttributionAggregator(AggregatorConfig(min_occurrences=3, min_share=0.30))
    reviews = [_review("systematic_overbid") for _ in range(12)]
    reviews += [_review("noise") for _ in range(2)]
    findings = agg.aggregate(reviews)
    crits = [f for f in findings if f.severity == "critical"]
    assert crits


def test_aggregator_finding_to_dict_round_trip() -> None:
    agg = AttributionAggregator(AggregatorConfig(min_occurrences=3, min_share=0.30))
    reviews = [_review("valuation_model") for _ in range(5)]
    findings = agg.aggregate(reviews)
    d = findings[0].to_dict()
    assert "attribution_key" in d
    assert d["primary_attribution"] == "valuation_model"
    assert d["occurrences"] == 5
    assert d["share"] == pytest.approx(1.0)
