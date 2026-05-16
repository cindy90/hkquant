"""AlertRouter tests — Phase 7.5c-2 per ADR 0012."""

from __future__ import annotations

import uuid

import psycopg
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from hk_ipo_agent.common.enums import AlertLevel
from hk_ipo_agent.common.settings import get_settings
from hk_ipo_agent.data.models import AlertRow
from hk_ipo_agent.prediction_registry.alerts import (
    DEDUP_WINDOW,
    AlertRouter,
    load_alerts_config,
)


def _sync_dsn() -> str:
    return get_settings().database.url.replace("postgresql+asyncpg://", "postgresql://", 1)


@pytest_asyncio.fixture
async def fresh_sf():
    engine = create_async_engine(get_settings().database.url, poolclass=NullPool)
    sf = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    yield sf
    await engine.dispose()


def _truncate_alerts() -> None:
    """Truncate alerts + ipo_events; alerts.related_ipo_id has FK to ipo_events."""
    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE alerts, ipo_events RESTART IDENTITY CASCADE")
        conn.commit()


def _seed_ipo(ipo_id: uuid.UUID) -> None:
    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ipo_events (id, stock_code, company_name_zh, listing_type, "
            "created_at, updated_at) VALUES (%s, %s, %s, %s, NOW(), NOW())",
            (ipo_id, "TEST.HK", "Test", "mainboard_tech"),
        )
        conn.commit()


def test_load_alerts_config_finds_three_levels() -> None:
    cfg = load_alerts_config()
    assert set(cfg.get("levels", {}).keys()) == {"info", "warning", "critical"}
    assert cfg.get("dedup_window_hours") == 24


def test_dedup_window_constant() -> None:
    assert DEDUP_WINDOW.total_seconds() == 24 * 3600


# ===========================================================================
# emit() basics
# ===========================================================================


@pytest.mark.asyncio
async def test_emit_writes_alert_row(fresh_sf) -> None:
    _truncate_alerts()
    ipo_id = uuid.uuid4()
    _seed_ipo(ipo_id)
    router = AlertRouter(session_factory=fresh_sf, config={"levels": {}})
    alert = await router.emit(
        level=AlertLevel.WARNING,
        category="stale_pricing",
        message="IPO X 在 PRICING 状态停留 22 天",
        actionable_info="人工核实是否已定价但 detect_listed 未触发",
        related_ipo_id=ipo_id,
    )
    assert alert is not None
    assert alert.level is AlertLevel.WARNING
    async with fresh_sf() as s:
        rows = (await s.execute(select(AlertRow))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_emit_rejects_empty_actionable_info(fresh_sf) -> None:
    """CLAUDE.md v1.2: 'Failed' is unacceptable; actionable_info is required."""
    _truncate_alerts()
    router = AlertRouter(session_factory=fresh_sf, config={})
    with pytest.raises(ValueError, match="actionable_info"):
        await router.emit(
            level=AlertLevel.CRITICAL, category="x",
            message="something failed", actionable_info="",
        )
    with pytest.raises(ValueError):
        await router.emit(
            level=AlertLevel.CRITICAL, category="x",
            message="x", actionable_info="   ",  # whitespace only
        )


# ===========================================================================
# 24h dedup
# ===========================================================================


@pytest.mark.asyncio
async def test_dedup_suppresses_second_emit_in_window(fresh_sf) -> None:
    _truncate_alerts()
    ipo_id = uuid.uuid4()
    _seed_ipo(ipo_id)
    router = AlertRouter(session_factory=fresh_sf, config={})
    first = await router.emit(
        level=AlertLevel.WARNING, category="stale_pricing",
        message="first", actionable_info="check",
        related_ipo_id=ipo_id,
    )
    second = await router.emit(
        level=AlertLevel.WARNING, category="stale_pricing",
        message="second", actionable_info="check",
        related_ipo_id=ipo_id,
    )
    assert first is not None
    assert second is None  # deduped
    async with fresh_sf() as s:
        rows = (await s.execute(select(AlertRow))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_dedup_differentiates_by_category(fresh_sf) -> None:
    """Different category for same IPO → not deduped."""
    _truncate_alerts()
    ipo_id = uuid.uuid4()
    _seed_ipo(ipo_id)
    router = AlertRouter(session_factory=fresh_sf, config={})
    a = await router.emit(
        level=AlertLevel.WARNING, category="stale_pricing",
        message="x", actionable_info="check", related_ipo_id=ipo_id,
    )
    b = await router.emit(
        level=AlertLevel.WARNING, category="earnings_review_needed",
        message="y", actionable_info="check", related_ipo_id=ipo_id,
    )
    assert a is not None
    assert b is not None


@pytest.mark.asyncio
async def test_dedup_differentiates_by_level(fresh_sf) -> None:
    """Same category + ipo but different level → not deduped."""
    _truncate_alerts()
    ipo_id = uuid.uuid4()
    _seed_ipo(ipo_id)
    router = AlertRouter(session_factory=fresh_sf, config={})
    warn = await router.emit(
        level=AlertLevel.WARNING, category="stale_pricing",
        message="warn", actionable_info="check", related_ipo_id=ipo_id,
    )
    crit = await router.emit(
        level=AlertLevel.CRITICAL, category="stale_pricing",
        message="crit", actionable_info="escalate", related_ipo_id=ipo_id,
    )
    assert warn is not None
    assert crit is not None


@pytest.mark.asyncio
async def test_dedup_differentiates_by_ipo_id(fresh_sf) -> None:
    _truncate_alerts()
    ipo_a, ipo_b = uuid.uuid4(), uuid.uuid4()
    _seed_ipo(ipo_a)
    _seed_ipo(ipo_b)
    router = AlertRouter(session_factory=fresh_sf, config={})
    a = await router.emit(
        level=AlertLevel.WARNING, category="stale_pricing",
        message="x", actionable_info="c", related_ipo_id=ipo_a,
    )
    b = await router.emit(
        level=AlertLevel.WARNING, category="stale_pricing",
        message="y", actionable_info="c", related_ipo_id=ipo_b,
    )
    assert a is not None
    assert b is not None


# ===========================================================================
# Ack flow
# ===========================================================================


@pytest.mark.asyncio
async def test_ack_marks_alert_acknowledged(fresh_sf) -> None:
    _truncate_alerts()
    router = AlertRouter(session_factory=fresh_sf, config={})
    alert = await router.emit(
        level=AlertLevel.WARNING, category="stale_pricing",
        message="x", actionable_info="c",
    )
    assert alert is not None
    # Find the just-written row's id.
    async with fresh_sf() as s:
        row = (await s.execute(select(AlertRow))).scalar_one()
    ok = await router.ack(row.id, ack_by="alice")
    assert ok is True
    async with fresh_sf() as s:
        refreshed = (await s.execute(select(AlertRow))).scalar_one()
    assert refreshed.acknowledged_at is not None
    assert refreshed.acknowledged_by == "alice"


@pytest.mark.asyncio
async def test_ack_returns_false_for_unknown_id(fresh_sf) -> None:
    _truncate_alerts()
    router = AlertRouter(session_factory=fresh_sf, config={})
    assert await router.ack(uuid.uuid4(), ack_by="alice") is False


@pytest.mark.asyncio
async def test_ack_returns_false_when_already_acknowledged(fresh_sf) -> None:
    _truncate_alerts()
    router = AlertRouter(session_factory=fresh_sf, config={})
    await router.emit(
        level=AlertLevel.WARNING, category="x",
        message="x", actionable_info="c",
    )
    async with fresh_sf() as s:
        row = (await s.execute(select(AlertRow))).scalar_one()
    await router.ack(row.id, ack_by="alice")
    second = await router.ack(row.id, ack_by="bob")
    assert second is False
