"""Audit log query endpoint per PROJECT_SPEC.md §16."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from ...common.enums import Permission
from ...common.schemas import AuditLog
from ..auth.audit_middleware import get_audit_store
from ..auth.dependencies import CurrentUser, require_permission
from ..auth.rbac import has_permission
from ..schemas import PaginatedResponse, PaginationMeta

router = APIRouter(prefix="/api/audit", tags=["audit"])


# R6-3: fields that may carry PII / raw row bodies. Callers without
# READ_AUDIT_FULL see them as None; callers with FULL see them verbatim.
_SENSITIVE_FIELDS: tuple[str, ...] = (
    "before_state",
    "after_state",
    "diff",
    "ip_address",
    "user_agent",
    "error_message",
)


def _redact_for(record: AuditLog, *, full_access: bool) -> AuditLog:
    """R6-3 — null sensitive fields on ``record`` unless caller has full access.

    Field SHAPE is preserved (keys remain present in JSON output) so UI
    code that introspects the schema doesn't have to special-case
    redacted rows. Only the VALUES become None.
    """
    if full_access:
        return record
    return record.model_copy(update=dict.fromkeys(_SENSITIVE_FIELDS))


@router.get("/logs", response_model=PaginatedResponse)
async def list_audit_logs(
    user: Annotated[CurrentUser, Depends(require_permission(Permission.READ_AUDIT))],
    user_id: UUID | None = None,
    resource_type: str | None = None,
    since: datetime | None = None,
    limit: int = Query(50, ge=1, le=500),
) -> PaginatedResponse:
    """R6-3: any READ_AUDIT user can list, but sensitive payload fields are
    nulled out for callers that lack READ_AUDIT_FULL.
    """
    full_access = has_permission(user.roles, Permission.READ_AUDIT_FULL)
    records = await get_audit_store().query(
        user_id=user_id,
        resource_type=resource_type,
        since=since,
        limit=limit,
    )
    redacted = [_redact_for(r, full_access=full_access) for r in records]
    return PaginatedResponse(
        data=[r.model_dump(mode="json") for r in redacted],
        meta=PaginationMeta(total=len(redacted), limit=limit, offset=0, has_next=False),
    )


__all__ = ("_redact_for", "router")
