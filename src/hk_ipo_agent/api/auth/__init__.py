"""Auth + RBAC subsystem (Phase 7 MVP per ADR 0011)."""

from __future__ import annotations

from .audit_middleware import (
    AuditMiddleware,
    AuditStore,
    get_audit_store,
    reset_audit_store_for_test,
)
from .dependencies import (
    CurrentUser,
    CurrentUserDep,
    create_user,
    get_current_user,
    get_user_by_id,
    require_permission,
    require_role,
    reset_users_for_test,
    verify_user,
)
from .jwt import (
    AuthError,
    decode_access_token,
    issue_access_token,
    token_lifetime_remaining,
)
from .rbac import (
    has_permission,
    has_role,
    permissions_for_roles,
    roles_from_strings,
)

__all__ = (
    "AuditMiddleware",
    "AuditStore",
    "AuthError",
    "CurrentUser",
    "CurrentUserDep",
    "create_user",
    "decode_access_token",
    "get_audit_store",
    "get_current_user",
    "get_user_by_id",
    "has_permission",
    "has_role",
    "issue_access_token",
    "permissions_for_roles",
    "require_permission",
    "require_role",
    "reset_audit_store_for_test",
    "reset_users_for_test",
    "roles_from_strings",
    "token_lifetime_remaining",
    "verify_user",
)
