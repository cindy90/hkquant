"""R6-5 — CostGuard middleware uses tight path matching.

Pre-R6-5 the cheap-paths check was ``any(path.startswith(p) for p in _CHEAP_PATHS)``.
That naively prefix-matched: a path like ``/api/dashboard-llm-tool`` would
bypass the cost guard because ``/api/dashboard`` is a substring prefix.
An attacker (or accidental future endpoint) could mount a costly LLM
operation at an under-guarded path.

Post-R6-5 the check requires a true segment boundary: either the path
exactly equals an entry, OR the entry is a prefix followed by ``/``.
``/api/dashboard-llm-tool`` is correctly NOT exempted; ``/api/dashboard/summary``
still is.

We test the matcher directly (``_is_cheap_path``) — no FastAPI client needed.
"""

from __future__ import annotations

from hk_ipo_agent.api.middleware.cost_guard import _is_cheap_path

# ------------------------------------------------------------------ true cheap


def test_health_is_cheap() -> None:
    assert _is_cheap_path("/health")


def test_dashboard_summary_is_cheap() -> None:
    """R6-5 — true subpath of a cheap prefix → cheap."""
    assert _is_cheap_path("/api/dashboard/summary")


def test_snapshots_root_is_cheap() -> None:
    assert _is_cheap_path("/api/snapshots/")


def test_snapshots_detail_is_cheap() -> None:
    assert _is_cheap_path("/api/snapshots/abc-123")


def test_audit_logs_is_cheap() -> None:
    assert _is_cheap_path("/api/audit/logs")


# ------------------------------------------------------------------ NOT cheap


def test_lookalike_dashboard_is_not_cheap() -> None:
    """R6-5 — substring match without segment boundary → NOT cheap."""
    assert not _is_cheap_path("/api/dashboard-llm-tool")


def test_lookalike_audit_is_not_cheap() -> None:
    """R6-5 — /api/audit-export (hypothetical LLM-backed path) → NOT cheap."""
    assert not _is_cheap_path("/api/audit-export")


def test_lookalike_health_is_not_cheap() -> None:
    """R6-5 — /healthz-fake is NOT the actual /health endpoint."""
    assert not _is_cheap_path("/healthz-fake")


def test_analysis_endpoint_is_not_cheap() -> None:
    """R6-5 — /api/analysis hits LLM; never cheap."""
    assert not _is_cheap_path("/api/analysis/run")


def test_chat_endpoint_is_not_cheap() -> None:
    """R6-5 — /api/chat hits LLM."""
    assert not _is_cheap_path("/api/chat/send")


def test_whatif_endpoint_is_not_cheap() -> None:
    """R6-5 — /api/whatif may run LLM-backed valuation rerun."""
    assert not _is_cheap_path("/api/whatif/run")


def test_empty_path_is_not_cheap() -> None:
    assert not _is_cheap_path("")


def test_exact_dashboard_prefix_with_no_trailing_slash_is_cheap() -> None:
    """R6-5 — ``/api/dashboard`` (exact, no trailing slash) is also exempted.

    This matters because FastAPI normalises some forms; a request to the
    bare prefix without a sub-route is still a no-LLM call.
    """
    assert _is_cheap_path("/api/dashboard")
