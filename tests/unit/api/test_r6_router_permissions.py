"""R6-1 — 5 read-side routers enforce require_permission.

Pre-R6-1 dashboard / ipos / snapshots / alerts / prospectus accepted any
authenticated request — `user: CurrentUserDep` only proves "valid bearer
token". A VIEWER token from a less privileged tenant could pull every IPO
+ snapshot in the system, breaking PROJECT_SPEC.md §6 / §16.5 RBAC.

Post-R6-1:
  * 4 new permissions added — READ_DASHBOARD, READ_IPO, READ_ALERT,
    READ_PROSPECTUS — alongside the existing READ_SNAPSHOTS.
  * All 5 read-permissions are part of `_BASE_READ` so every role at
    VIEWER and above inherits them (matches the spec's "all internal
    users can read everything; writes are restricted").
  * AUDITOR (read-only audit role) also inherits the 5 read perms.
  * The 9 read endpoints each gain ``Depends(require_permission(...))``.

These tests confirm:
1. The 4 new enum values + ROLE_PERMISSIONS membership.
2. Each endpoint 401s on no bearer / 403s on a role lacking the perm /
   200s on a role that has it.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from hk_ipo_agent.api.auth.jwt import issue_access_token
from hk_ipo_agent.common.enums import (
    ROLE_PERMISSIONS,
    Permission,
    UserRole,
)


def _token_for(roles: list[UserRole]) -> str:
    """Issue a JWT with the given roles. No password required."""
    token, _ = issue_access_token(
        user_id=uuid4(),
        email=f"r6-test-{roles[0].value}@hk.local",
        roles=[r.value for r in roles],
    )
    return token


def _headers(roles: list[UserRole]) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token_for(roles)}"}


# ------------------------------------------------------------------ enum surface


def test_r6_new_permissions_exist() -> None:
    """R6-1 — 4 new READ_* permissions are part of the Permission enum."""
    for name in ("READ_DASHBOARD", "READ_IPO", "READ_ALERT", "READ_PROSPECTUS"):
        assert hasattr(Permission, name), f"Permission.{name} missing"


def test_r6_viewer_inherits_all_new_read_permissions() -> None:
    """R6-1 — VIEWER (lowest role) has all 5 read perms (the spec floor)."""
    viewer_perms = ROLE_PERMISSIONS[UserRole.VIEWER]
    for p in (
        Permission.READ_DASHBOARD,
        Permission.READ_IPO,
        Permission.READ_ALERT,
        Permission.READ_PROSPECTUS,
        Permission.READ_SNAPSHOTS,  # pre-existing
    ):
        assert p in viewer_perms, f"VIEWER should inherit {p.value}"


def test_r6_auditor_also_inherits_new_read_permissions() -> None:
    """R6-1 — AUDITOR (audit-focused role) also reads the 5 surfaces.

    Otherwise an auditor can read audit logs but can't see the snapshots /
    IPOs the audit log references, which would make audit investigations
    impossible.
    """
    auditor_perms = ROLE_PERMISSIONS[UserRole.AUDITOR]
    for p in (
        Permission.READ_DASHBOARD,
        Permission.READ_IPO,
        Permission.READ_ALERT,
        Permission.READ_PROSPECTUS,
        Permission.READ_SNAPSHOTS,
    ):
        assert p in auditor_perms, f"AUDITOR should inherit {p.value}"


# ------------------------------------------------------------------ endpoint enforcement


# Each tuple: (path, method, required_perm, bypass_with_role_for_200_check_or_None)
_GUARDED_ROUTES: list[tuple[str, str, Permission]] = [
    ("/api/dashboard/summary", "GET", Permission.READ_DASHBOARD),
    ("/api/ipos/", "GET", Permission.READ_IPO),
    ("/api/snapshots/", "GET", Permission.READ_SNAPSHOTS),
    ("/api/alerts/", "GET", Permission.READ_ALERT),
]


@pytest.mark.parametrize("path,method,perm", _GUARDED_ROUTES)
def test_endpoint_401_without_token(
    client: TestClient, path: str, method: str, perm: Permission
) -> None:
    """R6-1 — no Authorization header → 401."""
    resp = client.request(method, path)
    assert resp.status_code == 401, (
        f"{method} {path} expected 401 without token, got {resp.status_code}; "
        f"body={resp.text[:200]}"
    )


class _FakeRole:
    """A user role with zero permissions — used to prove 403 on missing perm.

    We can't construct a real UserRole that's missing one of the BASE_READ
    perms (every defined role has them). To prove the *gate is wired*, we
    issue a JWT with an unrecognised role name — `roles_from_strings` drops
    it, leaving the token with an empty role list and therefore zero perms.
    """


def _token_with_no_recognised_roles() -> str:
    """Issue a JWT whose ``roles`` claim contains only unrecognised strings."""
    token, _ = issue_access_token(
        user_id=uuid4(),
        email="r6-no-roles@hk.local",
        roles=["__nonexistent_role__"],
    )
    return token


@pytest.mark.parametrize("path,method,perm", _GUARDED_ROUTES)
def test_endpoint_403_when_user_has_no_role(
    client: TestClient, path: str, method: str, perm: Permission
) -> None:
    """R6-1 — valid bearer but user has 0 roles → 403 with `requires permission`."""
    token = _token_with_no_recognised_roles()
    resp = client.request(method, path, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403, (
        f"{method} {path} expected 403 on permission gate, got {resp.status_code}; "
        f"body={resp.text[:200]}"
    )
    assert "permission" in resp.text.lower()


@pytest.mark.parametrize("path,method,perm", _GUARDED_ROUTES)
def test_endpoint_200_with_viewer_role(
    client: TestClient, path: str, method: str, perm: Permission
) -> None:
    """R6-1 — VIEWER role has the perm → endpoint succeeds (2xx)."""
    resp = client.request(method, path, headers=_headers([UserRole.VIEWER]))
    # 2xx (200 with empty list is fine — we're checking the gate, not the payload).
    assert 200 <= resp.status_code < 300, (
        f"{method} {path} expected 2xx with VIEWER, got {resp.status_code}; body={resp.text[:200]}"
    )


# Prospectus endpoint has a different return shape (404 if file missing).
# We assert it 401s without token + 403s without perm; 404 with VIEWER (file
# isn't present) is "the gate let us through" so we accept it as 2xx-ish-ok.


def test_prospectus_401_without_token(client: TestClient) -> None:
    """R6-1 — prospectus PDF needs auth too."""
    resp = client.get("/api/prospectus/TEST.pdf")
    assert resp.status_code == 401


def test_prospectus_403_without_perm(client: TestClient) -> None:
    """R6-1 — prospectus PDF needs READ_PROSPECTUS too."""
    token = _token_with_no_recognised_roles()
    resp = client.get(
        "/api/prospectus/TEST.pdf",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "permission" in resp.text.lower()


def test_prospectus_404_with_viewer_role(client: TestClient) -> None:
    """R6-1 — with READ_PROSPECTUS the gate is open; the 404 is from
    missing PDF on disk (which is correct behaviour for unknown id)."""
    resp = client.get("/api/prospectus/UNKNOWN.pdf", headers=_headers([UserRole.VIEWER]))
    assert resp.status_code == 404
    # We confirm the gate is the file-not-found 404, not a permission 403.
    assert "permission" not in resp.text.lower()
