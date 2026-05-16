"""Proposals router — Phase 7.5b implementation per ADR 0011 + ADR 0012.

Proposals are ``ProposedAdjustment`` blobs stored inside
``prediction_reviews.proposed_adjustments`` (JSONB). The router exposes
accept / reject endpoints that update the *parent review's*
``adjustment_status`` and write an audit trail entry; it never mutates
proposals in isolation. CLAUDE.md prediction-lifecycle constraint:
"system MUST NOT auto-apply any adjustment" — flipping a review to
ACCEPTED here just signals consent; the Phase 10 ``adjustment_applier``
is what eventually edits config files.

Endpoints:
- GET   /api/proposals                — list reviews with proposals
- POST  /api/proposals/{review_id}/accept
- POST  /api/proposals/{review_id}/reject
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from ...common.enums import AdjustmentStatus, Permission
from ...data.database import async_session_factory
from ...data.models import PredictionReviewRow
from ..auth.dependencies import CurrentUser, require_permission
from ..schemas import PaginatedResponse, PaginationMeta

router = APIRouter(prefix="/api/proposals", tags=["proposals"])

# Transitions allowed via this router. Phase 10's adjustment_applier
# moves accepted → implemented in a separate workflow.
_ALLOWED_FROM_STATES = {None, AdjustmentStatus.PROPOSED.value}


class _ProposalDecisionRequest(BaseModel):
    """Body for accept / reject."""

    reviewer: str = Field(..., min_length=1, max_length=100)
    rationale: str = Field("", max_length=2000)


class _ProposalView(BaseModel):
    review_id: UUID
    snapshot_id: UUID
    reviewer: str | None
    primary_attribution: str | None
    proposed_adjustments: list[dict[str, Any]]
    adjustment_status: str | None
    applied_at: datetime | None
    applied_version: str | None


def _row_to_view(row: PredictionReviewRow) -> _ProposalView:
    return _ProposalView(
        review_id=row.id,
        snapshot_id=row.snapshot_id,
        reviewer=row.reviewer,
        primary_attribution=row.primary_attribution,
        proposed_adjustments=list(row.proposed_adjustments or []),
        adjustment_status=row.adjustment_status,
        applied_at=row.applied_at,
        applied_version=row.applied_version,
    )


@router.get("/", response_model=PaginatedResponse)
async def list_proposals(
    user: Annotated[CurrentUser, Depends(require_permission(Permission.READ_PROPOSALS))],
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    adjustment_status: AdjustmentStatus | None = None,
) -> PaginatedResponse:
    _ = user
    stmt = (
        select(PredictionReviewRow)
        .where(PredictionReviewRow.proposed_adjustments.isnot(None))
        .order_by(PredictionReviewRow.created_at.desc())
        .offset(offset)
        .limit(limit + 1)
    )
    if adjustment_status is not None:
        stmt = stmt.where(PredictionReviewRow.adjustment_status == adjustment_status.value)
    async with async_session_factory()() as s:
        rows = (await s.execute(stmt)).scalars().all()
    has_next = len(rows) > limit
    rows = rows[:limit]
    return PaginatedResponse(
        data=[_row_to_view(r).model_dump(mode="json") for r in rows],
        meta=PaginationMeta(total=len(rows), limit=limit, offset=offset, has_next=has_next),
    )


async def _transition(
    review_id: UUID,
    new_status: AdjustmentStatus,
    reviewer: str,
) -> _ProposalView:
    """Single-shot state transition guarded by allowed_from_states."""
    async with async_session_factory()() as s:
        row = await s.get(PredictionReviewRow, review_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"review {review_id} not found",
            )
        current = row.adjustment_status
        if current not in _ALLOWED_FROM_STATES:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"cannot transition status={current} → {new_status.value}",
            )
        await s.execute(
            update(PredictionReviewRow)
            .where(PredictionReviewRow.id == review_id)
            .values(
                adjustment_status=new_status.value,
                reviewer=reviewer,
                applied_at=datetime.now(UTC) if new_status is AdjustmentStatus.ACCEPTED else None,
            )
        )
        await s.commit()
        # Re-fetch for fresh state.
        row = await s.get(PredictionReviewRow, review_id)
    assert row is not None  # noqa: S101
    return _row_to_view(row)


@router.post("/{review_id}/accept", response_model=_ProposalView)
async def accept_proposal(
    review_id: UUID,
    body: _ProposalDecisionRequest,
    user: Annotated[CurrentUser, Depends(require_permission(Permission.ACCEPT_PROPOSAL))],
) -> dict[str, Any]:
    _ = user, body.rationale  # rationale goes into audit middleware via request body
    view = await _transition(review_id, AdjustmentStatus.ACCEPTED, body.reviewer)
    return view.model_dump(mode="json")


@router.post("/{review_id}/reject", response_model=_ProposalView)
async def reject_proposal(
    review_id: UUID,
    body: _ProposalDecisionRequest,
    user: Annotated[CurrentUser, Depends(require_permission(Permission.REJECT_PROPOSAL))],
) -> dict[str, Any]:
    _ = user, body.rationale
    view = await _transition(review_id, AdjustmentStatus.REJECTED, body.reviewer)
    return view.model_dump(mode="json")


__all__ = ("router",)
