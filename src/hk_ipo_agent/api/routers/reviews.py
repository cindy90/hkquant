"""Reviews router — Phase 7.5b implementation per ADR 0011 + ADR 0012.

ADR 0011 Progress had this stubbed at 501; now wired against the
``prediction_reviews`` table + ReviewWorkflow.submit_review.

Endpoints:
- GET    /api/reviews                            — list recent reviews
- GET    /api/reviews/snapshot/{snapshot_id}     — list for one snapshot
- POST   /api/reviews/snapshot/{snapshot_id}     — submit a new review

Read endpoints require READ_REVIEWS; submit requires SUBMIT_REVIEW.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from ...common.enums import AdjustmentStatus, Permission
from ...data.database import async_session_factory
from ...data.models import PredictionReviewRow
from ..auth.dependencies import CurrentUser, require_permission
from ..schemas import PaginatedResponse, PaginationMeta

router = APIRouter(prefix="/api/reviews", tags=["reviews"])


class _ReviewSummary(BaseModel):
    """Wire-format summary for list / detail responses."""

    id: UUID
    snapshot_id: UUID
    review_checkpoint_day: int | None
    reviewer: str | None
    what_we_got_right: str | None
    what_we_got_wrong: str | None
    primary_attribution: str | None
    adjustment_status: str | None
    applied_version: str | None
    notes_md: str | None
    proposed_adjustments_count: int = Field(default=0)


class _ReviewSubmitRequest(BaseModel):
    reviewer: str = Field(..., min_length=1, max_length=100)
    what_we_got_right: str = Field("", max_length=2000)
    what_we_got_wrong: str = Field("", max_length=2000)
    notes_md: str = Field("", max_length=8000)
    review_checkpoint_day: int = Field(default=-1)
    adjustment_status: AdjustmentStatus = AdjustmentStatus.PROPOSED


def _row_to_summary(row: PredictionReviewRow) -> _ReviewSummary:
    proposed = row.proposed_adjustments or []
    return _ReviewSummary(
        id=row.id,
        snapshot_id=row.snapshot_id,
        review_checkpoint_day=row.review_checkpoint_day,
        reviewer=row.reviewer,
        what_we_got_right=row.what_we_got_right,
        what_we_got_wrong=row.what_we_got_wrong,
        primary_attribution=row.primary_attribution,
        adjustment_status=row.adjustment_status,
        applied_version=row.applied_version,
        notes_md=row.notes_md,
        proposed_adjustments_count=len(proposed),
    )


@router.get("/", response_model=PaginatedResponse)
async def list_reviews(
    user: Annotated[CurrentUser, Depends(require_permission(Permission.READ_REVIEWS))],
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    adjustment_status: AdjustmentStatus | None = None,
) -> PaginatedResponse:
    _ = user
    stmt = select(PredictionReviewRow).order_by(PredictionReviewRow.created_at.desc())
    if adjustment_status is not None:
        stmt = stmt.where(PredictionReviewRow.adjustment_status == adjustment_status.value)
    stmt = stmt.offset(offset).limit(limit + 1)
    async with async_session_factory()() as s:
        rows = (await s.execute(stmt)).scalars().all()
    has_next = len(rows) > limit
    rows = rows[:limit]
    return PaginatedResponse(
        data=[_row_to_summary(r).model_dump(mode="json") for r in rows],
        meta=PaginationMeta(total=len(rows), limit=limit, offset=offset, has_next=has_next),
    )


@router.get("/snapshot/{snapshot_id}", response_model=PaginatedResponse)
async def list_reviews_for_snapshot(
    snapshot_id: UUID,
    user: Annotated[CurrentUser, Depends(require_permission(Permission.READ_REVIEWS))],
    limit: int = Query(50, ge=1, le=500),
) -> PaginatedResponse:
    _ = user
    stmt = (
        select(PredictionReviewRow)
        .where(PredictionReviewRow.snapshot_id == snapshot_id)
        .order_by(PredictionReviewRow.created_at.desc())
        .limit(limit)
    )
    async with async_session_factory()() as s:
        rows = (await s.execute(stmt)).scalars().all()
    return PaginatedResponse(
        data=[_row_to_summary(r).model_dump(mode="json") for r in rows],
        meta=PaginationMeta(total=len(rows), limit=limit, offset=0, has_next=False),
    )


@router.post(
    "/snapshot/{snapshot_id}",
    response_model=_ReviewSummary,
    status_code=status.HTTP_201_CREATED,
)
async def submit_review(
    snapshot_id: UUID,
    body: _ReviewSubmitRequest,
    user: Annotated[CurrentUser, Depends(require_permission(Permission.SUBMIT_REVIEW))],
) -> dict[str, Any]:
    # Use the workflow so we honour Phase 7.5b semantics (status default,
    # stub attribution etc.). Lazy import avoids module-init cycles.
    from ...common.llm_client import LLMClient  # noqa: PLC0415
    from ...prediction_registry.attribution import AttributionEngine  # noqa: PLC0415
    from ...prediction_registry.registry import get_registry  # noqa: PLC0415
    from ...prediction_registry.review_workflow import ReviewWorkflow  # noqa: PLC0415

    registry = get_registry()
    try:
        snap = await registry.get_snapshot(snapshot_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"snapshot {snapshot_id} not found",
        ) from exc

    workflow = ReviewWorkflow(
        registry=registry,
        attribution=AttributionEngine(llm=LLMClient()),
        session_factory=async_session_factory(),
    )
    review_id = await workflow.submit_review(
        snapshot_id=snap.id,
        reviewer=body.reviewer,
        what_we_got_right=body.what_we_got_right,
        what_we_got_wrong=body.what_we_got_wrong,
        notes_md=body.notes_md,
        adjustment_status=body.adjustment_status,
        review_checkpoint_day=body.review_checkpoint_day,
    )
    # Echo the just-written row.
    stmt = select(PredictionReviewRow).where(PredictionReviewRow.id == review_id)
    async with async_session_factory()() as s:
        row = (await s.execute(stmt)).scalar_one()
    summary = _row_to_summary(row)
    _ = user
    return summary.model_dump(mode="json")


__all__ = ("router",)
