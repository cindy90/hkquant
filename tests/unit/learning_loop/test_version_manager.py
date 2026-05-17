"""version_manager.py tests — Phase 10a per ADR 0015.

PG-required tests verify the full bump_version / get_active /
rollback / list_versions round trip against the real config_versions
table.
"""

from __future__ import annotations

import functools
from collections.abc import Iterator

import psycopg
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from hk_ipo_agent.common.settings import get_settings
from hk_ipo_agent.learning_loop.version_manager import (
    DEFAULT_SEED_VERSION,
    VersionManager,
    bump_semver_patch,
    hash_content,
)


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


@pytest.fixture(autouse=True)
def _fresh_engine() -> Iterator[None]:
    from hk_ipo_agent.data.database import async_session_factory, get_engine  # noqa: PLC0415

    get_engine.cache_clear()  # type: ignore[attr-defined]
    async_session_factory.cache_clear()  # type: ignore[attr-defined]
    yield
    get_engine.cache_clear()  # type: ignore[attr-defined]
    async_session_factory.cache_clear()  # type: ignore[attr-defined]


def _sync_dsn() -> str:
    return get_settings().database.url.replace(
        "postgresql+asyncpg://", "postgresql://", 1,
    )


@pytest_asyncio.fixture
async def sf():
    """Async sessionmaker + clean config_versions table for the test."""
    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM config_versions")
        conn.commit()
    engine = create_async_engine(get_settings().database.url, poolclass=NullPool)
    sf_ = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    yield sf_
    await engine.dispose()


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_bump_semver_patch_basic() -> None:
    assert bump_semver_patch("1.0.0") == "1.0.1"
    assert bump_semver_patch("3.14.99") == "3.14.100"


def test_bump_semver_patch_invalid_falls_back_to_seed() -> None:
    assert bump_semver_patch("not-a-version") == DEFAULT_SEED_VERSION
    assert bump_semver_patch("1.2") == DEFAULT_SEED_VERSION


def test_hash_content_deterministic() -> None:
    a = {"x": 1, "y": [1, 2]}
    b = {"y": [1, 2], "x": 1}
    assert hash_content(a) == hash_content(b)


def test_hash_content_changes_on_modification() -> None:
    a = {"x": 1}
    b = {"x": 2}
    assert hash_content(a) != hash_content(b)


# ---------------------------------------------------------------------------
# VersionManager — PG-backed
# ---------------------------------------------------------------------------


@pg_required
@pytest.mark.asyncio
async def test_version_manager_first_bump_is_seed(sf) -> None:
    vm = VersionManager(sf)
    v = await vm.bump_version(
        "config/x.yaml",
        {"weight": 1.0},
        applied_by="test",
    )
    assert v.version == DEFAULT_SEED_VERSION
    assert v.change_type == "learning_loop_applied"


@pg_required
@pytest.mark.asyncio
async def test_version_manager_subsequent_bumps_increment(sf) -> None:
    vm = VersionManager(sf)
    v1 = await vm.bump_version("config/x.yaml", {"weight": 1.0})
    v2 = await vm.bump_version("config/x.yaml", {"weight": 2.0})
    v3 = await vm.bump_version("config/x.yaml", {"weight": 3.0})
    assert v1.version == "1.0.0"
    assert v2.version == "1.0.1"
    assert v3.version == "1.0.2"


@pg_required
@pytest.mark.asyncio
async def test_version_manager_get_active_returns_latest(sf) -> None:
    vm = VersionManager(sf)
    await vm.bump_version("config/x.yaml", {"weight": 1.0})
    await vm.bump_version("config/x.yaml", {"weight": 2.0})
    active = await vm.get_active_version("config/x.yaml")
    assert active is not None
    assert active.version == "1.0.1"
    assert active.content == {"weight": 2.0}


@pg_required
@pytest.mark.asyncio
async def test_version_manager_get_active_returns_none_for_unknown(sf) -> None:
    vm = VersionManager(sf)
    assert await vm.get_active_version("config/never_existed.yaml") is None


@pg_required
@pytest.mark.asyncio
async def test_version_manager_list_versions_orders_newest_first(sf) -> None:
    vm = VersionManager(sf)
    await vm.bump_version("config/x.yaml", {"w": 1})
    await vm.bump_version("config/x.yaml", {"w": 2})
    await vm.bump_version("config/x.yaml", {"w": 3})
    versions = await vm.list_versions("config/x.yaml")
    assert len(versions) == 3
    assert versions[0].version == "1.0.2"
    assert versions[-1].version == "1.0.0"


@pg_required
@pytest.mark.asyncio
async def test_version_manager_rollback_creates_new_row(sf) -> None:
    vm = VersionManager(sf)
    await vm.bump_version("config/x.yaml", {"w": 1})  # 1.0.0
    await vm.bump_version("config/x.yaml", {"w": 2})  # 1.0.1
    rolled = await vm.rollback("config/x.yaml", "1.0.0", applied_by="test")
    # New row with bumped version but content from 1.0.0
    assert rolled.version == "1.0.2"
    assert rolled.content == {"w": 1}
    assert rolled.change_type == "rollback"
    active = await vm.get_active_version("config/x.yaml")
    assert active is not None
    assert active.version == "1.0.2"
    assert active.content == {"w": 1}


@pg_required
@pytest.mark.asyncio
async def test_version_manager_rollback_unknown_version_raises(sf) -> None:
    vm = VersionManager(sf)
    await vm.bump_version("config/x.yaml", {"w": 1})
    with pytest.raises(KeyError, match="not found"):
        await vm.rollback("config/x.yaml", "9.9.9")
