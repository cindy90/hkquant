"""Shared fixtures for end-to-end tests.

Phase 9b adds the ``_ensure_etl_data`` session fixture: when a unit test
earlier in the run has truncated ``ipo_events`` (e.g. the audit /
backtest router tests use CASCADE), we re-run the NACS → PG ETL so the
e2e tests have data to work against.

This pattern keeps the e2e tests order-independent without paying the
ETL cost on every run — the fixture is session-scoped, so the ETL
fires at most once per pytest invocation, and is skipped entirely when
the data is already present.
"""

from __future__ import annotations

import functools
import subprocess
import sys
from collections.abc import Iterator

import pytest


@functools.lru_cache(maxsize=1)
def _pg_available() -> bool:
    import psycopg

    from hk_ipo_agent.common.settings import get_settings

    url = get_settings().database.url
    dsn = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    try:
        with psycopg.connect(dsn, connect_timeout=2):
            return True
    except Exception:
        return False


def _ipo_event_count() -> int:
    """Sync count of ipo_events rows (psycopg, fast)."""
    import psycopg

    from hk_ipo_agent.common.settings import get_settings

    url = get_settings().database.url
    dsn = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    try:
        with psycopg.connect(dsn, connect_timeout=2) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM ipo_events")
            return int(cur.fetchone()[0])
    except Exception:
        return 0


@pytest.fixture(scope="function", autouse=True)
def _ensure_etl_data() -> Iterator[None]:
    """Re-run ETL if the e2e tests find ipo_events empty.

    Function-scoped: another test in the same module may have TRUNCATEd
    (e.g. test_learning_cycle wipes everything between cases). The
    check is cheap (sync psycopg count); the ETL itself only re-runs
    when truly needed.
    """
    if not _pg_available():
        # PG-required tests will skip themselves; nothing to do.
        yield
        return
    if _ipo_event_count() == 0:
        print(
            "[e2e/conftest] ipo_events empty; re-running NACS ETL "
            "(scripts/migrate_sqlite_to_pg.py)",
            file=sys.stderr,
        )
        # Subprocess to keep test process clean.
        result = subprocess.run(
            [sys.executable, "scripts/migrate_sqlite_to_pg.py", "--no-backup"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode != 0:
            print(
                f"[e2e/conftest] ETL failed: {result.stderr[-500:]}",
                file=sys.stderr,
            )
    yield
