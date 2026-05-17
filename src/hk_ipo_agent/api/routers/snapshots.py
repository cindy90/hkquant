"""Snapshot list / detail / memo-export / outcomes endpoints per PROJECT_SPEC.md §16.2."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import select

from ...common.enums import Permission
from ...data.database import async_session_factory
from ...data.models import PredictionOutcomeRow
from ...prediction_registry.registry import get_registry
from ...reporting import build_memo_markdown, export_docx, export_pdf
from ..auth.dependencies import CurrentUser, require_permission
from ..schemas import (
    PaginatedResponse,
    PaginationMeta,
    SnapshotSummary,
    snapshot_to_summary,
)

router = APIRouter(prefix="/api/snapshots", tags=["snapshots"])

# R6-1: every snapshot read endpoint gates on READ_SNAPSHOTS.
_SnapDep = Annotated[CurrentUser, Depends(require_permission(Permission.READ_SNAPSHOTS))]


@router.get("/", response_model=PaginatedResponse)
async def list_snapshots(
    user: _SnapDep,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> PaginatedResponse:
    _ = user
    snapshots = await get_registry().list_snapshots()
    snapshots = sorted(snapshots, key=lambda s: s.created_at, reverse=True)
    page = snapshots[offset : offset + limit]
    return PaginatedResponse(
        data=[snapshot_to_summary(s).model_dump(mode="json") for s in page],
        meta=PaginationMeta(
            total=len(snapshots),
            limit=limit,
            offset=offset,
            has_next=offset + limit < len(snapshots),
        ),
    )


@router.get("/{snapshot_id}", response_model=SnapshotSummary)
async def get_snapshot(snapshot_id: UUID, user: _SnapDep) -> SnapshotSummary:
    _ = user
    try:
        snap = await get_registry().get_snapshot(snapshot_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"snapshot {snapshot_id} not found",
        ) from exc
    return snapshot_to_summary(snap)


@router.get("/{snapshot_id}/memo.md")
async def get_memo_markdown(snapshot_id: UUID, user: _SnapDep) -> Response:
    _ = user
    snap = await get_registry().get_snapshot(snapshot_id)
    md = build_memo_markdown(snap)
    return Response(content=md, media_type="text/markdown; charset=utf-8")


@router.get("/{snapshot_id}/memo.pdf")
async def get_memo_pdf(snapshot_id: UUID, user: _SnapDep) -> Response:
    _ = user
    snap = await get_registry().get_snapshot(snapshot_id)
    pdf_bytes = export_pdf(snap)
    media = "application/pdf" if pdf_bytes[:4] == b"%PDF" else "text/html"
    return Response(content=pdf_bytes, media_type=media)


@router.get("/{snapshot_id}/memo.docx")
async def get_memo_docx(snapshot_id: UUID, user: _SnapDep) -> Response:
    _ = user
    snap = await get_registry().get_snapshot(snapshot_id)
    docx_bytes = export_docx(snap)
    return Response(
        content=docx_bytes,
        media_type=("application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    )


@router.get("/{snapshot_id}/outcomes")
async def get_snapshot_outcomes(
    snapshot_id: UUID, user: _SnapDep
) -> dict[str, Any]:
    """Return all T+N outcomes for a snapshot, ordered by checkpoint_day.

    R6-1: gated behind ``READ_SNAPSHOTS``.
    """
    _ = user
    sf = async_session_factory()
    async with sf() as session:
        stmt = (
            select(PredictionOutcomeRow)
            .where(PredictionOutcomeRow.snapshot_id == snapshot_id)
            .order_by(PredictionOutcomeRow.checkpoint_day.asc())
        )
        rows = (await session.execute(stmt)).scalars().all()

    outcomes = [
        {
            "snapshot_id": str(r.snapshot_id),
            "checkpoint_day": r.checkpoint_day,
            "return_since_ipo": str(r.return_since_ipo) if r.return_since_ipo is not None else None,
            "return_since_listing": str(r.return_since_listing) if r.return_since_listing is not None else None,
            "max_drawdown": str(r.max_drawdown) if r.max_drawdown is not None else None,
            "relative_return_hsi": str(r.relative_return_hsi) if r.relative_return_hsi is not None else None,
            "relative_return_hstech": str(r.relative_return_hstech) if r.relative_return_hstech is not None else None,
            "relative_return_industry": str(r.relative_return_industry) if r.relative_return_industry is not None else None,
            "earnings_released": r.earnings_released,
            "earnings_beat_extraction": r.earnings_beat_extraction,
            "cornerstone_held_pct": str(r.cornerstone_held_pct) if r.cornerstone_held_pct is not None else None,
            "cornerstone_reduced": r.cornerstone_reduced,
            "price_in_predicted_range": r.price_in_predicted_range,
            "decision_correct": r.decision_correct,
            "recorded_at": r.recorded_at.isoformat(),
        }
        for r in rows
    ]

    return {"snapshot_id": str(snapshot_id), "outcomes": outcomes}


__all__ = ("router",)
