"""Backtest router — Phase 8d per ADR 0011 + ADR 0013 §8d.

Surfaces walk-forward runs persisted by ``backtest/runner.persist_run_to_pg``
as a list / detail API for the UI dashboard.

Storage shape (ADR 0013 §8d — no new tables): each historical IPO in a
backtest run gets one ``prediction_snapshots`` row carrying
``config_snapshot["backtest_run_id"] = run_id``. The list endpoint
groups by that key; the detail endpoint aggregates the per-IPO rows
into metrics + samples.

Endpoints:
- ``GET /api/backtest/runs``                — paginated list of runs
- ``GET /api/backtest/runs/{run_id}``        — detail for one run
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ...common.enums import Permission
from ...data.database import async_session_factory
from ...data.models import PredictionSnapshotRow
from ..auth.dependencies import CurrentUser, require_permission
from ..schemas import PaginatedResponse, PaginationMeta

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


# ===========================================================================
# Response models
# ===========================================================================


class _BacktestRunSummary(BaseModel):
    """Top-level summary of one backtest run."""

    run_id: UUID
    n_samples: int = Field(ge=0)
    earliest_pricing_date: str | None
    latest_pricing_date: str | None
    earliest_created_at: str
    latest_created_at: str
    scorer: str | None = None
    horizons: list[str] = Field(default_factory=list)


class _BacktestSampleView(BaseModel):
    """One IPO inside a backtest run."""

    snapshot_id: UUID
    ipo_id: UUID
    stock_code: str | None
    listing_type: str | None
    pricing_date: str | None
    as_of_date: str
    decision_score: float | None
    regime_score: float | None
    regime_pass: bool | None
    realized_returns: dict[str, float] = Field(default_factory=dict)


class _BacktestRunDetail(BaseModel):
    """Full detail for one backtest run."""

    run_id: UUID
    n_samples: int = Field(ge=0)
    scorer: str | None
    horizons: list[str]
    config_snapshot: dict[str, Any]
    samples: list[_BacktestSampleView]


# ===========================================================================
# Helpers
# ===========================================================================


def _row_to_sample_view(row: PredictionSnapshotRow) -> _BacktestSampleView:
    """Project a backtest-marked snapshot row into a sample view."""
    input_data = row.input_data_snapshot or {}
    valuation = row.valuation_output or {}
    decision = row.decision or {}
    return _BacktestSampleView(
        snapshot_id=row.id,
        ipo_id=row.ipo_id,
        stock_code=input_data.get("stock_code"),
        listing_type=input_data.get("listing_type"),
        pricing_date=input_data.get("pricing_date"),
        as_of_date=row.as_of_date.isoformat(),
        decision_score=valuation.get("decision_score"),
        regime_score=valuation.get("regime_score"),
        regime_pass=valuation.get("regime_pass"),
        realized_returns=decision.get("realized_returns", {}),
    )


def _config_get(row: PredictionSnapshotRow, key: str) -> Any:
    return (row.config_snapshot or {}).get(key)


# Marker we look for to identify backtest rows.
_BACKTEST_KEY = "backtest_run_id"


# ===========================================================================
# Endpoints
# ===========================================================================


@router.get("/runs", response_model=PaginatedResponse)
async def list_backtest_runs(
    user: Annotated[CurrentUser, Depends(require_permission(Permission.READ_SETTINGS))],
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> PaginatedResponse:
    """List distinct backtest runs (paginated, newest first).

    Aggregates rows in ``prediction_snapshots`` where ``config_snapshot``
    has a ``backtest_run_id`` key.
    """
    _ = user
    # Fetch rows carrying our marker. The PG JSONB ``?`` containment
    # operator surfaces here via SQLAlchemy's ``has_key`` (PG-specific).
    stmt = (
        select(PredictionSnapshotRow)
        .where(PredictionSnapshotRow.config_snapshot.has_key(_BACKTEST_KEY))
        .order_by(PredictionSnapshotRow.created_at.desc())
    )
    sf = async_session_factory()
    async with sf() as s:
        rows = list((await s.execute(stmt)).scalars().all())

    # Group by run_id (preserve newest-first ordering of latest_created_at).
    grouped: dict[str, list[PredictionSnapshotRow]] = {}
    for row in rows:
        rid = str(_config_get(row, _BACKTEST_KEY))
        grouped.setdefault(rid, []).append(row)

    runs: list[_BacktestRunSummary] = []
    for rid, members in grouped.items():
        pricing_dates: list[str] = [
            pd for m in members if (pd := (m.input_data_snapshot or {}).get("pricing_date"))
        ]
        created = [m.created_at for m in members]
        scorer = _config_get(members[0], "scorer")
        horizons = _config_get(members[0], "horizons") or []
        runs.append(
            _BacktestRunSummary(
                run_id=UUID(rid),
                n_samples=len(members),
                earliest_pricing_date=min(pricing_dates) if pricing_dates else None,
                latest_pricing_date=max(pricing_dates) if pricing_dates else None,
                earliest_created_at=min(created).isoformat(),
                latest_created_at=max(created).isoformat(),
                scorer=scorer,
                horizons=list(horizons),
            )
        )
    # Newest run first (by latest_created_at).
    runs.sort(key=lambda r: r.latest_created_at, reverse=True)
    page = runs[offset : offset + limit]
    return PaginatedResponse(
        data=[r.model_dump(mode="json") for r in page],
        meta=PaginationMeta(
            total=len(runs),
            limit=limit,
            offset=offset,
            has_next=offset + limit < len(runs),
        ),
    )


@router.get("/runs/{run_id}", response_model=_BacktestRunDetail)
async def get_backtest_run(
    run_id: UUID,
    user: Annotated[CurrentUser, Depends(require_permission(Permission.READ_SETTINGS))],
) -> _BacktestRunDetail:
    """Detail for one backtest run — all samples + headline config."""
    _ = user
    sf = async_session_factory()
    async with sf() as s:
        stmt = (
            select(PredictionSnapshotRow)
            .where(PredictionSnapshotRow.config_snapshot["backtest_run_id"].astext == str(run_id))
            .order_by(PredictionSnapshotRow.as_of_date.asc())
        )
        rows = list((await s.execute(stmt)).scalars().all())

    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"backtest run {run_id} not found",
        )

    samples = [_row_to_sample_view(r) for r in rows]
    head = rows[0]
    config = head.config_snapshot or {}
    return _BacktestRunDetail(
        run_id=run_id,
        n_samples=len(samples),
        scorer=config.get("scorer"),
        horizons=list(config.get("horizons") or []),
        config_snapshot=config,
        samples=samples,
    )


# Cheap aggregate ping used by tests / health checks — proves the
# router is mounted without requiring data.
@router.get("/runs/_meta/count")
async def runs_count(
    user: Annotated[CurrentUser, Depends(require_permission(Permission.READ_SETTINGS))],
) -> dict[str, int]:
    """Return the count of unique backtest_run_id values currently visible."""
    _ = user
    stmt = select(
        func.count(func.distinct(PredictionSnapshotRow.config_snapshot["backtest_run_id"].astext))
    ).where(PredictionSnapshotRow.config_snapshot.has_key(_BACKTEST_KEY))
    sf = async_session_factory()
    async with sf() as s:
        row = (await s.execute(stmt)).scalar_one_or_none() or 0
    return {"runs": int(row)}


__all__ = ("router",)
