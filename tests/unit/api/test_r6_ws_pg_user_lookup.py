"""R6-4 — WebSocket chat endpoint resolves users via PG-first, in-memory dev fallback.

Pre-R6-4 ``chat_endpoint.py`` called the synchronous in-memory
``get_user_by_id`` only. That meant a production user provisioned via
``user_accounts`` (Phase 7.5b-3 PG path) couldn't open a chat WebSocket
because their record only exists in the database, not in the in-memory
seed.

Post-R6-4 the endpoint uses a new ``resolve_user_async(user_id)`` helper
that tries PG first, then in-memory. Both paths return the same
``_UserRecord`` shape so downstream code is unchanged. If PG raises
(missing tables in dev, network blip) we silently fall back rather than
killing the WS connection.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from hk_ipo_agent.api.auth import dependencies as dep
from hk_ipo_agent.common.enums import UserRole


@pytest.mark.asyncio
async def test_resolve_user_async_falls_back_to_memory_when_pg_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R6-4 — PG returns None → in-memory user is returned.

    Simulates a dev environment where ``user_accounts`` is empty but the
    in-memory seed still has the default viewer/reviewer/admin.
    """
    dep.reset_users_for_test()
    rec = dep.create_user(email="ws-fallback@hk.local", password="x", roles=[UserRole.VIEWER])

    async def _pg_returns_none(_uid):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(dep, "get_user_by_id_pg", _pg_returns_none)

    result = await dep.resolve_user_async(rec.id)
    assert result is not None
    assert result.id == rec.id
    assert result.email == "ws-fallback@hk.local"


@pytest.mark.asyncio
async def test_resolve_user_async_falls_back_when_pg_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R6-4 — PG raises (e.g. table missing in dev) → in-memory used; no crash."""
    dep.reset_users_for_test()
    rec = dep.create_user(email="ws-pg-error@hk.local", password="x", roles=[UserRole.VIEWER])

    async def _pg_raises(_uid):  # type: ignore[no-untyped-def]
        raise RuntimeError("PG offline in dev")

    monkeypatch.setattr(dep, "get_user_by_id_pg", _pg_raises)

    result = await dep.resolve_user_async(rec.id)
    assert result is not None
    assert result.id == rec.id


@pytest.mark.asyncio
async def test_resolve_user_async_prefers_pg_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R6-4 — PG result wins when present (production path).

    We create the same user_id in both stores but with different emails;
    PG's wins.
    """
    dep.reset_users_for_test()
    uid = uuid4()
    # In-memory seed has email A.
    mem_rec = dep._UserRecord(  # type: ignore[attr-defined]
        id=uid,
        email="from-memory@hk.local",
        password_sha256="",
        roles=[UserRole.VIEWER],
    )
    dep._USERS["from-memory@hk.local"] = mem_rec  # type: ignore[attr-defined]

    # PG returns email B for the same user_id.
    async def _pg_returns_b(_uid):  # type: ignore[no-untyped-def]
        return dep._UserRecord(  # type: ignore[attr-defined]
            id=uid,
            email="from-pg@hk.local",
            password_sha256="",
            roles=[UserRole.ADMIN],
        )

    monkeypatch.setattr(dep, "get_user_by_id_pg", _pg_returns_b)

    result = await dep.resolve_user_async(uid)
    assert result is not None
    # PG wins.
    assert result.email == "from-pg@hk.local"
    assert UserRole.ADMIN in result.roles


@pytest.mark.asyncio
async def test_resolve_user_async_returns_none_when_neither_has_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R6-4 — neither store has the user → None (caller closes WS with 1008)."""
    dep.reset_users_for_test()

    async def _pg_returns_none(_uid):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(dep, "get_user_by_id_pg", _pg_returns_none)

    result = await dep.resolve_user_async(uuid4())
    assert result is None


def test_chat_endpoint_uses_resolve_user_async() -> None:
    """R6-4 — the WS endpoint imports + calls the PG-first resolver, not
    only the in-memory ``get_user_by_id``.

    We assert by AST so prose mentions don't false-fire.
    """
    import ast
    import inspect

    import hk_ipo_agent.api.websocket.chat_endpoint as ws_mod

    tree = ast.parse(inspect.getsource(ws_mod))
    found_call = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else (func.attr if isinstance(func, ast.Attribute) else None)
            )
            if name == "resolve_user_async":
                found_call = True
                break
    assert found_call, (
        "chat_endpoint.py must call resolve_user_async(user_id) — "
        "R6-4 requires PG-first, in-memory-fallback user lookup."
    )
