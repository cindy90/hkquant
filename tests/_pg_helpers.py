"""R9-7 — shared PG helpers extracted from per-suite conftests.

Pre-R9-7 the same boilerplate (fresh-engine fixture, sync DSN derivation,
TRUNCATE helpers) was duplicated across:
  * tests/unit/api/conftest.py
  * tests/unit/backtest/conftest.py
  * tests/unit/prediction_registry/conftest.py
  * tests/unit/api/test_phase7_pg_stores.py
  * tests/unit/api/test_reviews_proposals_drift.py
  * tests/e2e/test_*.py

This module is the single import point for those primitives. A future
R9-7 follow-up may also move the PG-required test modules from
``tests/unit/`` to ``tests/integration/``; for now the
``@pytest.mark.pg_required`` marker (registered in pyproject.toml) plus
this helper module are the load-bearing surface.

Usage:
    from tests._pg_helpers import sync_dsn, truncate_tables

    @pytest.mark.pg_required
    @pytest.mark.asyncio
    async def test_x(fresh_sf):
        truncate_tables("ipo_events", "prediction_snapshots")
        ...
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def sync_dsn() -> str:
    """Resolve a sync psycopg DSN from the async Settings.database.url.

    Async URL: ``postgresql+asyncpg://user:pw@host:port/db``
    Sync DSN:  ``postgresql://user:pw@host:port/db``
    """
    from hk_ipo_agent.common.settings import get_settings

    return get_settings().database.url.replace("postgresql+asyncpg://", "postgresql://", 1)


def truncate_tables(*tables: str) -> None:
    """TRUNCATE ... RESTART IDENTITY CASCADE on the named tables.

    Convenience for per-test isolation in PG-required tests. Uses sync
    psycopg so it's safe to call from sync setup fixtures.
    """
    import psycopg

    if not tables:
        return
    sql = "TRUNCATE TABLE " + ", ".join(tables) + " RESTART IDENTITY CASCADE"
    with psycopg.connect(sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute(sql)
        conn.commit()


async def fresh_async_session_factory() -> Any:
    """Build a NullPool async engine + session-maker bound to the test's
    event loop. Caller is responsible for ``await engine.dispose()``.

    Pattern matches the ``fresh_sf`` fixture duplicated across conftests.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from hk_ipo_agent.common.settings import get_settings

    engine = create_async_engine(get_settings().database.url, poolclass=NullPool)
    sf = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    return engine, sf


__all__ = (
    "fresh_async_session_factory",
    "sync_dsn",
    "truncate_tables",
)


def _consume_iterable_for_lint(it: Iterable[str]) -> list[str]:
    """Keep the unused-import for ``Iterable`` honest if we add typed
    helpers later; ruff complains otherwise on Python<3.12."""
    return list(it)
