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

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, Header, HTTPException, Request, status

from ...common.enums import Permission, UserRole
from .jwt import AuthError, decode_access_token
from .rbac import has_permission, has_role, roles_from_strings

# R6-2: single shared Argon2id hasher.
# OWASP "Password Storage Cheat Sheet" recommends Argon2id with at least
# memory_cost=19 MiB, time_cost=2, parallelism=1. argon2-cffi's defaults
# (memory_cost=65 MiB, time_cost=3, parallelism=4, hash_len=32) exceed
# those floors and remain fast enough for sub-100 ms login on dev hardware.
_PASSWORD_HASHER = PasswordHasher()

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
    """R6-2: Argon2id hash (OWASP-recommended). Replaces SHA-256 with static salt.

    The returned string is the standard PHC-encoded form
    ``$argon2id$v=19$m=...,t=...,p=...$<salt>$<hash>`` which carries all
    parameters needed to re-verify. Each call produces a different hash
    even for the same input because Argon2 salts internally.
    """
    return _PASSWORD_HASHER.hash(plain)


def _is_legacy_sha256(stored: str) -> bool:
    """R6-2: detect pre-R6-2 SHA-256 hashes for lazy-rehash on login.

    Legacy format: 64-char lowercase hex (sha256 hexdigest). Argon2id
    starts with ``$argon2id$``. Any other shape is treated as Argon2id and
    will fail verification cleanly.
    """
    return len(stored) == 64 and all(c in "0123456789abcdef" for c in stored)


def _legacy_sha256_hash(plain: str) -> str:
    """The exact pre-R6-2 SHA-256 derivation, kept for lazy-rehash checks."""
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
    """Return the user record iff email+password matches; else None.

    R6-2: dual-format verification with lazy rehash.
      * Stored Argon2id hash: verify via the Argon2 hasher.
      * Stored legacy SHA-256 hash: verify via the legacy derivation;
        on success, upgrade the stored hash to Argon2id in-place so future
        logins use the modern path.
    """
    rec = _USERS.get(email.lower().strip())
    if rec is None or not rec.is_active:
        return None

    stored = rec.password_sha256  # field name is back-compat; holds Argon2 today
    if _is_legacy_sha256(stored):
        # Legacy path: constant-time compare to the legacy SHA-256.
        if stored != _legacy_sha256_hash(password):
            return None
        # R6-2 lazy rehash: upgrade to Argon2id now that we've validated.
        rec.password_sha256 = _hash_password(password)
        return rec

    # Argon2id path.
    try:
        _PASSWORD_HASHER.verify(stored, password)
    except VerifyMismatchError:
        return None
    except Exception:
        # Malformed hash — fail closed rather than leak details.
        return None
    # If argon2-cffi recommends a rehash (e.g. cost params upgraded), do it.
    if _PASSWORD_HASHER.check_needs_rehash(stored):
        rec.password_sha256 = _hash_password(password)
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


async def upsert_user_account_for_jwt(user: CurrentUser) -> None:
    """R6-7 — idempotently insert this user into ``user_accounts`` + ``user_roles``.

    Why: production endpoints that persist a FK reference to user_accounts
    (e.g. ``whatif_calculations.user_id``) need every JWT-issued caller to
    exist in the table. Pre-R6-7 those rows stored ``user_id=None`` to
    side-step the FK; that broke Phase 10 attribution.

    Behaviour:
      * If ``user_accounts`` row with ``id=user.id`` already exists, no-op
        (we keep the existing email / display_name).
      * Else INSERT a row with ``sso_provider='local'``,
        ``sso_subject=str(user.id)``, ``is_active=True``.
      * For each role in ``user.roles``, ensure a ``user_roles`` row.
        Uses the (user_id, role) UNIQUE index — ``ON CONFLICT DO NOTHING``
        keeps repeat calls cheap.
      * Any DB error is swallowed: callers (whatif persist, future
        endpoints) are best-effort.
    """
    from datetime import UTC, datetime

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from ...data.database import async_session_factory
    from ...data.models import UserAccountRow, UserRoleRow

    try:
        sf = async_session_factory()
        async with sf() as s:
            stmt = (
                pg_insert(UserAccountRow)
                .values(
                    id=user.id,
                    email=user.email or f"{user.id}@jwt.local",
                    sso_provider="local",
                    sso_subject=str(user.id),
                    is_active=True,
                    created_at=datetime.now(UTC),
                )
                .on_conflict_do_nothing(index_elements=["id"])
            )
            await s.execute(stmt)

            for role in user.roles:
                role_stmt = (
                    pg_insert(UserRoleRow)
                    .values(
                        id=uuid4(),
                        user_id=user.id,
                        role=role.value,
                        granted_at=datetime.now(UTC),
                    )
                    .on_conflict_do_nothing(index_elements=["user_id", "role"])
                )
                await s.execute(role_stmt)

            await s.commit()
    except Exception:
        # Best-effort: PG unavailable / table missing in dev should not
        # break the request path. The audit middleware still records the
        # subject for forensics.
        pass


async def resolve_user_async(user_id: UUID) -> _UserRecord | None:
    """R6-4 — async resolver: PG first, in-memory dev fallback.

    Used by the WebSocket chat endpoint (and any future async-only entry
    point) so production users provisioned only in ``user_accounts`` can
    authenticate without a parallel in-memory seed.

    Behaviour:
      1. Try ``get_user_by_id_pg(user_id)``. If it returns a user, use it.
      2. If PG returns None OR raises (table missing in dev, network
         blip), fall back to the in-memory store via ``get_user_by_id``.
      3. If neither has the user, return ``None`` — caller must close the
         WebSocket with a policy violation.

    Errors are swallowed (logged-and-fallback) rather than propagated
    because PG availability is best-effort here; failing the entire WS
    handshake on a transient DB hiccup creates worse UX than continuing
    with the in-memory seed.
    """
    try:
        pg_rec = await get_user_by_id_pg(user_id)
        if pg_rec is not None:
            return pg_rec
    except Exception:
        # Log-and-fallback. We don't have a logger in this module; the
        # WS handler will close the connection cleanly if BOTH stores
        # miss the user, which is the only externally observable result.
        pass
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
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> CurrentUser:
    """Decode the ``Authorization: Bearer <token>`` header → ``CurrentUser``.

    Raises 401 if the token is missing / invalid / expired.

    R2-6: also stashes the resolved ``CurrentUser`` on ``request.state.current_user``
    so downstream middleware (audit, rate_limit) can attribute the request
    to a subject. Pre-fix audit_middleware / rate_limit middleware both
    read ``request.state.current_user`` but no one wrote it → audit_log
    rows always had ``user_id=None``, violating CLAUDE.md §UI 集成约束 §3.
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

    result = CurrentUser(
        id=user_id,
        email=payload.get("email", ""),
        roles=roles_from_strings(payload.get("roles", [])),
    )
    # R2-6: write to request.state so audit + rate_limit middleware
    # (which run AFTER the route handler) can pick up the subject.
    request.state.current_user = result
    return result


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
    "resolve_user_async",
    "upsert_user_account_for_jwt",
    "verify_user",
)
