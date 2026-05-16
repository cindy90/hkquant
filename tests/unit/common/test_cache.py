"""Tests for `hk_ipo_agent.common.cache`."""

from __future__ import annotations

import asyncio

import pytest

from hk_ipo_agent.common.cache import _MemoryCache, cached, reset_default_cache

pytestmark = pytest.mark.asyncio


async def test_memory_cache_set_get() -> None:
    cache = _MemoryCache()
    await cache.set("k", 42, ttl_seconds=10)
    assert await cache.get("k") == 42


async def test_memory_cache_expiration_returns_none() -> None:
    cache = _MemoryCache()
    await cache.set("k", "v", ttl_seconds=0)
    # ttl=0 means already expired
    await asyncio.sleep(0.01)
    assert await cache.get("k") is None


async def test_cached_decorator_hits_after_miss() -> None:
    await reset_default_cache()
    calls = {"n": 0}

    @cached(prefix="test.add", ttl_seconds=30)
    async def add(a: int, b: int) -> int:
        calls["n"] += 1
        return a + b

    r1 = await add(2, 3)
    r2 = await add(2, 3)
    r3 = await add(2, 4)  # different args -> miss
    assert r1 == 5
    assert r2 == 5
    assert r3 == 6
    assert calls["n"] == 2


async def test_cached_decorator_kwargs_distinguished() -> None:
    await reset_default_cache()
    calls = {"n": 0}

    @cached(prefix="test.kw", ttl_seconds=30)
    async def fn(a: int, b: int = 1) -> int:
        calls["n"] += 1
        return a + b

    await fn(1)
    await fn(1, b=2)
    assert calls["n"] == 2
