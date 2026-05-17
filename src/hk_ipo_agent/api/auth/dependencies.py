"""FastAPI Depends — ``require_role`` / ``require_permission`` + in-memory user store.

Phase 7 MVP per ADR 0011: stores users in process memory. Phase 7.5
replaces with PostgreSQL ``users`` + ``user_roles`` tables.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends, Header, HTTPException, status

from ...common.enums import Permission, UserRole
from .jwt import AuthError, decode_access_token
from .rbac import has_permission, has_role, roles_from_strings

# ---------------------------------------------------------------------------
# In-memory user store (Phase 7 MVP)
# ---------------------------------------------------------------------------


@dataclass
class _UserRecord:
    """Internal user record. Phase 7.5 replaces with ORM."""

    id: UUID
    email: str
    password_sha256: str
    display_name: str | None = None
    roles: list[UserRole] = field(default_factory=list)
    is_active: bool = True
    created_at: datetime = field(default_factory=datetime.utcnow)


_USERS: dict[str, _UserRecord] = {}


def _hash_password(plain: str) -> str:
    """SHA-256 of plaintext + a static salt. Phase 7.5 uses Argon2."""
    return hashlib.sha256(f"hkipo::{plain}".encode()).hexdigest()


def create_user(
    *,
    email: str,
    password: str,
    roles: list[UserRole],
    display_name: str | None = None,
) -> _UserRecord:
    """Create or replace a local user. Phase 7 MVP convenience."""
    rec = _UserRecord(
        id=uuid4(),
        email=email.lower().strip(),
        password_sha256=_hash_password(password),
        display_name=display_name,
        roles=list(roles),
    )
    _USERS[rec.email] = rec
    return rec


def verify_user(email: str, password: str) -> _UserRecord | None:
    """Return the user record iff email+password matches; else None."""
    rec = _USERS.get(email.lower().strip())
    if rec is None or not rec.is_active:
        return None
    if rec.password_sha256 != _hash_password(password):
        return None
    return rec


def get_user_by_id(user_id: UUID) -> _UserRecord | None:
    for rec in _USERS.values():
        if rec.id == user_id:
            return rec
    return None


def reset_users_for_test() -> None:
    """Wipe user store — testing only."""
    _USERS.clear()


# ---------------------------------------------------------------------------
# PG-backed user lookup (7.5b-3) — opt-in fallback used when the in-memory
# store doesn't have a match. Lets production lifespan provision users
# via the user_accounts + user_roles tables without breaking Phase 7's
# in-memory tests.
# ---------------------------------------------------------------------------


async def get_user_by_id_pg(user_id: UUID) -> _UserRecord | None:
    """Look up a user from the ``user_accounts`` + ``user_roles`` tables.

    Returns ``None`` if the user is absent or inactive. Caller is
    responsible for falling back to the in-memory store when None.
    """
    from sqlalchemy import select

    from ...data.database import async_session_factory
    from ...data.models import UserAccountRow, UserRoleRow

    sf = async_session_factory()
    async with sf() as s:
        user = await s.get(UserAccountRow, user_id)
        if user is None or not user.is_active:
            return None
        role_rows = (
            (await s.execute(select(UserRoleRow).where(UserRoleRow.user_id == user_id)))
            .scalars()
            .all()
        )
    roles = [UserRole(r.role) for r in role_rows if r.role in {ur.value for ur in UserRole}]
    return _UserRecord(
        id=user.id,
        email=user.email,
        password_sha256="",  # passwords only kept in-memory for Phase 7 MVP
        display_name=user.display_name,
        roles=roles,
        is_active=user.is_active,
        created_at=user.created_at,
    )


def resolve_user(user_id: UUID) -> _UserRecord | None:
    """Synchronous: prefer in-memory; PG lookup is async — callers needing
    it should ``await get_user_by_id_pg`` directly."""
    return get_user_by_id(user_id)


# Seed default users on import so dev / test environments have working
# credentials without a setup step. Production should disable this via env.
def _seed_defaults() -> None:
    if not _USERS:
        create_user(email="viewer@hk.local", password="viewer", roles=[UserRole.VIEWER])
        create_user(email="reviewer@hk.local", password="reviewer", roles=[UserRole.REVIEWER])
        create_user(email="admin@hk.local", password="admin", roles=[UserRole.ADMIN])


_seed_defaults()


# ---------------------------------------------------------------------------
# FastAPI dependency: current user (decodes Bearer token)
# ---------------------------------------------------------------------------


@dataclass
class CurrentUser:
    """Lightweight current-user struct passed to route handlers."""

    id: UUID
    email: str
    roles: list[UserRole]


def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
) -> CurrentUser:
    """Decode the ``Authorization: Bearer <token>`` header → ``CurrentUser``.

    Raises 401 if the token is missing / invalid / expired.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or malformed Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(None, 1)[1].strip()
    try:
        payload = decode_access_token(token)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    try:
        user_id = UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="malformed token claims",
        ) from exc

    return CurrentUser(
        id=user_id,
        email=payload.get("email", ""),
        roles=roles_from_strings(payload.get("roles", [])),
    )


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


# ---------------------------------------------------------------------------
# require_role / require_permission factories
# ---------------------------------------------------------------------------


def require_role(*roles: UserRole) -> Callable[[CurrentUser], CurrentUser]:
    """Return a FastAPI dependency that 403s if user lacks any of ``roles``."""

    def _checker(user: CurrentUserDep) -> CurrentUser:
        if not has_role(user.roles, *roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"requires one of roles: {[r.value for r in roles]}",
            )
        return user

    return _checker


def require_permission(perm: Permission) -> Callable[[CurrentUser], CurrentUser]:
    """Return a FastAPI dependency that 403s if user lacks ``perm``."""

    def _checker(user: CurrentUserDep) -> CurrentUser:
        if not has_permission(user.roles, perm):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"requires permission: {perm.value}",
            )
        return user

    return _checker


__all__ = (
    "CurrentUser",
    "CurrentUserDep",
    "create_user",
    "get_current_user",
    "get_user_by_id",
    "get_user_by_id_pg",
    "require_permission",
    "require_role",
    "reset_users_for_test",
    "resolve_user",
    "verify_user",
)
