"""Phase 7.5b-3 PG-store tests — chat / event_bus / whatif / users.

Each test creates a NullPool engine bound to the current event loop so
the cached AsyncEngine reuse doesn't cause asyncpg teardown errors.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import psycopg
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from hk_ipo_agent.api.auth.dependencies import (
    _UserRecord,
    get_user_by_id_pg,
)
from hk_ipo_agent.api.streaming.event_bus import EventBus
from hk_ipo_agent.api.websocket.manager import PGChatStore
from hk_ipo_agent.common.enums import (
    ChatMessageRole,
    RealtimeEventType,
    UserRole,
)
from hk_ipo_agent.common.settings import get_settings
from hk_ipo_agent.data.models import (
    RealtimeEventRow,
)


def _sync_dsn() -> str:
    return get_settings().database.url.replace("postgresql+asyncpg://", "postgresql://", 1)


@pytest.fixture(autouse=True)
def _fresh_async_engine() -> None:
    from hk_ipo_agent.data.database import async_session_factory, get_engine  # noqa: PLC0415

    get_engine.cache_clear()  # type: ignore[attr-defined]
    async_session_factory.cache_clear()  # type: ignore[attr-defined]
    yield
    get_engine.cache_clear()  # type: ignore[attr-defined]
    async_session_factory.cache_clear()  # type: ignore[attr-defined]


@pytest_asyncio.fixture
async def fresh_sf():
    """NullPool engine + sessionmaker bound to the test's event loop."""
    engine = create_async_engine(get_settings().database.url, poolclass=NullPool)
    sf = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    yield sf
    await engine.dispose()


def _truncate_chat_and_users() -> None:
    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "TRUNCATE TABLE chat_messages, chat_sessions, user_roles, user_accounts, "
            "whatif_calculations, realtime_events, prediction_snapshots, ipo_events "
            "RESTART IDENTITY CASCADE"
        )
        conn.commit()


# ---------------------------------------------------------------------------
# PGChatStore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pg_chat_store_session_and_messages_roundtrip(fresh_sf) -> None:
    _truncate_chat_and_users()
    # Seed a user_accounts row so chat_sessions FK passes.
    user_id = uuid.uuid4()
    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO user_accounts (id, email, sso_provider, sso_subject, is_active, created_at) "
            "VALUES (%s, %s, 'local', %s, true, NOW())",
            (user_id, "u@x", str(user_id)),
        )
        conn.commit()
    store = PGChatStore(session_factory=fresh_sf)
    session = await store.create_session(user_id=user_id, title="test")
    assert session.user_id == user_id

    # Append 3 messages
    for i, txt in enumerate(["hi", "ok", "bye"]):
        msg = await store.append_message(
            session_id=session.id,
            role=ChatMessageRole.USER if i % 2 == 0 else ChatMessageRole.ASSISTANT,
            content=txt,
        )
        assert msg.sequence == i

    messages = await store.list_messages(session.id)
    assert [m.content for m in messages] == ["hi", "ok", "bye"]
    assert [m.sequence for m in messages] == [0, 1, 2]


@pytest.mark.asyncio
async def test_pg_chat_store_get_session_returns_none_for_unknown(fresh_sf) -> None:
    _truncate_chat_and_users()
    store = PGChatStore(session_factory=fresh_sf)
    assert await store.get_session(uuid.uuid4()) is None


@pytest.mark.asyncio
async def test_pg_chat_store_append_message_raises_for_unknown_session(fresh_sf) -> None:
    _truncate_chat_and_users()
    store = PGChatStore(session_factory=fresh_sf)
    with pytest.raises(KeyError):
        await store.append_message(
            session_id=uuid.uuid4(), role=ChatMessageRole.USER, content="x",
        )


# ---------------------------------------------------------------------------
# EventBus PG persistence hook
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_bus_persists_when_session_factory_provided(fresh_sf) -> None:
    _truncate_chat_and_users()
    bus = EventBus(session_factory=fresh_sf)
    await bus.publish(
        RealtimeEventType.SNAPSHOT_CREATED,
        payload={"ipo_id": "test"},
    )
    async with fresh_sf() as s:
        rows = (await s.execute(select(RealtimeEventRow))).scalars().all()
    assert len(rows) == 1
    assert rows[0].event_type == "snapshot.created"
    assert rows[0].broadcast_count == 0  # no subscribers
    assert rows[0].payload == {"ipo_id": "test"}


@pytest.mark.asyncio
async def test_event_bus_skips_persistence_without_session_factory(fresh_sf) -> None:
    _truncate_chat_and_users()
    bus = EventBus()  # default: no PG
    await bus.publish(
        RealtimeEventType.SNAPSHOT_CREATED, payload={"ipo_id": "test"},
    )
    async with fresh_sf() as s:
        rows = (await s.execute(select(RealtimeEventRow))).scalars().all()
    assert rows == []


# ---------------------------------------------------------------------------
# WhatIf persistence (best-effort INSERT into whatif_calculations)
# ---------------------------------------------------------------------------


def test_whatif_endpoint_persists_calculation(
    client, admin_headers, seeded_snapshot,
) -> None:
    """Sync test: TestClient + sync psycopg verify avoids the async-loop
    mismatch (TestClient uses its own loop; verifying via psycopg keeps
    everything synchronous on the caller side).

    Seeds matching ipo_event + prediction_snapshot rows into PG so the
    ``whatif_calculations.snapshot_id`` FK passes — the in-memory
    seeded_snapshot fixture only writes to the in-process registry.
    """
    _truncate_chat_and_users()
    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ipo_events (id, stock_code, company_name_zh, listing_type, "
            "created_at, updated_at) VALUES (%s, %s, %s, %s, NOW(), NOW())",
            (seeded_snapshot.ipo_id, "TEST.HK", "Test", "mainboard_tech"),
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
            (seeded_snapshot.id, seeded_snapshot.ipo_id, seeded_snapshot.as_of_date, "0" * 64),
        )
        conn.commit()

    r = client.post(
        "/api/whatif/run",
        json={
            "snapshot_id": str(seeded_snapshot.id),
            "modified_assumptions": {"mc_seed": 1},
        },
        headers=admin_headers,
    )
    assert r.status_code == 200
    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM whatif_calculations")
        (count,) = cur.fetchone()
        cur.execute("SELECT modified_assumptions FROM whatif_calculations LIMIT 1")
        row = cur.fetchone()
    assert count == 1
    assert row is not None
    assert row[0] == {"mc_seed": 1}


def test_whatif_endpoint_returns_200_even_when_persist_fails(
    client, admin_headers, seeded_snapshot,
) -> None:
    """Best-effort persistence: PG FK violation logs a warning but the
    caller still gets 200 with the computed delta. This is the spirit
    of ADR 0011 (UI can still demo without DB) carried into 7.5b-3."""
    _truncate_chat_and_users()
    # NB: no snapshot pre-seeded — FK violation triggers the catch in
    # _persist_calculation. The response should still be 200.
    r = client.post(
        "/api/whatif/run",
        json={
            "snapshot_id": str(seeded_snapshot.id),
            "modified_assumptions": {"mc_seed": 7},
        },
        headers=admin_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert "delta_summary" in body


# ---------------------------------------------------------------------------
# Users PG lookup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_user_by_id_pg_returns_record_with_roles(fresh_sf) -> None:
    _truncate_chat_and_users()
    user_id = uuid.uuid4()
    granted_at = datetime.now(UTC)
    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO user_accounts (id, email, sso_provider, sso_subject, is_active, created_at) "
            "VALUES (%s, %s, 'local', %s, true, NOW())",
            (user_id, "alice@hk.local", str(user_id)),
        )
        cur.execute(
            "INSERT INTO user_roles (id, user_id, role, granted_at) "
            "VALUES (%s, %s, %s, %s)",
            (uuid.uuid4(), user_id, "reviewer", granted_at),
        )
        conn.commit()
    rec = await get_user_by_id_pg(user_id)
    assert rec is not None
    assert isinstance(rec, _UserRecord)
    assert rec.email == "alice@hk.local"
    assert UserRole.REVIEWER in rec.roles


@pytest.mark.asyncio
async def test_get_user_by_id_pg_returns_none_for_inactive(fresh_sf) -> None:
    _truncate_chat_and_users()
    user_id = uuid.uuid4()
    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO user_accounts (id, email, sso_provider, sso_subject, is_active, created_at) "
            "VALUES (%s, %s, 'local', %s, false, NOW())",
            (user_id, "ghost@hk.local", str(user_id)),
        )
        conn.commit()
    rec = await get_user_by_id_pg(user_id)
    assert rec is None


@pytest.mark.asyncio
async def test_get_user_by_id_pg_returns_none_for_missing(fresh_sf) -> None:
    _truncate_chat_and_users()
    rec = await get_user_by_id_pg(uuid.uuid4())
    assert rec is None
