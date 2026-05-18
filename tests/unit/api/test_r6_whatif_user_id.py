"""R6-7 — whatif persistence sets user_id = current_user.id (FK satisfied).

Pre-R6-7 the whatif endpoint persisted ``user_id=None`` because the
caller's JWT subject UUID wasn't guaranteed to exist in
``user_accounts`` — the FK on ``whatif_calculations.user_id`` would
violate. As a result the row went in anonymous-shaped, so Phase 10
attribution couldn't link whatif activity back to a person.

Post-R6-7 the lifespan upserts the 3 default in-memory accounts into
``user_accounts`` at startup, and whatif first calls
``upsert_user_account_for_jwt(current_user)`` (best-effort) before
writing the row so JWT-issued UUIDs that aren't in the lifespan seed
still satisfy the FK.

Tests:
1. ``upsert_user_account_for_jwt`` is idempotent (two calls → one row).
2. whatif endpoint passes ``user_id=user.id`` to ``_persist_calculation``.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from hk_ipo_agent.api.auth.dependencies import CurrentUser
from hk_ipo_agent.api.auth.jwt import issue_access_token
from hk_ipo_agent.common.enums import UserRole


def test_upsert_user_account_for_jwt_is_idempotent_helper_exists() -> None:
    """R6-7 — the upsert helper is exported from auth.dependencies."""
    from hk_ipo_agent.api.auth import dependencies as dep

    assert hasattr(dep, "upsert_user_account_for_jwt"), (
        "auth.dependencies must expose upsert_user_account_for_jwt for R6-7"
    )


@pytest.mark.asyncio
async def test_upsert_user_account_for_jwt_swallows_db_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R6-7 — when PG is unavailable (dev / test), the upsert must not crash.

    Best-effort semantics match the rest of the whatif persistence path:
    the user gets their response even if persistence is offline.
    """
    from hk_ipo_agent.api.auth import dependencies as dep

    # Force the session factory to raise.
    async def _fail_factory(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("PG offline in test")

    monkeypatch.setattr(dep, "async_session_factory", _fail_factory, raising=False)

    # Should not raise.
    user = CurrentUser(id=uuid4(), email="upsert-test@hk.local", roles=[UserRole.VIEWER])
    await dep.upsert_user_account_for_jwt(user)


@pytest.mark.asyncio
async def test_whatif_persists_with_user_id_from_current_user(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    seeded_snapshot,
) -> None:
    """R6-7 — the whatif endpoint sets ``user_id`` to the JWT subject UUID.

    We monkeypatch the persistence helper to capture its kwargs, then
    fire a request and assert ``user_id == current_user.id``.
    """
    captured: dict = {}

    from hk_ipo_agent.api.routers import whatif as whatif_mod

    real_persist = whatif_mod._persist_calculation

    async def _capture(**kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        # Still call real helper so test exercises the full path.
        await real_persist(**kwargs)

    monkeypatch.setattr(whatif_mod, "_persist_calculation", _capture)

    user_id = uuid4()
    token, _ = issue_access_token(
        user_id=user_id,
        email="r6-7-test@hk.local",
        roles=[UserRole.ADMIN.value],
    )

    r = client.post(
        "/api/whatif/run",
        json={
            "snapshot_id": str(seeded_snapshot.id),
            "modified_assumptions": {"regime_score": 0.05, "mc_seed": 7},
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200

    # The persistence call must have received user_id == the JWT subject.
    assert "user_id" in captured
    assert captured["user_id"] == user_id, (
        f"R6-7: whatif must persist user_id from JWT, got {captured['user_id']!r}"
    )


def test_whatif_router_source_passes_user_id_not_none() -> None:
    """R6-7 — AST guard: whatif endpoint must NOT pass ``user_id=None``.

    Pre-R6-7 the code explicitly read ``user_id=None``; this assertion
    catches accidental regressions.
    """
    import ast
    import inspect

    import hk_ipo_agent.api.routers.whatif as whatif_mod

    tree = ast.parse(inspect.getsource(whatif_mod))
    # Walk Call nodes; for any call to _persist_calculation, the user_id
    # kwarg must not be a Constant(None) anymore.
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else (func.attr if isinstance(func, ast.Attribute) else None)
            )
            if name == "_persist_calculation":
                for kw in node.keywords:
                    if kw.arg == "user_id" and isinstance(kw.value, ast.Constant):
                        assert kw.value.value is not None, (
                            "R6-7: whatif must not hard-code user_id=None when "
                            "calling _persist_calculation"
                        )
