"""Shared fixtures for prediction_registry unit tests.

The autouse engine cache clearer prevents the cached AsyncEngine
created by ``async_session_factory()`` from binding to a previous
test's event loop — that bug surfaces as "Event loop is closed" during
asyncpg teardown when test files using ``fresh_sf`` and test files using
the module-level cache are interleaved by pytest.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _fresh_async_engine() -> Iterator[None]:
    """Clear the lru_cached AsyncEngine before AND after each test.

    Mirrors the same-named fixture in ``tests/unit/api/`` so the two
    suites compose cleanly when pytest interleaves files.
    """
    from hk_ipo_agent.data.database import async_session_factory, get_engine  # noqa: PLC0415

    get_engine.cache_clear()  # type: ignore[attr-defined]
    async_session_factory.cache_clear()  # type: ignore[attr-defined]
    yield
    get_engine.cache_clear()  # type: ignore[attr-defined]
    async_session_factory.cache_clear()  # type: ignore[attr-defined]
