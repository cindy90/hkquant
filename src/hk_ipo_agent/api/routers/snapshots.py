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


@router.get("/{snapshot_id}")
async def get_snapshot(snapshot_id: UUID, user: _SnapDep) -> dict[str, Any]:
    """Return full snapshot data (agent_outputs, valuation, debate, decision, etc.).

    The analysis page needs the complete payload — not just the summary fields.
    A thin transform adapts the internal schema to the UI's expected shape
    (e.g. nested ``price_range`` object, ``ScoreCard`` with ``weighted_total``).

    R6-1: gated behind ``READ_SNAPSHOTS``.
    """
    _ = user
    snap = await _get_snapshot_or_404(snapshot_id)
    data = snap.model_dump(mode="json")
    _adapt_snapshot_for_ui(data)
    return data


def _adapt_snapshot_for_ui(data: dict[str, Any]) -> None:
    """In-place transform to align internal schema with frontend FullSnapshot type.

    Frontend expects:
    - ``decision.price_range.{low, fair, high}`` (nested) instead of flat fields
    - ``decision.scorecard.{agent_scores, weighted_total, regime_gate_passed}``
    """
    decision = data.get("decision")
    if not decision:
        return

    # --- price_range: flat → nested -----------------------------------------
    if "price_range_low" in decision:
        decision["price_range"] = {
            "low": decision.pop("price_range_low"),
            "fair": decision.pop("price_range_fair"),
            "high": decision.pop("price_range_high"),
        }

    # --- scorecard: raw dict → ScoreCard object ------------------------------
    raw = decision.get("scorecard", {})
    if not isinstance(raw, dict) or "weighted_total" not in raw:
        overall = raw.pop("overall", 0) if isinstance(raw, dict) else 0
        decision["scorecard"] = {
            "agent_scores": raw if isinstance(raw, dict) else {},
            "weighted_total": overall,
            "regime_gate_passed": True,  # default; real value from pipeline
        }


async def _get_snapshot_or_404(snapshot_id: UUID) -> Any:
    """Fetch snapshot from registry; raise 404 if not found."""
    try:
        return await get_registry().get_snapshot(snapshot_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"snapshot {snapshot_id} not found",
        ) from exc


@router.get("/{snapshot_id}/memo.md")
async def get_memo_markdown(snapshot_id: UUID, user: _SnapDep) -> Response:
    _ = user
    snap = await _get_snapshot_or_404(snapshot_id)
    md = build_memo_markdown(snap)
    return Response(content=md, media_type="text/markdown; charset=utf-8")


@router.get("/{snapshot_id}/memo.pdf")
async def get_memo_pdf(snapshot_id: UUID, user: _SnapDep) -> Response:
    _ = user
    snap = await _get_snapshot_or_404(snapshot_id)
    pdf_bytes = export_pdf(snap)
    media = "application/pdf" if pdf_bytes[:4] == b"%PDF" else "text/html"
    return Response(content=pdf_bytes, media_type=media)


@router.get("/{snapshot_id}/memo.docx")
async def get_memo_docx(snapshot_id: UUID, user: _SnapDep) -> Response:
    _ = user
    snap = await _get_snapshot_or_404(snapshot_id)
    docx_bytes = export_docx(snap)
    return Response(
        content=docx_bytes,
        media_type=("application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    )


@router.get("/{snapshot_id}/outcomes")
async def get_snapshot_outcomes(snapshot_id: UUID, user: _SnapDep) -> dict[str, Any]:
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

    outcomes = [_outcome_row_to_dict(r) for r in rows]
    return {"snapshot_id": str(snapshot_id), "outcomes": outcomes}


def _outcome_row_to_dict(r: PredictionOutcomeRow) -> dict[str, Any]:
    """Serialize a PredictionOutcomeRow to a JSON-safe dict."""
    return {
        "snapshot_id": str(r.snapshot_id),
        "checkpoint_day": r.checkpoint_day,
        "return_since_ipo": str(r.return_since_ipo) if r.return_since_ipo is not None else None,
        "return_since_listing": str(r.return_since_listing)
        if r.return_since_listing is not None
        else None,
        "max_drawdown": str(r.max_drawdown) if r.max_drawdown is not None else None,
        "relative_return_hsi": str(r.relative_return_hsi)
        if r.relative_return_hsi is not None
        else None,
        "relative_return_hstech": str(r.relative_return_hstech)
        if r.relative_return_hstech is not None
        else None,
        "relative_return_industry": str(r.relative_return_industry)
        if r.relative_return_industry is not None
        else None,
        "earnings_released": r.earnings_released,
        "earnings_beat_extraction": r.earnings_beat_extraction,
        "cornerstone_held_pct": str(r.cornerstone_held_pct)
        if r.cornerstone_held_pct is not None
        else None,
        "cornerstone_reduced": r.cornerstone_reduced,
        "price_in_predicted_range": r.price_in_predicted_range,
        "decision_correct": r.decision_correct,
        "recorded_at": r.recorded_at.isoformat(),
    }


# --- /api/outcomes/recent — used by the UI Dashboard component. ----------
# Mounted on a separate APIRouter so the prefix doesn't collide with
# /api/snapshots/{snapshot_id}/outcomes above.

outcomes_router = APIRouter(prefix="/api/outcomes", tags=["outcomes"])


@outcomes_router.get("/recent")
async def recent_outcomes(
    user: _SnapDep,
    limit: int = Query(10, ge=1, le=100),
) -> dict[str, Any]:
    """Return the most recent prediction outcomes across all snapshots.

    The UI Dashboard polls this endpoint to populate the "Recent Checkpoints"
    widget.

    R6-1: gated behind ``READ_SNAPSHOTS`` (same surface as the per-snapshot
    /outcomes endpoint above).
    """
    _ = user
    sf = async_session_factory()
    async with sf() as session:
        stmt = (
            select(PredictionOutcomeRow)
            .order_by(PredictionOutcomeRow.recorded_at.desc())
            .limit(limit)
        )
        rows = (await session.execute(stmt)).scalars().all()

    return {"data": [_outcome_row_to_dict(r) for r in rows]}


__all__ = ("outcomes_router", "router")
