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
from ..common.settings import get_settings
from ..prediction_registry.registry import PGPredictionRegistry, set_registry
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


async def _upsert_seed_accounts_into_pg() -> None:
    """R6-7: mirror the in-memory seed users into ``user_accounts`` so the
    whatif FK is satisfied at startup. Best-effort — silently skips on
    PG unavailability (matches dev / test environments).
    """
    from .auth.dependencies import _USERS, CurrentUser, upsert_user_account_for_jwt

    for rec in list(_USERS.values()):
        await upsert_user_account_for_jwt(
            CurrentUser(id=rec.id, email=rec.email, roles=list(rec.roles))
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize shared resources at startup, dispose at shutdown.

    Production wiring (local commit ae991c9): installs the PG-backed
    prediction registry so /api/ipos / /api/snapshots read persisted
    snapshots. Tests swap back to in-memory via ``reset_registry()`` in
    conftest. This is compatible with R5-4 (which only removed ``set_registry``
    from the pipeline layer, not from the API lifespan).

    R6-6: LLMClient construction failure handling is environment-aware.
      * dev / test: warn + fall back to ``app.state.llm_client = None``;
        cost_guard middleware tolerates None (skips the budget check).
      * production: re-raise the original exception so uvicorn fails
        fast at startup. Silently running with no LLM in production means
        every LLM-backed endpoint 500s mysteriously hours later, which is
        much worse than a loud startup crash an operator can fix.

    R6-7: also mirror seeded in-memory users to ``user_accounts`` so
    Phase 10 attribution can FK-link whatif persistence to a real row.
    """
    # Set PG-backed prediction registry so /api/ipos reads persisted snapshots.
    set_registry(PGPredictionRegistry())

    try:
        app.state.llm_client = LLMClient(daily_budget_usd=Decimal("100"))
    except Exception:
        settings = get_settings()
        if settings.environment.lower() in {"prod", "production"}:
            # Production: never silently degrade. Re-raise so the process
            # exits with a clear stack trace pointing at the missing config.
            raise
        # dev / test: the API key may not be set; cost_guard tolerates None.
        app.state.llm_client = None

    # R6-7: best-effort seed of user_accounts (no-op if PG offline).
    await _upsert_seed_accounts_into_pg()
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
