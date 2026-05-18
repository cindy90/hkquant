# SSE Protocol

> Server-Sent Events stream for one-way server → client push.
> Used for dashboard live tiles, analysis-run progress, alert pop-ups,
> state-transition broadcasts, and learning-loop notifications.

---

## 1. Endpoint

```
GET /api/stream/events
```

Defined in `src/hk_ipo_agent/api/streaming/sse_endpoint.py`. Returns
`text/event-stream` with `Cache-Control: no-cache` and
`X-Accel-Buffering: no` (nginx disables buffering on long-running streams).

---

## 2. Auth

The `EventSource` browser API cannot set custom headers, so this
endpoint accepts auth via **either**:

```
Authorization: Bearer <jwt>                   # programmatic clients
?token=<jwt>                                  # EventSource / browser
```

The query-string form is the path UI will use. Connection is rejected
with `401` if the token is missing, malformed, or expired. There is no
per-event RBAC — every authenticated user sees the same multiplexed
bus (no PII in payloads; see §5).

---

## 3. Frame format

Each event is emitted as a standard SSE message frame:

```
event: snapshot.created
data: {"event_type":"snapshot.created","related_ipo_id":"...","related_snapshot_id":"...","payload":{...},"created_at":"2026-05-18T03:14:15.926Z"}

```

Two blank lines terminate the frame. `data:` is **always** a single
JSON object matching `RealtimeEvent` (see `common/schemas.py`).

Heartbeats every `HEARTBEAT_SECONDS = 15.0` keep proxies from idle-killing
the socket:

```
:heartbeat

```

(Comment line per the SSE spec — clients ignore it.)

---

## 4. Registered event types

CLAUDE.md §UI 集成约束 makes this a HARD constraint:
**only event types declared in `common/enums.py::RealtimeEventType`
AND `streaming/event_types.py::REGISTERED_EVENTS` may be emitted.**
The `is_registered()` guard is wired into `event_bus.publish()`.

Phase 7.5 / Phase 8 / Phase 10 register the following:

| Channel | `event_type` | Trigger |
|---|---|---|
| Alert | `alert.created` | New row in `alerts` table |
| Alert | `alert.acknowledged` | POST `/api/alerts/{id}/acknowledge` |
| Snapshot | `snapshot.created` | `create_snapshot` node fires (every analysis run) |
| Snapshot | `snapshot.updated` | Phase 7.5 lifecycle transition |
| Outcome | `outcome.recorded` | Checkpoint scheduler writes `outcomes` row |
| Outcome | `checkpoint.completed` | T+N day fully reconciled |
| Lifecycle | `lifecycle.state_transition` | `ipo_state_transitions` insert |
| Scheduler | `scheduler.started` / `scheduler.completed` / `scheduler.failed` | high_freq / daily / event_driven dispatch |
| Learning | `drift.detected` | `drift_detector` CUSUM / PSI fires |
| Learning | `proposal.created` / `proposal.accepted` | adjustment_proposer / applier |
| Learning | `adjustment.applied` | After successful rollback-safe apply |
| System | `dashboard.refresh` | Tick — UI re-fetches `/api/dashboard/summary` |
| System | `datasource.degraded` | iFind / vendor degrade signal |
| System | `cost.threshold_hit` | CostGuard middleware tripped |

Add a new event: append to `RealtimeEventType` enum first, then
`REGISTERED_EVENTS` auto-picks it up (frozen-set over the enum).

---

## 5. Payload contract

```json
{
  "event_type": "snapshot.created",
  "related_ipo_id": "uuid | null",
  "related_snapshot_id": "uuid | null",
  "payload": { /* arbitrary, event-specific */ },
  "created_at": "ISO-8601 timestamp"
}
```

Conventions:

- **`related_ipo_id` + `related_snapshot_id` are the primary join keys**
  — UI uses them to route the event to the right ticker view / live tile.
- **`payload` is event-specific** but follows the same money-as-string
  rule as the REST API (R5-6). Decimal values arrive as strings.
- **No sensitive fields in `payload`** — full audit-redacted shape;
  if you need to broadcast a sensitive field, broadcast a marker and
  let the UI fetch through the RBAC-gated REST endpoint.

---

## 6. Client reconnect

Browsers reconnect `EventSource` automatically on close. Server side
is stateless (no per-connection state aside from the asyncio queue
binding); the next subscriber simply joins the bus.

The bus does **not** replay history — events emitted while the client
was disconnected are lost. UI must reconcile via the REST list endpoints
on reconnect (e.g. re-GET `/api/alerts?status=unacked`).

---

## 7. Testing

- Unit tests: `tests/unit/api/streaming/` cover `format_sse`,
  `is_registered`, and the heartbeat fallback.
- Integration: spin up a TestClient, open the stream, publish via
  `event_bus.publish()`, assert receipt.
- Manual: `curl -N -H "Authorization: Bearer $JWT" http://localhost:8000/api/stream/events`

---

## 8. See also

- `event_types.py` — registry
- `connection_manager.py` — heartbeat + format
- `event_bus.py` — asyncio fan-out
- [WS_PROTOCOL.md](WS_PROTOCOL.md) — bidirectional sibling for chat
- PROJECT_SPEC.md §16.3 — original spec
