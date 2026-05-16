"""Health / readiness endpoints per PROJECT_SPEC.md §16.2. No auth required."""

from __future__ import annotations

import time

from fastapi import APIRouter

from ...common.settings import get_settings
from ..schemas import HealthResponse, ReadyResponse

router = APIRouter(tags=["health"])

_START_TS = time.monotonic()


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        version=settings.orchestrator.system_version,
        environment=settings.environment,
        uptime_seconds=round(time.monotonic() - _START_TS, 3),
    )


@router.get("/ready", response_model=ReadyResponse)
async def ready() -> ReadyResponse:
    """Phase 7 MVP: always ready. Phase 7.5 probes DB / Qdrant / Redis."""
    return ReadyResponse(ready=True, checks={"llm": "ok"})


__all__ = ("router",)
