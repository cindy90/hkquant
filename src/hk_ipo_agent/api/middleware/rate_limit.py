"""Rate-limit middleware per PROJECT_SPEC.md §16 + CLAUDE.md v1.2.1.

Phase 7 MVP: in-process sliding window per (user/IP, route). Phase 7.5 /
Phase 8 may swap to Redis-backed limiter for multi-worker correctness.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable

from fastapi import Request, Response, status
from starlette.middleware.base import BaseHTTPMiddleware

from ...common.settings import get_settings


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP rolling 60-second window. Limit from ``Settings.api.rate_limit_per_min``."""

    def __init__(self, app) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()
        self._window = 60.0

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        limit = get_settings().api.rate_limit_per_min
        if limit <= 0:
            return await call_next(request)

        # Identify by current user id if available, else client IP.
        ident = getattr(request.state, "current_user", None)
        key = (
            str(ident.id) if ident else (request.client.host if request.client else "anon")
        )

        now = time.monotonic()
        async with self._lock:
            bucket = self._buckets[key]
            while bucket and bucket[0] < now - self._window:
                bucket.popleft()
            if len(bucket) >= limit:
                return Response(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content=b'{"detail":"rate limit exceeded"}',
                    media_type="application/json",
                )
            bucket.append(now)

        return await call_next(request)


__all__ = ("RateLimitMiddleware",)
