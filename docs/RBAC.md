# RBAC

> Role-based access control: roles, permissions, and how endpoints
> enforce them. Source of truth is `src/hk_ipo_agent/common/enums.py`
> (`UserRole`, `Permission`, `ROLE_PERMISSIONS`).

---

## 1. Roles (6)

| Role | Persona | Description |
|---|---|---|
| `viewer` | Analyst-in-training | Read-only across all surfaces |
| `reviewer` | Junior reviewer | Read + submit reviews, propose adjustments, ack alerts, trigger analyses, run What-If, use chat |
| `senior_reviewer` | Senior reviewer | Reviewer + accept/reject proposals |
| `operator` | Ops / SRE | Reviewer + manage config, manage scheduler |
| `admin` | Tech lead | Everything except auditor-only items, plus user management + full audit |
| `auditor` | Compliance | Read-only + full audit payloads |

`auditor` is intentionally **not a superset of viewer-then-reviewer** —
they only have read perms plus `READ_AUDIT` / `READ_AUDIT_FULL`.

---

## 2. Permissions (22)

```
# read
snapshots.read     reviews.read     proposals.read
audit.read         audit.read_full  settings.read
dashboard.read     ipo.read         alert.read         prospectus.read

# write
reviews.submit         proposals.propose      proposals.accept
proposals.reject       alerts.acknowledge     analysis.trigger
whatif.run             chat.use

# system
config.manage          users.manage           scheduler.manage
```

`_BASE_READ` (10 perms) is granted to every authenticated role — see
`enums.py:294`. The 4 per-surface read perms (dashboard / ipo / alert /
prospectus) were added in R6-1 so endpoints can use a specific perm
instead of the broad `READ_SNAPSHOTS`.

---

## 3. Role → permission matrix

| Role | Reads | Writes | System |
|---|---|---|---|
| `viewer` | `_BASE_READ` | — | — |
| `reviewer` | `_BASE_READ` | reviewer set | — |
| `senior_reviewer` | `_BASE_READ` | reviewer set + accept/reject | — |
| `operator` | `_BASE_READ` | reviewer set | config + scheduler |
| `admin` | `_BASE_READ` + `READ_AUDIT` + `READ_AUDIT_FULL` | full | full + users |
| `auditor` | `_BASE_READ` + `READ_AUDIT` + `READ_AUDIT_FULL` | — | — |

Reviewer set = `reviews.submit`, `proposals.propose`, `alerts.acknowledge`,
`analysis.trigger`, `whatif.run`, `chat.use`.

---

## 4. Enforcement in endpoints

Two FastAPI dependencies in `src/hk_ipo_agent/api/auth/dependencies.py`:

```python
from .auth.dependencies import require_permission, require_role

@router.get(
    "/api/snapshots/{id}",
    dependencies=[Depends(require_permission(Permission.READ_SNAPSHOTS))],
)
async def get_snapshot(id: UUID) -> SnapshotOut: ...

@router.post(
    "/api/admin/users",
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
async def create_user(...) -> UserOut: ...
```

Both 403 on failure with `requires permission: <perm>` / `requires one
of roles: [...]` in the `detail` field (wrapped in the RFC 7807
Problem Details envelope).

**CLAUDE.md §UI 集成约束 HARD constraint**:
> RBAC checks are an endpoint requirement. Except for `/health`,
> `/ready`, `/metrics`, every route MUST use `require_role()` /
> `require_permission()`.

Audited via `tests/unit/api/test_rbac_coverage.py` — every router
not in the allow-list is checked for at least one of these deps.

---

## 5. JWT claim shape

The JWT issued by `POST /api/auth/login` carries:

```json
{
  "sub": "uuid",
  "email": "alice@example.com",
  "roles": ["reviewer", "senior_reviewer"],
  "exp": 1740000000
}
```

A user can have multiple roles; permissions are unioned via
`permissions_for_roles()`. The dependency `get_current_user`
hydrates `CurrentUser` from this claim plus a PG lookup
(`resolve_user_async` — R6-4 PG-first with in-memory dev fallback).

---

## 6. Audit redaction (R6-3)

`audit_middleware.py` writes `before_state` / `after_state` / `diff` /
`ip_address` / `user_agent` / `error_message` on every write request.
These fields are **sensitive** (may contain PII or internal state).

The `GET /api/audit/logs` endpoint redacts them based on the caller:

| Caller permissions | What they see |
|---|---|
| Lacks `READ_AUDIT` | 403 |
| `READ_AUDIT`, no `READ_AUDIT_FULL` | Metadata visible, 6 sensitive fields nulled |
| `READ_AUDIT_FULL` | Full payload |

Only `admin` and `auditor` get `READ_AUDIT_FULL`. Reviewers/operators
who somehow get `READ_AUDIT` (via custom role grants) see the redacted
shape.

---

## 7. Resource inference (R6-8)

`audit_middleware.py::_infer_resource_from_path` parses the URL path
to fill `audit_logs.resource_type` and `resource_id`, so the
`GET /api/audit/logs?resource_type=snapshot&resource_id=...` filter
actually returns rows. Without this the middleware wrote a row but
with NULL resource fields, making it useless for forensics.

---

## 8. Adding a new permission

1. Append to `Permission` enum in `common/enums.py`.
2. Add to the right tuple (`_BASE_READ` / `_REVIEWER_WRITE` /
   `_SENIOR_EXTRA` / `_OPERATOR_EXTRA`) AND to `ROLE_PERMISSIONS`
   if it should belong to admin / auditor only.
3. Use `Depends(require_permission(Permission.NEW_PERM))` on the
   relevant route.
4. Add a unit test asserting non-grantees get 403.
5. Update §3 matrix above + the [API_REFERENCE.md](API_REFERENCE.md)
   router table.

---

## 9. Adding a new role

Roles change the auth contract — go through ADR + CHANGELOG. The
`UserRole` enum is small by design (6 personas mapped to actual
operational seats). Resist the urge to add `"junior_operator"` etc.;
prefer composing existing roles on the user record (a user can have
multiple roles).

---

## 10. See also

- `common/enums.py` — `UserRole`, `Permission`, `ROLE_PERMISSIONS`
- `api/auth/rbac.py` — `has_role` / `has_permission` / `roles_from_strings`
- `api/auth/dependencies.py` — `require_role` / `require_permission`
- `api/auth/audit_middleware.py` — write logging + resource inference
- PROJECT_SPEC.md §6 + §16.5
