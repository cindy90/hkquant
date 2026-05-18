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

# R6-5: paths that don't trigger LLM calls — skip the cost-guard check.
# Pre-R6-5 these were matched with ``path.startswith(p)`` which produced
# false positives like ``/api/dashboard-llm-tool`` getting through because
# its prefix happens to start with ``/api/dashboard``. We now require a
# true segment boundary: the path EQUALS an entry OR the entry is a
# prefix followed by ``/``.
_CHEAP_PATHS: tuple[str, ...] = (
    "/health",
    "/ready",
    "/openapi.json",
    "/docs",
    "/api/dashboard",
    "/api/audit",
    "/api/snapshots",
    "/api/ipos",
    "/api/alerts",
    "/api/prospectus",
)


def _is_cheap_path(path: str) -> bool:
    """R6-5 — segment-boundary path match.

    True iff ``path`` equals one of ``_CHEAP_PATHS`` exactly, OR is a
    sub-route (i.e. the prefix is followed by ``/``). This eliminates
    the prefix-spoofing class of bypass without forcing operators to
    enumerate every concrete sub-path.
    """
    return any(path == p or path.startswith(p + "/") for p in _CHEAP_PATHS)


class CostGuardMiddleware(BaseHTTPMiddleware):
    """If today's cumulative LLM spend has hit the budget, reject 503."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        if _is_cheap_path(path):
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


__all__ = ("CostGuardMiddleware", "_is_cheap_path")
