"""SSE endpoint: GET /api/stream/events per PROJECT_SPEC.md §16.3.

Requires authentication. Streams ``RealtimeEvent``s as ``text/event-stream``.
"""

from __future__ import annotations

from fastapi import APIRouter
from starlette.responses import StreamingResponse

from ..auth import CurrentUserDep
from .connection_manager import stream_events

router = APIRouter(prefix="/api/stream", tags=["streaming"])


@router.get("/events")
async def get_events_stream(user: CurrentUserDep) -> StreamingResponse:
    """Open a server-sent-events stream of system events.

    Authenticated. Each connected client gets a private subscription
    to the in-memory ``EventBus``.
    """
    _ = user  # auth enforced; user identity not needed for global feed
    return StreamingResponse(
        stream_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering
        },
    )


__all__ = ("router",)
