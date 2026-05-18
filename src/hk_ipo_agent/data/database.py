"""Async SQLAlchemy engine + session factory.

Single place to build the project's PostgreSQL connection. Imports settings
lazily so test fixtures can override via env vars / monkeypatch before the
engine is materialized.

Phase 2 repositories will depend on this module exclusively — do not create
engines elsewhere in the code base.
"""

from __future__ import annotations

import functools
from collections.abc import AsyncIterator
from contextvars import ContextVar

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ..common.settings import get_settings


@functools.lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """Return the singleton async engine. Lazy + cached."""
    settings = get_settings()
    return create_async_engine(
        settings.database.url,
        pool_size=settings.database.pool_size,
        pool_pre_ping=True,
        echo=False,
        future=True,
    )


# R7-10: per-context session factory storage. Pre-R7-10 the factory was
# ``@functools.lru_cache(maxsize=1)``-wrapped, which globally bound the
# first call's engine + its event loop into the cached factory. Any
# subsequent caller from a different event loop (pytest-asyncio
# per-test loops, concurrent backtest workers) inherited the dead loop's
# asyncpg pool, manifesting as ``RuntimeError: <Future> attached to a
# different loop``. The ContextVar keys storage to the current asyncio
# context so each event loop gets its own factory.
_SESSION_FACTORY: ContextVar[async_sessionmaker[AsyncSession] | None] = ContextVar(
    "_SESSION_FACTORY", default=None
)


def async_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the per-context async sessionmaker bound to the project engine.

    Singleton-within-context: repeated calls inside the same asyncio
    context return the same factory; a fresh context (new event loop /
    test fixture / subprocess) constructs its own.
    """
    factory = _SESSION_FACTORY.get()
    if factory is not None:
        return factory
    factory = async_sessionmaker(
        bind=get_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    _SESSION_FACTORY.set(factory)
    return factory


def _clear_session_factory_cache() -> None:
    """R7-10 back-compat shim: tests that used to call
    ``async_session_factory.cache_clear()`` continue to work — this
    resets the ContextVar so the next call rebuilds the factory.
    """
    _SESSION_FACTORY.set(None)


# Expose as ``async_session_factory.cache_clear`` for callers that haven't
# migrated. They keep working; new code should call ``dispose_engine()``
# or ``_clear_session_factory_cache()`` directly.
async_session_factory.cache_clear = _clear_session_factory_cache  # type: ignore[attr-defined]


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI / generic dependency-injection helper yielding one `AsyncSession`.

    Usage in FastAPI endpoint (Phase 7+):

        @router.get("/ipos/{ipo_id}")
        async def get_ipo(
            ipo_id: UUID,
            session: AsyncSession = Depends(get_session),
        ) -> ...:
            ...
    """
    factory = async_session_factory()
    async with factory() as session:
        try:
            yield session
        finally:
            await session.close()


async def dispose_engine() -> None:
    """Dispose the global engine. Call from FastAPI shutdown / test teardown.

    R7-10: also resets the ContextVar-backed session factory so the next
    call rebuilds from scratch. ``get_engine`` still uses ``lru_cache`` and
    its ``cache_clear()`` works as before.
    """
    if get_engine.cache_info().currsize:  # type: ignore[attr-defined]
        await get_engine().dispose()
        get_engine.cache_clear()  # type: ignore[attr-defined]
    # R7-10: clear the per-context factory storage (no-op if never set).
    _SESSION_FACTORY.set(None)


__all__ = (
    "async_session_factory",
    "dispose_engine",
    "get_engine",
    "get_session",
)
