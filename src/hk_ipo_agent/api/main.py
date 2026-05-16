"""FastAPI application entry per PROJECT_SPEC.md §16.

Composes middleware + routers + WebSocket + SSE + custom OpenAPI. Run via:

    uvicorn hk_ipo_agent.api.main:app --reload

The shared ``LLMClient`` is stashed on ``app.state.llm_client`` so the
cost-guard middleware and chat handler can find it without re-constructing.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal

from fastapi import FastAPI

from ..common.llm_client import LLMClient
from .auth.audit_middleware import AuditMiddleware
from .middleware.cors import install_cors
from .middleware.cost_guard import CostGuardMiddleware
from .middleware.error_handler import register_error_handlers
from .middleware.rate_limit import RateLimitMiddleware
from .middleware.request_id import RequestIdMiddleware
from .openapi import install_openapi
from .routers import ALL_ROUTERS
from .streaming import sse_router
from .websocket import ws_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize shared resources at startup, dispose at shutdown."""
    try:
        app.state.llm_client = LLMClient(daily_budget_usd=Decimal("100"))
    except Exception:
        # In test envs the API key may not be set; fall back to None and
        # the cost_guard middleware will skip the check.
        app.state.llm_client = None
    yield


def create_app() -> FastAPI:
    """Build + configure the FastAPI application."""
    app = FastAPI(
        title="HK IPO Cornerstone Agent API",
        version="0.7.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )

    # Order matters: error handler first (always wraps); cors → request_id
    # → audit → rate_limit → cost_guard.
    register_error_handlers(app)
    install_cors(app)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(AuditMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(CostGuardMiddleware)

    install_openapi(app)

    # Mount all routers.
    for router in ALL_ROUTERS:
        app.include_router(router)
    app.include_router(sse_router)
    app.include_router(ws_router)

    return app


app = create_app()


__all__ = ("app", "create_app", "lifespan")
