"""Chat session management endpoints per PROJECT_SPEC.md §16.4.

WebSocket message exchange lives at ``/api/ws/chat/{session_id}`` (see
``websocket/chat_endpoint.py``). This router handles session CRUD only.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from ...common.enums import Permission
from ..auth.dependencies import CurrentUser, require_permission
from ..schemas import ChatSessionCreate
from ..websocket import get_chat_store

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(
    payload: ChatSessionCreate,
    user: Annotated[CurrentUser, Depends(require_permission(Permission.CHAT_WITH_AGENT))],
) -> dict[str, Any]:
    store = get_chat_store()
    session = await store.create_session(
        user_id=user.id,
        title=payload.title,
        snapshot_id=payload.snapshot_id,
        ipo_id=payload.ipo_id,
    )
    return {
        "id": str(session.id),
        "title": session.title,
        "created_at": session.created_at.isoformat(),
        "websocket_path": f"/api/ws/chat/{session.id}",
    }


@router.get("/sessions")
async def list_sessions(
    user: Annotated[CurrentUser, Depends(require_permission(Permission.CHAT_WITH_AGENT))],
) -> dict[str, Any]:
    sessions = await get_chat_store().list_sessions_for_user(user.id)
    return {
        "data": [
            {
                "id": str(s.id),
                "title": s.title,
                "last_active_at": s.last_active_at.isoformat(),
            }
            for s in sessions
        ]
    }


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(
    session_id: UUID,
    user: Annotated[CurrentUser, Depends(require_permission(Permission.CHAT_WITH_AGENT))],
) -> dict[str, Any]:
    store = get_chat_store()
    session = await store.get_session(session_id)
    if session is None or session.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"chat session {session_id} not found",
        )
    messages = await store.list_messages(session_id)
    return {
        "session_id": str(session_id),
        "messages": [m.model_dump(mode="json") for m in messages],
    }


__all__ = ("router",)
