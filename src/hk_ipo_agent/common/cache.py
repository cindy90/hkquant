"""Async cache decorators with Redis primary + in-memory fallback.

Use sparingly — the prediction registry is the canonical store for analysis
outputs. This cache is for expensive idempotent lookups (e.g. embedding
calls, comparable pool snapshots).
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, Protocol, TypeVar

from .logging import get_logger
from .utils import canonical_json

P = ParamSpec("P")
R = TypeVar("R")

_log = get_logger(__name__)


class CacheBackend(Protocol):
    """Minimal async cache interface."""

    async def get(self, key: str) -> Any | None: ...
    async def set(self, key: str, value: Any, ttl_seconds: int) -> None: ...
    async def clear(self) -> None: ...


class _MemoryCache:
    """Trivial in-memory cache with TTL. Process-local, async-safe via lock."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at < asyncio.get_running_loop().time():
                self._store.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        async with self._lock:
            expires_at = asyncio.get_running_loop().time() + ttl_seconds
            self._store[key] = (expires_at, value)

    async def clear(self) -> None:
        async with self._lock:
            self._store.clear()


class _RedisCache:
    """Redis-backed cache. Implementation lives in Phase 2 once the data layer
    is wired up; this skeleton fixes the interface so dependents can mock it.

    Use construction:
        from redis.asyncio import Redis
        backend = _RedisCache(Redis.from_url(settings.redis.url))
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    async def get(self, key: str) -> Any | None:
        raw = await self._client.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (TypeError, json.JSONDecodeError):  # pragma: no cover — Phase 2 work
            return raw

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        payload = json.dumps(value, default=str)
        await self._client.set(key, payload, ex=ttl_seconds)

    async def clear(self) -> None:  # pragma: no cover — Phase 2 work
        # Redis FLUSHDB is destructive; this is only used in tests.
        await self._client.flushdb()


_default_memory_cache = _MemoryCache()


def _make_key(prefix: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    """Stable cache key from positional + keyword arguments."""
    payload = canonical_json({"args": list(args), "kwargs": kwargs})
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return f"{prefix}:{digest}"


def cached(
    *,
    prefix: str,
    ttl_seconds: int = 300,
    cache: _MemoryCache | None = None,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Decorator that caches async function results in process memory.

    Args:
        prefix:       cache key prefix (use the function's qualified name).
        ttl_seconds:  expiration window.
        cache:        optional alternate cache (mainly for tests).

    Example:
        @cached(prefix="ifind.financials", ttl_seconds=3600)
        async def fetch_financials(ticker: str, year: int) -> dict:
            ...
    """
    backend = cache or _default_memory_cache

    def decorator(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            key = _make_key(prefix, args, kwargs)
            hit = await backend.get(key)
            if hit is not None:
                _log.debug("cache_hit", key=key, fn=fn.__qualname__)
                return hit  # type: ignore[no-any-return]
            value = await fn(*args, **kwargs)
            await backend.set(key, value, ttl_seconds)
            _log.debug("cache_miss_stored", key=key, fn=fn.__qualname__)
            return value

        return wrapper

    return decorator


async def reset_default_cache() -> None:
    """Clear the process-default cache. Used by tests."""
    await _default_memory_cache.clear()
