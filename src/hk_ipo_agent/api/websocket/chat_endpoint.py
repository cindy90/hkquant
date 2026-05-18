"""WebSocket endpoint: WS /api/ws/chat/{session_id} per PROJECT_SPEC.md §16.4.

Phase 7 MVP: auth via query-string ``token`` (WebSocket headers are
proxy-unfriendly). Phase 9 may add cookie-based auth.
"""

from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from ...common.enums import ChatMessageRole
from ..auth.dependencies import resolve_user_async
from ..auth.jwt import AuthError, decode_access_token
from ..auth.rbac import roles_from_strings
from .chat_handler import reply
from .manager import get_chat_store

router = APIRouter(prefix="/api/ws", tags=["websocket"])


@router.websocket("/chat/{session_id}")
async def chat_socket(
    websocket: WebSocket,
    session_id: UUID,
    token: str = Query(...),
) -> None:
    """Bidirectional chat over WebSocket.

    Inbound frames: ``{"content": str}``
    Outbound frames: ``{"role": "assistant", "content": str, "sequence": int}``
    """
    # Auth
    try:
        payload = decode_access_token(token)
        user_id = UUID(payload["sub"])
    except (AuthError, KeyError, ValueError):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # R6-4: PG-first user lookup with in-memory dev fallback. Production
    # users (provisioned in ``user_accounts`` via lifespan) used to be
    # locked out of WS because chat_endpoint only checked the in-memory
    # seed; now we resolve via PG with graceful fallback.
    user_rec = await resolve_user_async(user_id)
    if user_rec is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    _ = roles_from_strings(payload.get("roles", []))  # available for tool gating

    store = get_chat_store()
    session = await store.get_session(session_id)
    if session is None or session.user_id != user_id:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    llm_client = websocket.app.state.llm_client

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                payload = json.loads(raw)
                content = str(payload.get("content", "")).strip()
            except (json.JSONDecodeError, AttributeError):
                content = raw.strip()
            if not content:
                continue
            user_msg = await store.append_message(
                session_id=session_id,
                role=ChatMessageRole.USER,
                content=content,
            )
            history = await store.list_messages(session_id)
            reply_text = await reply(
                llm_client, history=history[:-1], user_message=user_msg.content
            )
            assistant_msg = await store.append_message(
                session_id=session_id,
                role=ChatMessageRole.ASSISTANT,
                content=reply_text,
            )
            await websocket.send_json(
                {
                    "role": "assistant",
                    "content": assistant_msg.content,
                    "sequence": assistant_msg.sequence,
                    "message_id": str(assistant_msg.id),
                }
            )
    except WebSocketDisconnect:
        return


__all__ = ("router",)
