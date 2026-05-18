# WebSocket Protocol — chat

> Bidirectional channel used for the chat assistant. SSE handles
> server → client push; WebSocket handles the chat turn-taking loop
> with LLM streaming reply.

---

## 1. Endpoint

```
WS /api/ws/chat/{session_id}
```

`session_id` is a UUID returned by `POST /api/chat/sessions`. The
server validates that the session belongs to the authenticated user
before upgrading; mismatched user/session pairs are closed with
`WS_1008_POLICY_VIOLATION` immediately.

Defined in `src/hk_ipo_agent/api/websocket/chat_endpoint.py`.

---

## 2. Auth

WebSocket headers are unreliable across reverse proxies, so auth goes
through the query string:

```
ws://localhost:8000/api/ws/chat/{session_id}?token=<jwt>
```

The server:

1. Decodes the JWT (`AuthError` → close `1008`).
2. Resolves the user via `resolve_user_async()` — PG first, in-memory
   dev fallback (R6-4). Closes `1008` if not found.
3. Loads the chat session via `get_chat_store().get_session(session_id)`.
   Closes `1008` if `session.user_id != jwt.sub`.

Required permission to create the upgrading session in the first
place: `CHAT_WITH_AGENT` (see [RBAC.md](RBAC.md)). The WebSocket
itself doesn't re-check fine-grained permissions per frame — the
session-owner check is the gate.

---

## 3. Frame shape

### Inbound (client → server)

```json
{ "content": "What's the risk profile of this IPO?" }
```

The handler also tolerates raw text fallback (`raw.strip()` if JSON
parsing fails) so simple clients work, but JSON is the contract.

### Outbound (server → client)

```json
{
  "role": "assistant",
  "content": "Based on the prospectus and base-rate signals...",
  "sequence": 7,
  "message_id": "uuid"
}
```

Fields:

- `role` — currently always `"assistant"` for outbound frames.
- `content` — full assistant reply (Phase 7 MVP is non-streamed; reply
  arrives as one frame after the agent finishes).
- `sequence` — monotonic per session, starts at 0.
- `message_id` — UUID of the persisted `chat_messages` row.

---

## 4. Persistence

**CLAUDE.md §UI 集成约束** hard rule:
**every chat message must persist to `chat_messages`.**

- Phase 7 MVP used in-memory `ChatStore`.
- Phase 7.5b-3 wired `PGChatStore` in `api/main.py` lifespan
  (`set_chat_store(PGChatStore())`).
- Both backends implement the `ChatStoreProtocol`, so the endpoint
  code is unchanged across modes.
- `chat_messages.sequence` is monotonic per session and is the order
  key the UI uses to render history (sort ASC).
- `chat_sessions.last_active_at` is bumped on every append.
- `ON DELETE CASCADE` from `chat_sessions` to `chat_messages` —
  deleting a session wipes its messages.

---

## 5. Lifecycle

```
client          server
  |  WS upgrade  |
  |─────────────▶|
  |              |  decode JWT, resolve user, load session
  |  101 / 1008  |
  |◀─────────────|
  |              |
  |  send_text   |
  |─────────────▶|  append_message(role=USER)
  |              |  history = list_messages()
  |              |  reply_text = await chat_handler.reply(llm, history, content)
  |              |  append_message(role=ASSISTANT)
  |  send_json   |
  |◀─────────────|  {role, content, sequence, message_id}
  |              |
  |    ...       |
  |  close       |
  |─────────────▶|  WebSocketDisconnect → return cleanly
```

Empty messages are skipped silently — server stays connected.

---

## 6. Error handling

The endpoint catches `WebSocketDisconnect` and returns cleanly. Other
exceptions inside the loop currently propagate and close the socket
with an abnormal code (`1011`). The UI should reconnect by:

1. Re-fetching message history via `GET /api/chat/sessions/{id}/messages`
   (REST, gives the persisted view from PG).
2. Re-opening the WebSocket with the same `session_id`.

There is no replay — the WS itself is stateless. The persisted
history is the source of truth.

---

## 7. Future (Phase 9+)

- **Streamed assistant tokens** — emit per-token frames with a
  `partial: true` flag; finalize with `partial: false` + `message_id`.
- **Tool-call frames** — when chat triggers MCP tool calls, emit
  intermediate `role: "tool"` frames so the UI can show progress.
- **Cookie-based auth** — current Phase 7 uses query-string tokens;
  Phase 9 may switch to short-lived cookie + per-message refresh.

---

## 8. See also

- `websocket/chat_endpoint.py` — handler
- `websocket/manager.py` — `ChatStore` (in-memory) + `PGChatStore` + Protocol
- `websocket/chat_handler.py` — LLM invocation
- [SSE_PROTOCOL.md](SSE_PROTOCOL.md) — one-way sibling for push events
- PROJECT_SPEC.md §16.4 — original spec
