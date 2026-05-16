"""Audit-log ORM model — v1.2.1 table per PROJECT_SPEC.md §5 + §16.

The ``audit_logs`` table is **immutable** at the DB layer: UPDATE and
DELETE are blocked by triggers (``audit_no_update`` / ``audit_no_delete``)
sharing the ``prevent_snapshot_modification`` trigger function from
the v1.1 migration. Phase 7 MVP wrote to an in-memory ``AuditStore``;
Phase 7.5a swaps to this table via ``audit_middleware.py`` rewrite.

Append-only semantics + RFC 7807 audit trail = forensic-grade.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDMixin


class AuditLogRow(UUIDMixin, Base):
    """Immutable audit log — PROJECT_SPEC.md §5 ``audit_logs``.

    DB triggers in the v1.2.1 Alembic migration reject UPDATE / DELETE
    by reusing ``prevent_snapshot_modification()``.
    """

    __tablename__ = "audit_logs"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("user_accounts.id"),
    )
    user_email: Mapped[str | None] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    # e.g. 'review.submitted' / 'proposal.accepted' / 'config.modified'
    resource_type: Mapped[str | None] = mapped_column(String(50))
    # enums.AuditResourceType: snapshot / review / proposal / config / ...
    resource_id: Mapped[str | None] = mapped_column(String(200))
    before_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    diff: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(100))
    api_endpoint: Mapped[str | None] = mapped_column(String(200))
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        Index("idx_audit_user_time", "user_id", "occurred_at"),
        Index("idx_audit_resource", "resource_type", "resource_id"),
        Index("idx_audit_action", "action", "occurred_at"),
    )


__all__ = ("AuditLogRow",)
