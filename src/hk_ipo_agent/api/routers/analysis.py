"""Analysis-trigger endpoints per PROJECT_SPEC.md §16.2.

POST /api/analysis/run starts an asynchronous LangGraph run for one IPO.
Phase 7 MVP returns ``run_id`` immediately + emits an
``ANALYSIS_STARTED`` SSE event. Phase 7.5 wires the background task to
Celery or a worker queue; Phase 7 MVP runs inline within FastAPI's task
queue (``BackgroundTasks``) which is fine for low-throughput dev.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from ...common.enums import Permission, RealtimeEventType
from ..auth.dependencies import CurrentUser, require_permission
from ..schemas import AnalysisRequest, AnalysisStartResponse
from ..streaming.event_bus import get_event_bus

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


@router.post(
    "/run",
    response_model=AnalysisStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_analysis(
    payload: AnalysisRequest,
    background: BackgroundTasks,
    user: Annotated[CurrentUser, Depends(require_permission(Permission.TRIGGER_ANALYSIS))],
) -> AnalysisStartResponse:
    """Kick off a full LangGraph analysis. Returns immediately.

    Phase 7 MVP: the actual run happens out-of-band; this endpoint is the
    UI's "start" signal. Wire-up to the real Phase 6 graph happens via
    background task — for MVP it just emits an SSE event.
    """
    if not payload.ipo_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ipo_id is required",
        )
    _ = user
    run_id = uuid4()
    background.add_task(_emit_started, payload.ipo_id, str(run_id))
    return AnalysisStartResponse(
        ipo_id=payload.ipo_id,
        run_id=run_id,
        accepted_at=datetime.now(UTC),
    )


async def _emit_started(ipo_id: str, run_id: str) -> None:
    """Background helper: publish ANALYSIS_STARTED SSE event."""
    bus = get_event_bus()
    await bus.publish(
        RealtimeEventType.SCHEDULER_STARTED,
        payload={"ipo_id": ipo_id, "run_id": run_id, "kind": "analysis"},
    )


__all__ = ("router",)
