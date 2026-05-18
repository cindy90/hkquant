# API Reference

> **Source of truth**: the live OpenAPI 3.1 spec at `GET /openapi.json`.
> CI runs `openapi-typescript` against the UI repo to generate
> `apps/web/src/types/openapi.ts`; a diff means the API changed and
> needs UI follow-up.
>
> This document is the human-readable summary. For exact field shapes,
> defer to OpenAPI.

---

## 1. Base URL + auth

| Environment | Base URL | Auth |
|---|---|---|
| dev | `http://localhost:8000` | Local JWT via `POST /api/auth/login` |
| prod | `https://api.hkipo.example.com` | Local JWT (Phase 9: optional SSO via Okta/Azure AD) |

All endpoints (except `/health` / `/ready` / `/metrics` / `/api/auth/login`) require:

```
Authorization: Bearer <jwt>
```

JWT claims: `sub` (user_id), `email`, `roles[]`, `exp`. Issued by
`POST /api/auth/login`; default TTL 60 minutes.

---

## 2. Router map (10 production routers, 31 endpoints)

| Router | Prefix | Endpoints | Required perm |
|---|---|---|---|
| auth | `/api/auth` | POST `/login`, GET `/me` | (login is public; /me requires bearer) |
| dashboard | `/api/dashboard` | GET `/summary` | `READ_DASHBOARD` |
| ipos | `/api/ipos` | GET `/`, `/{id}`, `/{id}/snapshots`, `/{id}/lifecycle` | `READ_IPO` |
| snapshots | `/api/snapshots` | GET `/`, `/{id}`, `/{id}/memo.{md,pdf,docx}`, `/{id}/outcomes` | `READ_SNAPSHOTS` |
| alerts | `/api/alerts` | GET `/`, POST `/{id}/acknowledge` | `READ_ALERT` / `ACK_ALERT` |
| prospectus | `/api/prospectus` | GET `/{id}.pdf` | `READ_PROSPECTUS` |
| audit | `/api/audit` | GET `/logs` | `READ_AUDIT` (sensitive fields nulled unless `READ_AUDIT_FULL`) |
| chat | `/api/chat` | POST `/sessions`, GET `/sessions`, GET `/sessions/{id}/messages` | `CHAT_WITH_AGENT` |
| whatif | `/api/whatif` | POST `/run` | `RUN_WHATIF` |
| analysis | `/api/analysis` | POST `/trigger`, GET `/runs`, `/runs/{id}` | `TRIGGER_ANALYSIS` |
| outcomes | `/api/outcomes` | GET `/recent` | `READ_SNAPSHOTS` |

Plus `health`, `system`, `settings`, `reviews`, `proposals`, `drift`,
`backtest` (Phase 7.5b additions).

---

## 3. Error format (RFC 7807 Problem Details)

Every 4xx / 5xx response follows:

```json
{
  "type": "about:blank",
  "title": "Pydantic Validation Error",
  "status": 422,
  "detail": "1 validation error for PredictionSnapshot\nvaluation_output.company_id\n  Field required ...",
  "request_id": "req_abc123"
}
```

CLAUDE.md §UI 集成约束 forbids the ad-hoc `{"error": "..."}` shape.

---

## 4. Pagination

Every list endpoint returns:

```json
{
  "data": [...],
  "meta": {
    "total": 374,
    "limit": 50,
    "offset": 0,
    "has_next": true
  }
}
```

Defaults: `limit=50`, `offset=0`, `limit ∈ [1, 500]`.

---

## 5. Money / Decimal fields are STRING in JSON

CLAUDE.md §UI 集成约束 + R5-6: Decimal fields serialise as JSON strings
to avoid JS Number precision loss past the 17th significant digit.

```json
{
  "price_range_low": "10.00",
  "total_cost_usd": "1.85"
}
```

The UI's `openapi-typescript`-generated types reflect this — they're
`string` on the wire, parsed to BigDecimal / number client-side as needed.

---

## 6. Audit middleware

Every write request (POST / PUT / PATCH / DELETE) auto-logs to the
`audit_logs` table via `AuditMiddleware`. R6-8: the middleware infers
`resource_type` + `resource_id` from the path so audit queries by
resource actually return rows. R6-3: callers with `READ_AUDIT` but
not `READ_AUDIT_FULL` see redacted (null) `before_state` /
`after_state` / `diff` / `ip_address` / `user_agent` / `error_message`.

---

## 7. Realtime channels

- **SSE** (`GET /api/events/stream`): server → client push, used for
  analysis progress, alert pop-ups, dashboard live updates. See
  [SSE_PROTOCOL.md](SSE_PROTOCOL.md).
- **WebSocket** (`WS /api/ws/chat/{session_id}`): bidirectional chat.
  See [WS_PROTOCOL.md](WS_PROTOCOL.md).

Both authenticate via the `?token=<jwt>` query parameter (WS headers
are proxy-unfriendly).

---

## 8. Versioning policy

The OpenAPI `info.version` follows the project semver tag (currently
`v1.0.9`). Breaking changes — schema field rename, endpoint removal,
auth contract change — go through ADR + CHANGELOG; the UI repo's CI
catches them by diffing the regenerated TypeScript types.
