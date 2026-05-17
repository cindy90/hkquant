"""Drift router — Phase 7.5b MVP per ADR 0011 + ADR 0012.

Phase 10's ``learning_loop/drift_detector.py`` will eventually compute
proper CUSUM / PSI drift signals over many outcomes. For Phase 7.5b
MVP this endpoint aggregates ``prediction_reviews.attribution_details``
into a flat "recent issues" view so the UI dashboard can render a
basic drift panel.

Endpoint:
- GET /api/drift — list recent reviews grouped by primary_attribution
"""

from __future__ import annotations

from collections import Counter
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from ...common.enums import Permission
from ...data.database import async_session_factory
from ...data.models import PredictionReviewRow
from ..auth.dependencies import CurrentUser, require_permission

router = APIRouter(prefix="/api/drift", tags=["drift"])


class _DriftBucket(BaseModel):
    """One primary_attribution bucket — what's been the most common cause lately."""

    primary_attribution: str
    review_count: int = Field(ge=0)
    snapshots: list[UUID] = Field(default_factory=list)


class _DriftSummary(BaseModel):
    total_reviews_scanned: int = Field(ge=0)
    buckets: list[_DriftBucket]
    note: str = Field(
        default=(
            "MVP aggregate. Phase 10 learning_loop/drift_detector will replace "
            "this with CUSUM + PSI signals over outcome streams."
        ),
    )


@router.get("/", response_model=_DriftSummary)
async def list_drift_buckets(
    user: Annotated[CurrentUser, Depends(require_permission(Permission.READ_REVIEWS))],
    limit: int = Query(200, ge=1, le=1000),
) -> dict[str, Any]:
    """Aggregate recent reviews by primary_attribution."""
    _ = user
    stmt = (
        select(
            PredictionReviewRow.id,
            PredictionReviewRow.snapshot_id,
            PredictionReviewRow.primary_attribution,
        )
        .order_by(PredictionReviewRow.created_at.desc())
        .limit(limit)
    )
    async with async_session_factory()() as s:
        rows = list((await s.execute(stmt)).all())

    counter: Counter[str] = Counter()
    snapshots_by_attr: dict[str, list[UUID]] = {}
    for _id, snapshot_id, primary_attribution in rows:
        key = primary_attribution or "unclassified"
        counter[key] += 1
        snapshots_by_attr.setdefault(key, []).append(snapshot_id)

    buckets = [
        _DriftBucket(
            primary_attribution=k,
            review_count=v,
            snapshots=snapshots_by_attr[k][:50],  # cap per bucket
        )
        for k, v in counter.most_common()
    ]
    return _DriftSummary(
        total_reviews_scanned=len(rows),
        buckets=buckets,
    ).model_dump(mode="json")


__all__ = ("router",)
