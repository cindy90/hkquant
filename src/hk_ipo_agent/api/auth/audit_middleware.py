"""Audit log middleware per PROJECT_SPEC.md §16 + CLAUDE.md v1.2.1 constraints.

Auto-writes an ``AuditLog`` entry for every write request (POST / PUT /
DELETE / PATCH). Read requests (GET / HEAD / OPTIONS) are skipped to keep
the log compact.

Phase 7 MVP shipped an in-memory store; Phase 7.5b (this commit) adds
``PGAuditStore`` for production with ``audit_logs`` DB trigger giving
defense-in-depth immutability. ``get_audit_store()`` still defaults to
in-memory so unit tests Just Work; the FastAPI lifespan calls
``set_audit_store(PGAuditStore())`` on startup.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4

from fastapi import Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from ...common.enums import AuditResourceType
from ...common.schemas import AuditLog
from ...data.database import async_session_factory
from ...data.models import AuditLogRow

_WRITE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}


# R6-8: prefix → AuditResourceType lookup for resource_type inference. The
# matched second URL segment becomes the resource_id (uuid / alphanumeric).
_RESOURCE_PREFIXES: tuple[tuple[str, AuditResourceType], ...] = (
    ("/api/snapshots", AuditResourceType.SNAPSHOT),
    ("/api/reviews", AuditResourceType.REVIEW),
    ("/api/proposals", AuditResourceType.PROPOSAL),
    ("/api/alerts", AuditResourceType.ALERT),
    ("/api/settings", AuditResourceType.CONFIG),
    ("/api/chat", AuditResourceType.CHAT_SESSION),
    ("/api/auth", AuditResourceType.USER),
)


def _infer_resource_from_path(
    path: str,
) -> tuple[AuditResourceType | None, str | None]:
    """R6-8 — derive (resource_type, resource_id) from request.url.path.

    Returns ``(None, None)`` when the path doesn't match any registered
    prefix or when it has no second segment beyond the prefix. The match
    requires a segment boundary (i.e. the prefix must equal the path or
    be followed by ``/``) so ``/api/snapshots-fake/x`` doesn't accidentally
    bind to SNAPSHOT — same R6-5-style guard.
    """
    if not path or not path.startswith("/api/"):
        return (None, None)
    for prefix, rtype in _RESOURCE_PREFIXES:
        if path == prefix or path == prefix + "/" or path.startswith(prefix + "/"):
            remainder = path[len(prefix) :].lstrip("/")
            if not remainder:
                return (rtype, None)
            # Second segment is the resource id; trailing sub-paths (e.g.
            # /memo.md, /accept) are stripped because they describe an
            # action against the resource, not part of its identity.
            resource_id = remainder.split("/", 1)[0]
            return (rtype, resource_id or None)
    return (None, None)


# ---------------------------------------------------------------------------
# Store Protocol — InMemory + PG share this contract
# ---------------------------------------------------------------------------


@runtime_checkable
class AuditStoreProtocol(Protocol):
    """Public API both backends honour. Used by middleware + audit router."""

    async def append(self, record: AuditLog) -> None: ...

    async def query(
        self,
        *,
        user_id: UUID | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        since: datetime | None = None,
        limit: int = 50,
    ) -> list[AuditLog]: ...


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
        resource_id: str | None = None,
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
                if resource_id and r.resource_id != resource_id:
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


class PGAuditStore:
    """PostgreSQL-backed audit log honouring DB-trigger immutability.

    ``audit_logs`` has ``audit_no_update`` + ``audit_no_delete`` triggers
    that share the snapshot-trigger function (Phase 7.5a migration).
    Application code only ever INSERTs.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._sf = session_factory or async_session_factory()

    async def append(self, record: AuditLog) -> None:
        row = AuditLogRow(
            id=record.id,
            user_id=record.user_id,
            user_email=record.user_email,
            action=record.action,
            resource_type=record.resource_type.value if record.resource_type else None,
            resource_id=record.resource_id,
            before_state=record.before_state,
            after_state=record.after_state,
            diff=record.diff,
            ip_address=record.ip_address,
            user_agent=record.user_agent,
            request_id=record.request_id,
            api_endpoint=record.api_endpoint,
            success=record.success,
            error_message=record.error_message,
            occurred_at=record.occurred_at,
        )
        async with self._sf() as s:
            s.add(row)
            await s.commit()

    async def query(
        self,
        *,
        user_id: UUID | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        since: datetime | None = None,
        limit: int = 50,
    ) -> list[AuditLog]:
        stmt = select(AuditLogRow).order_by(AuditLogRow.occurred_at.desc()).limit(limit)
        if user_id is not None:
            stmt = stmt.where(AuditLogRow.user_id == user_id)
        if resource_type is not None:
            stmt = stmt.where(AuditLogRow.resource_type == resource_type)
        if resource_id is not None:
            stmt = stmt.where(AuditLogRow.resource_id == resource_id)
        if since is not None:
            stmt = stmt.where(AuditLogRow.occurred_at >= since)
        async with self._sf() as s:
            rows = (await s.execute(stmt)).scalars().all()
        return [
            AuditLog(
                id=r.id,
                user_id=r.user_id,
                user_email=r.user_email,
                action=r.action,
                resource_type=None,  # caller-side enum decode if needed
                resource_id=r.resource_id,
                before_state=r.before_state,
                after_state=r.after_state,
                diff=r.diff,
                ip_address=r.ip_address,
                user_agent=r.user_agent,
                request_id=r.request_id,
                api_endpoint=r.api_endpoint,
                success=r.success,
                error_message=r.error_message,
                occurred_at=r.occurred_at,
            )
            for r in rows
        ]


_default_store: list[AuditStoreProtocol] = []


def get_audit_store() -> AuditStoreProtocol:
    """Process-wide singleton. Default in-memory; lifespan swaps to PG."""
    if not _default_store:
        _default_store.append(AuditStore())
    return _default_store[0]


def set_audit_store(store: AuditStoreProtocol) -> None:
    """Replace the process-wide store — called from FastAPI lifespan."""
    _default_store.clear()
    _default_store.append(store)


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

        # R6-8: infer resource_type + resource_id from the path so audit
        # query filtering by resource_type works (pre-fix both were None,
        # making /api/audit?resource_type=alert always empty).
        resource_type, resource_id = _infer_resource_from_path(request.url.path)
        record = AuditLog(
            id=uuid4(),
            user_id=user_id,
            user_email=user_email,
            action=f"{request.method} {request.url.path}",
            resource_type=resource_type,
            resource_id=resource_id,
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
    "AuditStoreProtocol",
    "PGAuditStore",
    "_infer_resource_from_path",
    "get_audit_store",
    "reset_audit_store_for_test",
    "set_audit_store",
)
