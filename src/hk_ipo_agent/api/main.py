"""FastAPI application entry per PROJECT_SPEC.md §16.

Composes middleware + routers + WebSocket + SSE + custom OpenAPI. Run via:

    uvicorn hk_ipo_agent.api.main:app --reload

The shared ``LLMClient`` is stashed on ``app.state.llm_client`` so the
cost-guard middleware and chat handler can find it without re-constructing.
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI

from ..common.llm_client import LLMClient
from ..common.logging import get_logger
from ..common.settings import get_settings
from ..data.database import async_session_factory
from ..prediction_registry.registry import PGPredictionRegistry, set_registry
from .auth.audit_middleware import (
    AuditMiddleware,
    PGAuditStore,
    set_audit_store,
)
from .middleware.cors import install_cors
from .middleware.cost_guard import CostGuardMiddleware
from .middleware.error_handler import register_error_handlers
from .middleware.rate_limit import RateLimitMiddleware
from .middleware.request_id import RequestIdMiddleware
from .openapi import install_openapi
from .routers import ALL_ROUTERS
from .streaming import sse_router
from .streaming.event_bus import EventBus, set_event_bus
from .websocket import ws_router

# R7-12 (a): force stdout/stderr to UTF-8 BEFORE the first log call so
# structlog can print stack traces that contain CJK file paths (e.g.
# ``D:\自定义工具\港股数据分析``) without crashing on Windows' default
# gbk codec. Without this, any ``logger.exception()`` in the background
# task except block raises ``UnicodeEncodeError`` and *masks* the real
# underlying exception — which is how a missing ``KIMI_API_KEY`` looked
# like a silent pipeline hang during regression testing.
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and getattr(_stream, "encoding", "").lower() != "utf-8":
        with contextlib.suppress(AttributeError, OSError):
            _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

# R7-12 (b): explicitly load the repo-root .env into os.environ BEFORE
# any code reads ``KIMI_API_KEY``. Settings uses ``env_prefix="HK_IPO__"``
# so a plain ``KIMI_API_KEY=...`` line is invisible to pydantic-settings
# alone; analyze_pdf.py (the CLI entry) already does this load_dotenv
# call, but uvicorn does not — which is why background pipelines died
# with "KIMI_API_KEY not configured" even though /health was 200.
_REPO_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_REPO_ROOT / ".env", override=False)

logger = get_logger(__name__)


async def _upsert_seed_accounts_into_pg() -> None:
    """R6-7: mirror the in-memory seed users into ``user_accounts`` so the
    whatif FK is satisfied at startup. Best-effort — silently skips on
    PG unavailability (matches dev / test environments).

    R6-7b: BEFORE inserting, reconcile each in-memory record's ``id`` with
    the existing ``user_accounts`` row for the same email. ``_seed_defaults``
    generates a fresh ``uuid4()`` on every process start, so without
    reconciliation the JWT it signs carries an ``id`` that drifts from the
    persisted ``user_accounts.id`` — every write-audited request then
    blows up on the ``audit_logs.user_id`` FK (the symptom that surfaced
    when the UI's first prospectus upload returned 500 IntegrityError
    even though the upload endpoint itself doesn't touch the DB).
    """
    from sqlalchemy import select

    from ..data.database import async_session_factory
    from ..data.models import UserAccountRow
    from .auth.dependencies import _USERS, CurrentUser, upsert_user_account_for_jwt

    try:
        sf = async_session_factory()
        async with sf() as s:
            emails = [rec.email for rec in _USERS.values()]
            existing = (
                await s.execute(
                    select(UserAccountRow.id, UserAccountRow.email).where(
                        UserAccountRow.email.in_(emails)
                    )
                )
            ).all()
            db_id_by_email = {row.email: row.id for row in existing}
    except Exception:
        # PG unreachable in dev: skip reconciliation; upsert call below
        # will also no-op, and audit_logs INSERT can still NULL out the
        # FK if needed.
        db_id_by_email = {}

    for rec in list(_USERS.values()):
        db_id = db_id_by_email.get(rec.email)
        if db_id is not None and db_id != rec.id:
            rec.id = db_id  # align in-memory → DB so JWT.sub matches FK target
        await upsert_user_account_for_jwt(
            CurrentUser(id=rec.id, email=rec.email, roles=list(rec.roles))
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize shared resources at startup, dispose at shutdown.

    Wires the PG-backed prediction registry so /api/ipos reads persisted
    snapshots instead of the in-memory dev fallback.

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
    # PG-backed registry first — every downstream init may need to read it.
    set_registry(PGPredictionRegistry())

    # Wire PG-backed EventBus + AuditStore so SSE history persists into
    # ``realtime_events`` and write-side audit lands in ``audit_logs``.
    # Pre-fix: lifespan only set the registry, leaving event_bus and audit
    # store as their in-memory defaults. Background tasks (e.g.
    # upload_prospectus → _run_pipeline_background) would publish
    # SCHEDULER_FAILED events that vanished without a trace if no SSE
    # client happened to be subscribed at that moment — the symptom that
    # surfaced when the UI showed "analyzing…" forever for 越疆 2432.HK.
    sf = async_session_factory()
    set_event_bus(EventBus(session_factory=sf))
    set_audit_store(PGAuditStore(session_factory=sf))
    logger.info("api_lifespan_persistence_wired")

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
