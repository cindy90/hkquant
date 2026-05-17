"""Shared fixtures for backtest unit tests."""

from __future__ import annotations

import functools
from collections.abc import Iterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


@pytest.fixture(autouse=True)
def _fresh_async_engine() -> Iterator[None]:
    """Mirror tests/unit/api + tests/unit/prediction_registry pattern —
    clear the lru_cached AsyncEngine so cross-loop reuse doesn't trigger
    asyncpg teardown errors."""
    from hk_ipo_agent.data.database import async_session_factory, get_engine  # noqa: PLC0415

    get_engine.cache_clear()  # type: ignore[attr-defined]
    async_session_factory.cache_clear()  # type: ignore[attr-defined]
    yield
    get_engine.cache_clear()  # type: ignore[attr-defined]
    async_session_factory.cache_clear()  # type: ignore[attr-defined]


@pytest_asyncio.fixture
async def fresh_sf():
    """Async sessionmaker tied to the configured DB.

    Phase 8 tests use this for AsOfDataProvider — when docker is up the
    tests hit PG; otherwise the pg_required-marked tests skip and the
    rest of the suite uses the sf only as an IPC handle (no actual
    queries fire in V8LiteScorer / runner harness paths).
    """
    from hk_ipo_agent.common.settings import get_settings  # noqa: PLC0415

    engine = create_async_engine(get_settings().database.url, poolclass=NullPool)
    sf = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    yield sf
    await engine.dispose()


@functools.lru_cache(maxsize=1)
def _pg_available() -> bool:
    """Probe docker postgres once per session.

    Phase 7.5 fixtures assumed docker is up; Phase 8 inherits that, but
    we keep this opt-in skip so tests that depend on PG aren't blocked
    when the dev DB happens to be down. CI / Airflow always have PG
    running so this is dev-loop only.
    """
    import psycopg  # noqa: PLC0415

    from hk_ipo_agent.common.settings import get_settings  # noqa: PLC0415

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
