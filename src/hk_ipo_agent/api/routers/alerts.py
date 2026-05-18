"""Alerts list / acknowledge per PROJECT_SPEC.md §16.

Phase 7 MVP: in-memory alert store (similar pattern to audit_log). The
alerts router exposes a paginated list + an acknowledge endpoint.
Phase 7.5 binds against PostgreSQL ``alerts`` table.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status

from ...common.enums import AlertLevel, Permission
from ...common.schemas import Alert
from ..auth.dependencies import CurrentUser, require_permission
from ..schemas import AlertAck, PaginatedResponse, PaginationMeta

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


class AlertStore:
    """In-memory alert store."""

    def __init__(self) -> None:
        self._alerts: dict[UUID, Alert] = {}
        self._ids: list[UUID] = []
        self._lock = asyncio.Lock()

    async def add(self, alert: Alert) -> UUID:
        alert_id = uuid4()
        async with self._lock:
            self._alerts[alert_id] = alert
            self._ids.append(alert_id)
        return alert_id

    async def list(self, *, limit: int, offset: int) -> tuple[list[tuple[UUID, Alert]], int]:
        async with self._lock:
            ids = list(reversed(self._ids))
            total = len(ids)
            page = ids[offset : offset + limit]
            return [(aid, self._alerts[aid]) for aid in page], total

    async def acknowledge(self, alert_id: UUID, by: str) -> Alert:
        async with self._lock:
            existing = self._alerts.get(alert_id)
            if existing is None:
                raise KeyError(alert_id)
            updated = existing.model_copy(
                update={
                    "acknowledged_at": datetime.now(UTC),
                    "acknowledged_by": by,
                }
            )
            self._alerts[alert_id] = updated
            return updated

    def clear(self) -> None:
        self._alerts.clear()
        self._ids.clear()


class PGAlertStore:
    """R8-8: PG-backed alert reader for the API surface.

    Reads from the ``alerts`` table populated by
    ``prediction_registry.alerts.AlertRouter`` (scheduler-side). The
    API doesn't write alerts (those come FROM the scheduler), so this
    store only implements ``list`` + ``acknowledge``. Calling ``add``
    raises ``NotImplementedError``.

    Pre-R8-8 the API used in-memory ``AlertStore`` exclusively, so
    real PG alerts (from the daily/event/high-freq schedulers) were
    invisible to ``GET /api/alerts/``.
    """

    def __init__(self, *, session_factory: Any) -> None:
        self._sf = session_factory

    async def add(self, alert: Alert) -> UUID:
        """API doesn't add alerts — they come from the scheduler. Raises."""
        raise NotImplementedError(
            "PGAlertStore is read-only on the API side. Alerts originate "
            "from prediction_registry.alerts.AlertRouter (scheduler). "
            "See R8-8."
        )

    async def list(self, *, limit: int, offset: int) -> tuple[list[tuple[UUID, Alert]], int]:
        from sqlalchemy import func as sa_func
        from sqlalchemy import select

        from ...common.enums import AlertLevel
        from ...data.models import AlertRow

        async with self._sf() as s:
            total = int(
                (await s.execute(select(sa_func.count()).select_from(AlertRow))).scalar_one() or 0
            )
            stmt = (
                select(AlertRow).order_by(AlertRow.detected_at.desc()).limit(limit).offset(offset)
            )
            rows = list((await s.execute(stmt)).scalars().all())

        items: list[tuple[UUID, Alert]] = []
        for r in rows:
            items.append(
                (
                    r.id,
                    Alert(
                        level=AlertLevel(r.level),
                        category=r.category,
                        message=r.message,
                        actionable_info=r.actionable_info,
                        related_ipo_id=r.related_ipo_id,
                        related_snapshot_id=r.related_snapshot_id,
                        detected_at=r.detected_at,
                        acknowledged_at=r.acknowledged_at,
                        acknowledged_by=r.acknowledged_by,
                    ),
                )
            )
        return items, total

    async def acknowledge(self, alert_id: UUID, by: str) -> Alert:
        from sqlalchemy import select

        from ...common.enums import AlertLevel
        from ...data.models import AlertRow

        async with self._sf() as s:
            row = (
                await s.execute(select(AlertRow).where(AlertRow.id == alert_id))
            ).scalar_one_or_none()
            if row is None:
                raise KeyError(alert_id)
            row.acknowledged_at = datetime.now(UTC)
            row.acknowledged_by = by
            await s.commit()
            await s.refresh(row)
        return Alert(
            level=AlertLevel(row.level),
            category=row.category,
            message=row.message,
            actionable_info=row.actionable_info,
            related_ipo_id=row.related_ipo_id,
            related_snapshot_id=row.related_snapshot_id,
            detected_at=row.detected_at,
            acknowledged_at=row.acknowledged_at,
            acknowledged_by=row.acknowledged_by,
        )


# R8-8: AlertStore | PGAlertStore — the API endpoint code is identical
# either way (both implement list/acknowledge via the same surface).
_default_store: list[Any] = []


def get_alert_store() -> Any:
    if not _default_store:
        _default_store.append(AlertStore())
    return _default_store[0]


def set_alert_store(store: Any) -> None:
    """R8-8: install a custom alert store (typically ``PGAlertStore``).

    The FastAPI lifespan should call this with a PG-backed store on
    production startup so ``GET /api/alerts/`` shows the same alerts
    the scheduler emitted. Tests keep using the default in-memory.
    """
    _default_store.clear()
    _default_store.append(store)


def reset_alert_store_for_test() -> None:
    _default_store.clear()


@router.get("/", response_model=PaginatedResponse)
async def list_alerts(
    user: Annotated[CurrentUser, Depends(require_permission(Permission.READ_ALERT))],
    limit: int = 50,
    offset: int = 0,
) -> PaginatedResponse:
    """R6-1: gated behind READ_ALERT."""
    _ = user
    items, total = await get_alert_store().list(limit=limit, offset=offset)
    return PaginatedResponse(
        data=[{"id": str(aid), **alert.model_dump(mode="json")} for aid, alert in items],
        meta=PaginationMeta(
            total=total, limit=limit, offset=offset, has_next=offset + limit < total
        ),
    )


@router.post("/{alert_id}/acknowledge")
async def acknowledge_alert(
    alert_id: UUID,
    payload: AlertAck,
    user: Annotated[CurrentUser, Depends(require_permission(Permission.ACK_ALERT))],
) -> dict[str, Any]:
    """Mark an alert as acknowledged."""
    _ = payload
    try:
        updated = await get_alert_store().acknowledge(alert_id, by=user.email)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"alert {alert_id} not found",
        ) from exc
    return {
        "alert_id": str(alert_id),
        "acknowledged_at": updated.acknowledged_at.isoformat() if updated.acknowledged_at else None,
        "acknowledged_by": updated.acknowledged_by,
    }


__all__ = (
    "AlertStore",
    "PGAlertStore",
    "get_alert_store",
    "reset_alert_store_for_test",
    "router",
    "set_alert_store",
)


_ = AlertLevel  # re-exported as side dependency
