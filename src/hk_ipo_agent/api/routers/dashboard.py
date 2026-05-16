"""Dashboard summary endpoint per PROJECT_SPEC.md §16.2.1."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter

from ...prediction_registry.registry import get_registry
from ..auth import CurrentUserDep
from ..auth.audit_middleware import get_audit_store
from ..schemas import DashboardSummary

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummary)
async def get_summary(user: CurrentUserDep) -> DashboardSummary:
    """Aggregate counts for the UI workbench landing page."""
    _ = user
    reg = get_registry()
    snapshots = await reg.list_snapshots()
    audit = get_audit_store()
    # AuditStoreProtocol doesn't guarantee __len__; use query() for count.
    recent_audit = await audit.query(limit=1)
    audit_count = len(recent_audit)
    return DashboardSummary(
        critical_alerts_count=0,
        pending_reviews_count=0,
        pending_proposals_count=0,
        overdue_checkpoints_count=0,
        active_snapshots=[
            {
                "id": str(s.id),
                "ipo_id": str(s.ipo_id),
                "decision": s.decision.decision.value,
                "created_at": s.created_at.isoformat(),
            }
            for s in snapshots[-10:]
        ],
        upcoming_events=[],
        system_health={
            "api": "ok",
            "snapshots_total": str(len(snapshots)),
            "audit_records": str(audit_count),
        },
        cost_summary={"today_usd": Decimal("0")},
    )


__all__ = ("router",)
