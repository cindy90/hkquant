"""Shared fixtures for integration tests.

Phase 10c adds the ``_ensure_etl_data`` session fixture (mirrors
``tests/e2e/conftest.py``): unit tests with TRUNCATE CASCADE leak into
later integration runs, so we re-seed via subprocess if ipo_events is
empty when integration runs start.
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
    """Returns the minimum of (ipo_events, ipo_pricings, ipo_postmarket) so
    a partial wipe (e.g. e2e tests that insert 1 ipo_events row but leave
    pricings/postmarket empty) is also caught."""
    import psycopg

    from hk_ipo_agent.common.settings import get_settings

    url = get_settings().database.url
    dsn = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    try:
        with psycopg.connect(dsn, connect_timeout=2) as conn, conn.cursor() as cur:
            counts: list[int] = []
            for tbl in ("ipo_events", "ipo_pricings", "ipo_postmarket"):
                cur.execute(f"SELECT count(*) FROM {tbl}")
                counts.append(int(cur.fetchone()[0]))
            return min(counts)
    except Exception:
        return 0


@pytest.fixture(scope="function", autouse=True)
def _ensure_etl_data() -> Iterator[None]:
    """Re-run ETL if ipo_events is empty before each test.

    Function scope (not module) so a prior test that TRUNCATEd between
    tests inside the same module doesn't leave the next test on empty.
    Cheap check; ETL only re-runs when truly needed.
    """
    if not _pg_available():
        yield
        return
    if _ipo_event_count() == 0:
        print(
            "[integration/conftest] ipo_events empty; re-running NACS ETL",
            file=sys.stderr,
        )
        result = subprocess.run(
            [sys.executable, "scripts/migrate_sqlite_to_pg.py", "--no-backup"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode != 0:
            print(
                f"[integration/conftest] ETL failed: {result.stderr[-500:]}",
                file=sys.stderr,
            )
    yield
