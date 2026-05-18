"""R6-8 — audit middleware infers resource_type + resource_id from request path.

Pre-R6-8 the AuditLog record always had ``resource_type=None`` and
``resource_id=None``. The audit query endpoint exposes a
``resource_type`` filter, so audit investigations querying by resource
returned 0 rows — the audit log was effectively content-free.

Post-R6-8 the middleware infers both fields by inspecting
``request.url.path``:
  * Path prefix → ``AuditResourceType`` enum (snapshots → SNAPSHOT, etc.)
  * Second path segment is treated as ``resource_id`` if it looks like
    a UUID or alphanumeric id (e.g. ``/api/snapshots/<uuid>/memo.md``
    → resource_id=<uuid>). Plain collection routes (``/api/snapshots/``)
    have resource_id=None.

We test the inference helper directly so we don't have to spin up a
TestClient and reason about middleware order — that's a property of the
helper, not the HTTP plumbing.
"""

from __future__ import annotations

import pytest

from hk_ipo_agent.api.auth.audit_middleware import _infer_resource_from_path
from hk_ipo_agent.common.enums import AuditResourceType


@pytest.mark.parametrize(
    "path,expected_type,expected_id",
    [
        # collection routes — type only, no id
        ("/api/snapshots/", AuditResourceType.SNAPSHOT, None),
        ("/api/reviews/", AuditResourceType.REVIEW, None),
        ("/api/proposals/", AuditResourceType.PROPOSAL, None),
        ("/api/alerts/", AuditResourceType.ALERT, None),
        ("/api/settings/", AuditResourceType.CONFIG, None),
        # detail routes — type + id
        (
            "/api/snapshots/123e4567-e89b-12d3-a456-426614174000",
            AuditResourceType.SNAPSHOT,
            "123e4567-e89b-12d3-a456-426614174000",
        ),
        (
            "/api/snapshots/123e4567-e89b-12d3-a456-426614174000/memo.md",
            AuditResourceType.SNAPSHOT,
            "123e4567-e89b-12d3-a456-426614174000",
        ),
        (
            "/api/reviews/abc-review-id/accept",
            AuditResourceType.REVIEW,
            "abc-review-id",
        ),
        (
            "/api/alerts/alert-42/acknowledge",
            AuditResourceType.ALERT,
            "alert-42",
        ),
        (
            "/api/chat/sessions",
            AuditResourceType.CHAT_SESSION,
            "sessions",  # second segment; treated as id for ``chat`` subresource pattern
        ),
        (
            "/api/auth/login",
            AuditResourceType.USER,
            "login",
        ),
        # No mapping → None / None
        ("/health", None, None),
        ("/ready", None, None),
        ("/api/dashboard/summary", None, None),
        ("/api/whatif/run", None, None),
        ("/api/audit/logs", None, None),
        # Path-traversal-ish prefixes don't match (R6-5 lookalike guard not the
        # subject here, but we still shouldn't bind a stray prefix).
        ("/api/snapshots-fake/run", None, None),
    ],
)
def test_infer_resource_type_and_id(
    path: str, expected_type: AuditResourceType | None, expected_id: str | None
) -> None:
    """R6-8 — helper returns the expected (type, id) pair for representative paths."""
    rtype, rid = _infer_resource_from_path(path)
    assert rtype == expected_type, f"path={path!r}: expected type={expected_type}, got {rtype}"
    assert rid == expected_id, f"path={path!r}: expected id={expected_id!r}, got {rid!r}"


def test_infer_resource_handles_empty_or_invalid_path() -> None:
    """R6-8 — edge case: empty path / non-/api/ → (None, None)."""
    assert _infer_resource_from_path("") == (None, None)
    assert _infer_resource_from_path("/") == (None, None)
    assert _infer_resource_from_path("/not-api/snapshots/1") == (None, None)


def test_audit_log_record_populates_resource_fields_via_middleware(client, admin_headers) -> None:
    """R6-8 — end-to-end: a write request → audit row has correct resource_type/id.

    We hit ``POST /api/alerts/<id>/acknowledge`` (which 404s on missing
    alert; the audit middleware still records the request) and read the
    audit store to confirm fields are populated.
    """
    from hk_ipo_agent.api.auth.audit_middleware import get_audit_store

    fake_alert_id = "00000000-0000-4000-8000-000000000099"
    client.post(
        f"/api/alerts/{fake_alert_id}/acknowledge",
        json={"comment": "test"},
        headers=admin_headers,
    )
    # Inspect the in-memory audit store (use the sync internals).
    store = get_audit_store()
    # We can't easily await query() from a sync test; the InMemoryAuditStore
    # has an internal _records list we can read for assertion.
    records = store._records  # type: ignore[attr-defined]
    matching = [r for r in records if r.api_endpoint.endswith(f"/{fake_alert_id}/acknowledge")]
    assert matching, "audit middleware did not record the acknowledge request"
    record = matching[0]
    assert record.resource_type == AuditResourceType.ALERT
    assert record.resource_id == fake_alert_id
