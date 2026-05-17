"""Backtest router tests — Phase 8d per ADR 0013.

DONE-conditions covered (4 tests minimum):
- Happy: list runs / detail returns the seeded backtest run with samples.
- 404: detail for an unknown run_id → 404.
- Auth: missing Authorization header → 401.
- OpenAPI: the router's three routes are exposed in /openapi.json.
"""

from __future__ import annotations

import functools
import uuid
from collections.abc import Iterator
from datetime import date

import psycopg
import pytest

from hk_ipo_agent.common.settings import get_settings


@pytest.fixture(autouse=True)
def _fresh_async_engine() -> Iterator[None]:
    """Clear the lru_cached AsyncEngine between tests so cross-loop
    asyncpg teardown doesn't blow up on 'Event loop is closed'.

    Same pattern as test_reviews_proposals_drift.py.
    """
    from hk_ipo_agent.data.database import (  # noqa: PLC0415
        async_session_factory,
        get_engine,
    )

    get_engine.cache_clear()  # type: ignore[attr-defined]
    async_session_factory.cache_clear()  # type: ignore[attr-defined]
    yield
    get_engine.cache_clear()  # type: ignore[attr-defined]
    async_session_factory.cache_clear()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# PG availability probe (mirrors tests/unit/backtest/conftest.py)
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _pg_available() -> bool:
    url = get_settings().database.url
    dsn = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    try:
        with psycopg.connect(dsn, connect_timeout=2):
            return True
    except Exception:
        return False


pg_required = pytest.mark.skipif(
    not _pg_available(),
    reason="docker postgres unavailable — start with `docker compose up -d postgres`",
)


def _sync_dsn() -> str:
    return get_settings().database.url.replace(
        "postgresql+asyncpg://", "postgresql://", 1,
    )


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_backtest_run(
    *,
    run_id: uuid.UUID,
    sample_count: int = 3,
) -> list[uuid.UUID]:
    """Insert one ipo_event + N prediction_snapshots tagged with run_id.

    Returns the list of snapshot UUIDs inserted.
    """
    snap_ids: list[uuid.UUID] = []
    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "TRUNCATE TABLE prediction_reviews, prediction_outcomes, "
            "post_ipo_events, prediction_snapshots, ipo_events "
            "RESTART IDENTITY CASCADE"
        )
        for i in range(sample_count):
            ipo_id = uuid.uuid4()
            snap_id = uuid.uuid4()
            snap_ids.append(snap_id)
            cur.execute(
                "INSERT INTO ipo_events (id, stock_code, company_name_zh, "
                "listing_type, created_at, updated_at) VALUES (%s, %s, %s, %s, "
                "NOW(), NOW())",
                (ipo_id, f"{i:04d}.HK", f"Test {i}", "mainboard_tech"),
            )
            config_snapshot = (
                '{"backtest_run_id": "' + str(run_id) + '", '
                '"scorer": "V8LiteScorer", '
                '"horizons": ["5d", "30d", "60d", "180d"]}'
            )
            input_data = (
                '{"stock_code": "' + f"{i:04d}.HK" + '", '
                '"listing_type": "MB-TECH", '
                '"pricing_date": "2024-06-14", '
                '"regulatory_regime": "pre_new_pricing"}'
            )
            valuation = (
                '{"decision_score": ' + str(0.5 + i * 0.1) + ', '
                '"regime_score": 0.1, "regime_pass": true}'
            )
            decision = (
                '{"decision": "BACKTEST_ONLY", "confidence": 0.0, '
                '"realized_returns": {"5d": ' + str(0.05 + i * 0.01) + '}}'
            )
            cur.execute(
                "INSERT INTO prediction_snapshots "
                "(id, ipo_id, as_of_date, prospectus_version, input_data_hash, "
                " input_data_snapshot, agent_outputs, valuation_output, "
                " debate_output, decision, system_version, model_versions, "
                " config_snapshot, total_cost_usd, runtime_seconds, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s::jsonb, '{}'::jsonb, %s::jsonb, "
                " '{}'::jsonb, %s::jsonb, '0.0.1', '{}'::jsonb, %s::jsonb, "
                " 0.0, 0.0, NOW())",
                (
                    snap_id, ipo_id, date(2024, 6, 13),
                    f"backtest:{str(run_id)[:8]}", "0" * 64,
                    input_data, valuation, decision, config_snapshot,
                ),
            )
        conn.commit()
    return snap_ids


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pg_required
def test_list_backtest_runs_happy(client, admin_headers) -> None:
    run_id = uuid.uuid4()
    _seed_backtest_run(run_id=run_id, sample_count=3)
    resp = client.get("/api/backtest/runs", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["meta"]["total"] >= 1
    summaries = body["data"]
    matching = [s for s in summaries if s["run_id"] == str(run_id)]
    assert len(matching) == 1
    summary = matching[0]
    assert summary["n_samples"] == 3
    assert summary["scorer"] == "V8LiteScorer"
    assert summary["horizons"] == ["5d", "30d", "60d", "180d"]


@pg_required
def test_get_backtest_run_detail_happy(client, admin_headers) -> None:
    run_id = uuid.uuid4()
    _seed_backtest_run(run_id=run_id, sample_count=2)
    resp = client.get(f"/api/backtest/runs/{run_id}", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run_id"] == str(run_id)
    assert body["n_samples"] == 2
    assert len(body["samples"]) == 2
    assert body["scorer"] == "V8LiteScorer"
    # Sample fields project correctly.
    sample = body["samples"][0]
    assert "snapshot_id" in sample
    assert sample["listing_type"] == "MB-TECH"
    assert sample["pricing_date"] == "2024-06-14"
    assert sample["regime_pass"] is True
    assert "5d" in sample["realized_returns"]


@pg_required
def test_get_backtest_run_unknown_returns_404(client, admin_headers) -> None:
    _seed_backtest_run(run_id=uuid.uuid4(), sample_count=1)  # different run
    other_id = uuid.uuid4()
    resp = client.get(f"/api/backtest/runs/{other_id}", headers=admin_headers)
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


def test_list_backtest_runs_requires_auth(client) -> None:
    """No Authorization header → 401 from require_permission chain."""
    resp = client.get("/api/backtest/runs")
    assert resp.status_code == 401
    assert "authorization" in resp.json()["detail"].lower()


def test_backtest_routes_exposed_in_openapi(client) -> None:
    """All three router endpoints visible in /openapi.json."""
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    paths = resp.json()["paths"]
    assert "/api/backtest/runs" in paths
    assert "/api/backtest/runs/{run_id}" in paths
    assert "/api/backtest/runs/_meta/count" in paths
    # GET methods all present
    assert "get" in paths["/api/backtest/runs"]
    assert "get" in paths["/api/backtest/runs/{run_id}"]


@pg_required
def test_runs_count_endpoint(client, admin_headers) -> None:
    _seed_backtest_run(run_id=uuid.uuid4(), sample_count=2)
    resp = client.get("/api/backtest/runs/_meta/count", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["runs"] >= 1
