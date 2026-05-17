"""Reviews / proposals / drift router tests — Phase 7.5b per ADR 0012.

These exercise the routers against the real docker postgres because the
``prediction_reviews`` table is the focus of the test. The Phase 7
``client`` fixture wraps the full app; we seed snapshots + reviews
directly into the docker DB before each test.
"""

from __future__ import annotations

import uuid
from datetime import date

import psycopg
import pytest
from fastapi.testclient import TestClient

from hk_ipo_agent.common.enums import AdjustmentStatus
from hk_ipo_agent.common.settings import get_settings


def _sync_dsn() -> str:
    """psycopg-compatible DSN — strip the asyncpg dialect prefix."""
    url = get_settings().database.url
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _seed_snapshot_and_review(
    *,
    adjustment_status: AdjustmentStatus = AdjustmentStatus.PROPOSED,
    proposals: list | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Insert one ipo_event + snapshot + review row directly via sync psycopg.

    Synchronous to avoid the cached AsyncEngine reuse across pytest event
    loops issue. Returns ``(snapshot_id, review_id)``.
    """
    snap_id = uuid.uuid4()
    review_id = uuid.uuid4()
    ipo_id = uuid.uuid4()

    with psycopg.connect(_sync_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "TRUNCATE TABLE prediction_reviews, prediction_outcomes, post_ipo_events, "
                "prediction_snapshots, ipo_events RESTART IDENTITY CASCADE"
            )
            cur.execute(
                "INSERT INTO ipo_events (id, stock_code, company_name_zh, listing_type, "
                "created_at, updated_at) VALUES (%s, %s, %s, %s, NOW(), NOW())",
                (ipo_id, "TEST.HK", "Test", "mainboard_tech"),
            )
            cur.execute(
                "INSERT INTO prediction_snapshots "
                "(id, ipo_id, as_of_date, prospectus_version, input_data_hash, "
                " input_data_snapshot, agent_outputs, valuation_output, debate_output, "
                " decision, system_version, model_versions, config_snapshot, "
                " total_cost_usd, runtime_seconds, created_at) "
                "VALUES (%s, %s, %s, 'PHIP', %s, '{}'::jsonb, '{}'::jsonb, "
                " '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, '0.0.1', '{}'::jsonb, '{}'::jsonb, "
                " 0.0, 0.0, NOW())",
                (snap_id, ipo_id, date(2026, 1, 1), "0" * 64),
            )
            proposed_json = (
                '[{"target_path": "config/x.yaml", "adjustment_type": "weight_change"}]'
                if proposals
                else None
            )
            cur.execute(
                "INSERT INTO prediction_reviews "
                "(id, snapshot_id, review_checkpoint_day, reviewer, "
                " primary_attribution, proposed_adjustments, adjustment_status, "
                " notes_md, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, NOW(), NOW())",
                (
                    review_id,
                    snap_id,
                    30,
                    "alice",
                    "valuation_model",
                    proposed_json,
                    adjustment_status.value,
                    "test note",
                ),
            )
        conn.commit()
    return snap_id, review_id


def _truncate_review_tables() -> None:
    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "TRUNCATE TABLE prediction_reviews, prediction_outcomes, post_ipo_events, "
            "prediction_snapshots, ipo_events RESTART IDENTITY CASCADE"
        )
        conn.commit()


@pytest.fixture(autouse=True)
def _fresh_async_engine() -> None:
    """Clear lru_cached AsyncEngine before each test so the engine binds
    to the per-test event loop (avoids "Event loop is closed" during
    asyncpg teardown across pytest's function-scoped loops).
    """
    from hk_ipo_agent.data.database import async_session_factory, get_engine

    get_engine.cache_clear()  # type: ignore[attr-defined]
    async_session_factory.cache_clear()  # type: ignore[attr-defined]
    yield
    get_engine.cache_clear()  # type: ignore[attr-defined]
    async_session_factory.cache_clear()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Reviews router
# ---------------------------------------------------------------------------


def test_list_reviews_returns_seeded_row(client: TestClient, admin_headers) -> None:
    _seed_snapshot_and_review()
    r = client.get("/api/reviews/", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["total"] >= 1
    items = body["data"]
    assert items[0]["primary_attribution"] == "valuation_model"


def test_list_reviews_filters_by_status(client: TestClient, admin_headers) -> None:
    _seed_snapshot_and_review(adjustment_status=AdjustmentStatus.ACCEPTED)
    r = client.get("/api/reviews/?adjustment_status=proposed", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["meta"]["total"] == 0
    r2 = client.get("/api/reviews/?adjustment_status=accepted", headers=admin_headers)
    assert r2.json()["meta"]["total"] == 1


def test_list_reviews_for_snapshot(client: TestClient, admin_headers) -> None:
    snap_id, _ = _seed_snapshot_and_review()
    r = client.get(f"/api/reviews/snapshot/{snap_id}", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["meta"]["total"] == 1


def test_submit_review_returns_404_for_unknown_snapshot(client: TestClient, admin_headers) -> None:
    r = client.post(
        f"/api/reviews/snapshot/{uuid.uuid4()}",
        json={"reviewer": "alice", "what_we_got_right": "x", "what_we_got_wrong": "y"},
        headers=admin_headers,
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Proposals router
# ---------------------------------------------------------------------------


def test_list_proposals_returns_reviews_with_adjustments(client: TestClient, admin_headers) -> None:
    _seed_snapshot_and_review(proposals=[{"target_path": "x"}])
    r = client.get("/api/proposals/", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["meta"]["total"] >= 1


def test_accept_proposal_transitions_to_accepted(client: TestClient, admin_headers) -> None:
    _seed_snapshot_and_review(proposals=[{"target_path": "x"}])
    # Find the review_id from list.
    r = client.get("/api/proposals/", headers=admin_headers)
    review_id = r.json()["data"][0]["review_id"]
    r2 = client.post(
        f"/api/proposals/{review_id}/accept",
        json={"reviewer": "alice", "rationale": "agreed"},
        headers=admin_headers,
    )
    assert r2.status_code == 200
    assert r2.json()["adjustment_status"] == AdjustmentStatus.ACCEPTED.value


def test_reject_proposal_transitions_to_rejected(client: TestClient, admin_headers) -> None:
    _seed_snapshot_and_review(proposals=[{"target_path": "x"}])
    r = client.get("/api/proposals/", headers=admin_headers)
    review_id = r.json()["data"][0]["review_id"]
    r2 = client.post(
        f"/api/proposals/{review_id}/reject",
        json={"reviewer": "alice", "rationale": "data drift"},
        headers=admin_headers,
    )
    assert r2.status_code == 200
    assert r2.json()["adjustment_status"] == AdjustmentStatus.REJECTED.value


def test_accept_proposal_rejects_already_accepted(client: TestClient, admin_headers) -> None:
    _seed_snapshot_and_review(
        proposals=[{"target_path": "x"}],
        adjustment_status=AdjustmentStatus.ACCEPTED,
    )
    r = client.get("/api/proposals/", headers=admin_headers)
    review_id = r.json()["data"][0]["review_id"]
    r2 = client.post(
        f"/api/proposals/{review_id}/accept",
        json={"reviewer": "alice"},
        headers=admin_headers,
    )
    assert r2.status_code == 409  # CONFLICT — illegal transition


def test_accept_proposal_404_for_unknown_review(client: TestClient, admin_headers) -> None:
    r = client.post(
        f"/api/proposals/{uuid.uuid4()}/accept",
        json={"reviewer": "alice"},
        headers=admin_headers,
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Drift router
# ---------------------------------------------------------------------------


def test_drift_buckets_aggregate_by_attribution(client: TestClient, admin_headers) -> None:
    _seed_snapshot_and_review()
    r = client.get("/api/drift/", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total_reviews_scanned"] >= 1
    assert any(b["primary_attribution"] == "valuation_model" for b in body["buckets"])
    assert "MVP" in body["note"]


def test_drift_handles_empty_table(client: TestClient, admin_headers) -> None:
    """Empty PG → empty buckets, 0 reviews scanned."""
    _truncate_review_tables()
    r = client.get("/api/drift/", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total_reviews_scanned"] == 0
    assert body["buckets"] == []
