"""RBAC role / permission helpers per PROJECT_SPEC.md §6 / §16.5."""

from __future__ import annotations

from ...common.enums import (
    ROLE_PERMISSIONS,
    Permission,
    UserRole,
)


def has_role(user_roles: list[UserRole], *required: UserRole) -> bool:
    """True iff the user has at least one of ``required`` roles."""
    return any(r in user_roles for r in required)


def permissions_for_roles(user_roles: list[UserRole]) -> set[Permission]:
    """Union of permissions across all of a user's roles."""
    perms: set[Permission] = set()
    for r in user_roles:
        perms |= ROLE_PERMISSIONS.get(r, frozenset())
    return perms


def has_permission(user_roles: list[UserRole], required: Permission) -> bool:
    """True iff at least one of the user's roles grants ``required``."""
    return required in permissions_for_roles(user_roles)


def roles_from_strings(role_names: list[str]) -> list[UserRole]:
    """Convert string role names (from JWT claim) → ``UserRole`` enum list."""
    roles: list[UserRole] = []
    for name in role_names:
        try:
            roles.append(UserRole(name))
        except ValueError:
            continue
    return roles


__all__ = (
    "has_permission",
    "has_role",
    "permissions_for_roles",
    "roles_from_strings",
)
