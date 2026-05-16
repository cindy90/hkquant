"""Health / readiness / OpenAPI schema completeness tests."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_no_auth_required(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert "X-Request-Id" in r.headers


def test_ready_no_auth(client: TestClient) -> None:
    r = client.get("/ready")
    assert r.status_code == 200
    assert r.json()["ready"] is True


def test_openapi_schema_complete(client: TestClient) -> None:
    """UI ``openapi-typescript`` requires a 3.1 schema with our key paths."""
    r = client.get("/openapi.json")
    assert r.status_code == 200
    spec = r.json()
    assert spec["openapi"] == "3.1.0"
    assert spec["info"]["title"] == "HK IPO Cornerstone Agent API"
    # Bearer auth declared
    assert "BearerAuth" in spec["components"]["securitySchemes"]
    # Key paths exist
    must_have = {
        "/health",
        "/api/auth/login",
        "/api/dashboard/summary",
        "/api/snapshots/",
        "/api/whatif/run",
        "/api/chat/sessions",
        "/api/alerts/",
        "/api/audit/logs",
        "/api/stream/events",
    }
    paths = set(spec.get("paths", {}).keys())
    missing = must_have - paths
    assert not missing, f"OpenAPI missing paths: {missing}"


def test_unknown_endpoint_returns_404(client: TestClient) -> None:
    r = client.get("/api/does-not-exist")
    assert r.status_code == 404
