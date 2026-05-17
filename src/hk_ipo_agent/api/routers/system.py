"""system router — stub endpoints for the UI System Health page.

Real implementations will be filled in when their backing data layers
are ready (Phase 9+). For now the stubs return sensible defaults so the
UI doesn't get 404/500 errors.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query

from ..auth import CurrentUserDep

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/schedulers")
async def list_schedulers(user: CurrentUserDep) -> list[dict[str, Any]]:
    """Return scheduler status. Stub: returns empty list."""
    _ = user
    return []


@router.get("/data-sources")
async def list_data_sources(user: CurrentUserDep) -> list[dict[str, Any]]:
    """Return data source health. Stub: returns static entries."""
    _ = user
    now = datetime.now(UTC).isoformat()
    return [
        {
            "name": "iFind",
            "status": "unknown",
            "last_check_at": now,
            "latency_ms": None,
        },
        {
            "name": "HKEX",
            "status": "unknown",
            "last_check_at": now,
            "latency_ms": None,
        },
    ]


@router.get("/costs")
async def get_costs(
    user: CurrentUserDep,
    period: str = Query("monthly", description="Aggregation period"),
) -> dict[str, Any]:
    """Return cost tracking summary. Stub: returns zeros."""
    _ = user
    return {
        "today_usd": "0",
        "month_usd": "0",
        "monthly_budget_usd": "100",
        "budget_used_pct": "0",
        "period": period,
    }


__all__ = ("router",)
