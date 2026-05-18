"""SSE endpoint: GET /api/stream/events per PROJECT_SPEC.md §16.3.

Requires authentication. Streams ``RealtimeEvent``s as ``text/event-stream``.

EventSource does not support custom headers, so this endpoint also
accepts a ``token`` query parameter as an alternative to the standard
``Authorization: Bearer <token>`` header.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, status
from starlette.responses import StreamingResponse

from ..auth.jwt import AuthError, decode_access_token
from .connection_manager import stream_events

router = APIRouter(prefix="/api/stream", tags=["streaming"])


@router.get("/events")
async def get_events_stream(
    token: str | None = Query(None, description="JWT token (for EventSource which cannot set headers)"),
    authorization: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    """Open a server-sent-events stream of system events.

    Authenticated via either ``Authorization: Bearer`` header or ``?token=``
    query parameter (necessary because EventSource API does not support
    custom headers).
    """
    # Extract JWT from header or query param
    jwt_token: str | None = None
    if authorization and authorization.lower().startswith("bearer "):
        jwt_token = authorization.split(None, 1)[1].strip()
    elif token:
        jwt_token = token

    if not jwt_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing auth token (provide Authorization header or ?token= query param)",
        )

    # Verify token directly (bypass get_current_user DI wrapper)
    try:
        decode_access_token(jwt_token)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc

    return StreamingResponse(
        stream_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering
        },
    )


__all__ = ("router",)
