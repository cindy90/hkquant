"""What-If + chat + alerts + audit + analysis endpoint tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.mark.asyncio
async def test_whatif_404_for_missing_snapshot(
    client: TestClient, admin_headers
) -> None:
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


def test_audit_log_query_no_perm(client: TestClient) -> None:
    # Login as viewer (no READ_AUDIT permission)
    login = client.post(
        "/api/auth/login",
        json={"email": "viewer@hk.local", "password": "viewer"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]
    r = client.get(
        "/api/audit/logs", headers={"Authorization": f"Bearer {token}"}
    )
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
