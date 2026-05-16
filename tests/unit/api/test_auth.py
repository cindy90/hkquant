"""Login + JWT + protected route tests."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_login_success(client: TestClient) -> None:
    r = client.post(
        "/api/auth/login",
        json={"email": "admin@hk.local", "password": "admin"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["access_token"]
    assert body["roles"] == ["admin"]
    assert body["expires_in_seconds"] > 0


def test_login_wrong_password(client: TestClient) -> None:
    r = client.post(
        "/api/auth/login",
        json={"email": "admin@hk.local", "password": "WRONG"},
    )
    assert r.status_code == 401


def test_protected_endpoint_without_token(client: TestClient) -> None:
    r = client.get("/api/dashboard/summary")
    assert r.status_code == 401


def test_protected_endpoint_with_admin_token(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    r = client.get("/api/dashboard/summary", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert "active_snapshots" in body


def test_me_endpoint(client: TestClient, admin_headers: dict[str, str]) -> None:
    r = client.get("/api/auth/me", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["email"]


def test_invalid_token_rejected(client: TestClient) -> None:
    r = client.get(
        "/api/dashboard/summary",
        headers={"Authorization": "Bearer not.a.real.jwt"},
    )
    assert r.status_code == 401
