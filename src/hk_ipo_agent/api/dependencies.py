"""Top-level FastAPI Depends — re-exports from ``auth.dependencies`` for convenience."""

from __future__ import annotations

from .auth.dependencies import (
    CurrentUser,
    CurrentUserDep,
    get_current_user,
    require_permission,
    require_role,
)

__all__ = (
    "CurrentUser",
    "CurrentUserDep",
    "get_current_user",
    "require_permission",
    "require_role",
)
