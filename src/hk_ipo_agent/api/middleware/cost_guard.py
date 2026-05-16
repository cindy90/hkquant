"""LLM cost-guard middleware per PROJECT_SPEC.md §16.

Halts requests that would push daily LLM cost over the budget. Hooks into
``LLMClient.cost_log`` — Phase 7 MVP uses the singleton; Phase 7.5 may
move budget enforcement to a Redis-backed counter for multi-worker.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request, Response, status
from starlette.middleware.base import BaseHTTPMiddleware

from ...common.settings import get_settings

# Paths that don't trigger LLM calls — skip the check.
_CHEAP_PATHS = (
    "/health",
    "/ready",
    "/openapi.json",
    "/docs",
    "/api/dashboard",
    "/api/audit",
    "/api/snapshots",
    "/api/ipos",
)


class CostGuardMiddleware(BaseHTTPMiddleware):
    """If today's cumulative LLM spend has hit the budget, reject 503."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        if any(path.startswith(p) for p in _CHEAP_PATHS):
            return await call_next(request)

        # The shared LLMClient lives in app.state if installed by main.py.
        client = getattr(request.app.state, "llm_client", None)
        if client is None:
            return await call_next(request)

        settings = get_settings().llm
        used = float(client.cost_log.total_usd())
        if used >= float(settings.cost_daily_budget_usd):
            return Response(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content=b'{"detail":"daily LLM cost budget exhausted"}',
                media_type="application/json",
            )
        return await call_next(request)


__all__ = ("CostGuardMiddleware",)
