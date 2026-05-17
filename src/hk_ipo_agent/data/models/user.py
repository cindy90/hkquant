"""User / role ORM models — v1.2.1 tables per PROJECT_SPEC.md §5 + §16.5.

Phase 7 MVP authenticated against an in-memory user list (local JWT,
no SSO per ADR 0011). Phase 7.5a moves authoritative user storage to
PostgreSQL; SSO providers themselves are still Phase 9.

- ``user_accounts`` — one row per identity; SSO subject UNIQUE per
  provider; ``is_active=False`` disables login but preserves audit
  history.
- ``user_roles`` — many-to-many role grants with optional expiration.
  RBAC checks use this table joined to ``user_accounts``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDMixin


class UserAccountRow(UUIDMixin, Base):
    """User identity — PROJECT_SPEC.md §5 ``user_accounts``.

    ``sso_provider='local'`` is the Phase 7 MVP fallback (local JWT).
    External SSO (okta / azure_ad / google) lands in Phase 9.
    """

    __tablename__ = "user_accounts"

    email: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    display_name: Mapped[str | None] = mapped_column(String(100))
    sso_provider: Mapped[str] = mapped_column(String(50), nullable=False)
    # enums.SSOProvider: okta / azure_ad / google / local
    sso_subject: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index(
            "uq_user_accounts_provider_subject",
            "sso_provider",
            "sso_subject",
            unique=True,
        ),
    )


class UserRoleRow(UUIDMixin, Base):
    """Role grant (many-to-many) — PROJECT_SPEC.md §5 ``user_roles``.

    UNIQUE on (user_id, role) prevents duplicate grants. ON DELETE
    CASCADE keeps the grant set tidy when an account is hard-deleted —
    though most account removal goes through ``is_active=False`` to
    preserve audit history.
    """

    __tablename__ = "user_roles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    # enums.UserRole: viewer / reviewer / senior_reviewer / operator / admin / auditor
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("user_accounts.id"),
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("uq_user_roles_user_role", "user_id", "role", unique=True),
        Index("idx_user_roles_user", "user_id"),
    )


__all__ = (
    "UserAccountRow",
    "UserRoleRow",
)
