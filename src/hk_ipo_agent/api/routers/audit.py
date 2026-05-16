"""Audit log query endpoint per PROJECT_SPEC.md §16."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from ...common.enums import Permission
from ..auth.audit_middleware import get_audit_store
from ..auth.dependencies import CurrentUser, require_permission
from ..schemas import PaginatedResponse, PaginationMeta

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("/logs", response_model=PaginatedResponse)
async def list_audit_logs(
    user: Annotated[CurrentUser, Depends(require_permission(Permission.READ_AUDIT))],
    user_id: UUID | None = None,
    resource_type: str | None = None,
    since: datetime | None = None,
    limit: int = Query(50, ge=1, le=500),
) -> PaginatedResponse:
    _ = user
    records = await get_audit_store().query(
        user_id=user_id,
        resource_type=resource_type,
        since=since,
        limit=limit,
    )
    return PaginatedResponse(
        data=[r.model_dump(mode="json") for r in records],
        meta=PaginationMeta(
            total=len(records), limit=limit, offset=0, has_next=False
        ),
    )


__all__ = ("router",)
