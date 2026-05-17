"""What-If + chat + alerts + audit + analysis endpoint tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hk_ipo_agent.api.auth.audit_middleware import get_audit_store


@pytest.mark.asyncio
async def test_whatif_404_for_missing_snapshot(client: TestClient, admin_headers) -> None:
    r = client.post(
        "/api/whatif/run",
        json={
            "snapshot_id": "00000000-0000-0000-0000-000000000000",
            "modified_assumptions": {},
        },
        headers=admin_headers,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_whatif_with_real_snapshot(
    client: TestClient, admin_headers, seeded_snapshot
) -> None:
    r = client.post(
        "/api/whatif/run",
        json={
            "snapshot_id": str(seeded_snapshot.id),
            "modified_assumptions": {"regime_score": 0.05, "mc_seed": 7},
        },
        headers=admin_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert "delta_summary" in body
    assert "original_distribution" in body
    assert "new_distribution" in body


@pytest.mark.asyncio
async def test_chat_create_session(client: TestClient, admin_headers) -> None:
    r = client.post(
        "/api/chat/sessions",
        json={"title": "test session"},
        headers=admin_headers,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["title"] == "test session"
    assert body["websocket_path"].startswith("/api/ws/chat/")


def test_alerts_empty(client: TestClient, admin_headers) -> None:
    r = client.get("/api/alerts/", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["total"] == 0


@pytest.mark.asyncio
async def test_audit_log_records_authenticated_user_id(
    client: TestClient, admin_headers
) -> None:
    """R2-6 — write requests through audit middleware must capture user_id.

    Pre-fix ``audit_middleware.dispatch`` read ``request.state.current_user``
    but ``get_current_user`` never wrote it, so every audit_log row had
    user_id=None — equivalent to a missing SOX-style audit trail subject.
    CLAUDE.md §UI 集成约束 §3 mandates 'all writes through audit middleware'
    which is only meaningful if subject identity actually lands.

    Verification: issue a write (POST) with admin Bearer token, then query
    the audit store and assert the latest row's user_id is non-None
    and equal to the auth subject.
    """
    # Trigger a write request — chat session creation is a quick POST.
    r = client.post(
        "/api/chat/sessions",
        json={"title": "R2-6 audit subject test"},
        headers=admin_headers,
    )
    assert r.status_code == 201

    # Audit row should now have the admin user_id attached.
    store = get_audit_store()
    rows = await store.query(limit=10)
    assert len(rows) > 0, "AuditMiddleware should have written at least one row"
    latest = rows[0]
    assert latest.user_id is not None, (
        "audit_log row has user_id=None — R2-6 (request.state.current_user "
        "wiring in get_current_user) is not applied."
    )
    assert latest.user_email == "admin-test@hk.local"
    assert "POST" in latest.action
    assert "/api/chat/sessions" in latest.action


def test_audit_log_query_no_perm(client: TestClient) -> None:
    # Login as viewer (no READ_AUDIT permission)
    login = client.post(
        "/api/auth/login",
        json={"email": "viewer@hk.local", "password": "viewer"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]
    r = client.get("/api/audit/logs", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_analysis_trigger_returns_202(client: TestClient, admin_headers) -> None:
    r = client.post(
        "/api/analysis/run",
        json={
            "ipo_id": "ipo-x",
            "prospectus_id": "p-x",
            "as_of_date": "2026-05-16",
        },
        headers=admin_headers,
    )
    assert r.status_code == 202
    body = r.json()
    assert body["ipo_id"] == "ipo-x"
    assert body["run_id"]
