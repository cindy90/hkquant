"""Snapshot router tests — list / detail / memo formats."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.mark.asyncio
async def test_list_snapshots_empty(client: TestClient, admin_headers) -> None:
    r = client.get("/api/snapshots/", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["total"] == 0
    assert body["data"] == []


@pytest.mark.asyncio
async def test_get_snapshot_404(client: TestClient, admin_headers) -> None:
    r = client.get(
        "/api/snapshots/00000000-0000-0000-0000-000000000000",
        headers=admin_headers,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_with_seeded_snapshot(
    client: TestClient, admin_headers, seeded_snapshot
) -> None:
    r = client.get("/api/snapshots/", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["total"] == 1
    assert body["data"][0]["id"] == str(seeded_snapshot.id)


@pytest.mark.asyncio
async def test_get_memo_markdown(client: TestClient, admin_headers, seeded_snapshot) -> None:
    r = client.get(f"/api/snapshots/{seeded_snapshot.id}/memo.md", headers=admin_headers)
    assert r.status_code == 200
    assert "text/markdown" in r.headers["content-type"]
    assert "Investment Memo" in r.text


@pytest.mark.asyncio
async def test_get_memo_pdf_or_html(client: TestClient, admin_headers, seeded_snapshot) -> None:
    r = client.get(f"/api/snapshots/{seeded_snapshot.id}/memo.pdf", headers=admin_headers)
    assert r.status_code == 200
    # Either real PDF or HTML fallback
    assert r.headers["content-type"] in {"application/pdf", "text/html; charset=utf-8"}
    assert len(r.content) > 100
