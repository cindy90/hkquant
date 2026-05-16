"""Snapshot list / detail / memo-export endpoints per PROJECT_SPEC.md §16.2."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import Response

from ...prediction_registry.registry import get_registry
from ...reporting import build_memo_markdown, export_docx, export_pdf
from ..auth import CurrentUserDep
from ..schemas import (
    PaginatedResponse,
    PaginationMeta,
    SnapshotSummary,
    snapshot_to_summary,
)

router = APIRouter(prefix="/api/snapshots", tags=["snapshots"])


@router.get("/", response_model=PaginatedResponse)
async def list_snapshots(
    user: CurrentUserDep,
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
async def get_snapshot(snapshot_id: UUID, user: CurrentUserDep) -> SnapshotSummary:
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
async def get_memo_markdown(snapshot_id: UUID, user: CurrentUserDep) -> Response:
    _ = user
    snap = await get_registry().get_snapshot(snapshot_id)
    md = build_memo_markdown(snap)
    return Response(content=md, media_type="text/markdown; charset=utf-8")


@router.get("/{snapshot_id}/memo.pdf")
async def get_memo_pdf(snapshot_id: UUID, user: CurrentUserDep) -> Response:
    _ = user
    snap = await get_registry().get_snapshot(snapshot_id)
    pdf_bytes = export_pdf(snap)
    media = "application/pdf" if pdf_bytes[:4] == b"%PDF" else "text/html"
    return Response(content=pdf_bytes, media_type=media)


@router.get("/{snapshot_id}/memo.docx")
async def get_memo_docx(snapshot_id: UUID, user: CurrentUserDep) -> Response:
    _ = user
    snap = await get_registry().get_snapshot(snapshot_id)
    docx_bytes = export_docx(snap)
    return Response(
        content=docx_bytes,
        media_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
    )


__all__ = ("router",)
