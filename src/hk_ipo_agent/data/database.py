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


@functools.lru_cache(maxsize=1)
def async_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the singleton async sessionmaker bound to the project engine."""
    return async_sessionmaker(
        bind=get_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


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
    """Dispose the global engine. Call from FastAPI shutdown / test teardown."""
    if get_engine.cache_info().currsize:  # type: ignore[attr-defined]
        await get_engine().dispose()
        get_engine.cache_clear()  # type: ignore[attr-defined]
        async_session_factory.cache_clear()  # type: ignore[attr-defined]


__all__ = (
    "async_session_factory",
    "dispose_engine",
    "get_engine",
    "get_session",
)
