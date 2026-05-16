"""Audit log middleware per PROJECT_SPEC.md §16 + CLAUDE.md v1.2.1 constraints.

Auto-writes an ``AuditLog`` entry for every write request (POST / PUT /
DELETE / PATCH). Read requests (GET / HEAD / OPTIONS) are skipped to keep
the log compact.

Phase 7 MVP: stores audit records in an in-memory list. Phase 7.5
replaces with the ``audit_log`` PostgreSQL table.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from ...common.schemas import AuditLog

_WRITE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}


# ---------------------------------------------------------------------------
# In-memory store (Phase 7 MVP)
# ---------------------------------------------------------------------------


class AuditStore:
    """Process-wide append-only audit log. Phase 7.5 swaps to PG."""

    def __init__(self) -> None:
        self._records: list[AuditLog] = []
        self._lock = asyncio.Lock()

    async def append(self, record: AuditLog) -> None:
        async with self._lock:
            self._records.append(record)

    async def query(
        self,
        *,
        user_id: UUID | None = None,
        resource_type: str | None = None,
        since: datetime | None = None,
        limit: int = 50,
    ) -> list[AuditLog]:
        async with self._lock:
            results: list[AuditLog] = []
            for r in reversed(self._records):
                if user_id and r.user_id != user_id:
                    continue
                if resource_type:
                    rt = r.resource_type
                    if rt is None or rt.value != resource_type:
                        continue
                if since and r.occurred_at < since:
                    continue
                results.append(r)
                if len(results) >= limit:
                    break
            return results

    def __len__(self) -> int:
        return len(self._records)

    def clear(self) -> None:
        """Testing only."""
        self._records.clear()


_default_store: list[AuditStore] = []


def get_audit_store() -> AuditStore:
    """Process-wide singleton AuditStore."""
    if not _default_store:
        _default_store.append(AuditStore())
    return _default_store[0]


def reset_audit_store_for_test() -> None:
    _default_store.clear()


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class AuditMiddleware(BaseHTTPMiddleware):
    """Logs every write request to the audit store."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        if request.method not in _WRITE_METHODS:
            return response

        # Best-effort: pull current user from request.state if dependency was run.
        user_id: UUID | None = None
        user_email: str | None = None
        current = getattr(request.state, "current_user", None)
        if current is not None:
            user_id = getattr(current, "id", None)
            user_email = getattr(current, "email", None)

        record = AuditLog(
            id=uuid4(),
            user_id=user_id,
            user_email=user_email,
            action=f"{request.method} {request.url.path}",
            resource_type=None,
            resource_id=None,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            request_id=response.headers.get("X-Request-Id"),
            api_endpoint=request.url.path,
            success=200 <= response.status_code < 400,
            error_message=None if response.status_code < 400 else f"HTTP {response.status_code}",
            occurred_at=datetime.now(UTC),
        )
        await get_audit_store().append(record)
        return response


__all__ = (
    "AuditMiddleware",
    "AuditStore",
    "get_audit_store",
    "reset_audit_store_for_test",
)
