"""R8-8 — PGAlertStore reads alerts from the PG ``alerts`` table.

Pre-R8-8 the API-side ``AlertStore`` (api/routers/alerts.py) was
in-memory only. The scheduler-side ``AlertRouter`` already writes
to the PG ``alerts`` table, but those writes were invisible to the
``GET /api/alerts/`` endpoint — operators saw an empty list while
the system was emitting alerts. The two sides were disconnected.

Post-R8-8:
  * ``PGAlertStore`` reads from ``AlertRow`` and implements the same
    Protocol surface as the in-memory ``AlertStore``: list / acknowledge.
    Add is intentionally not supported on the API side (alerts come
    FROM the scheduler ``AlertRouter``, not from the API).
  * ``set_alert_store(store)`` setter lets the FastAPI lifespan
    install ``PGAlertStore`` for production. Tests stay in-memory
    via the default.
"""

from __future__ import annotations

import inspect


def test_pg_alert_store_class_exists() -> None:
    """R8-8 — PGAlertStore is importable from api.routers.alerts."""
    from hk_ipo_agent.api.routers import alerts as alerts_mod

    assert hasattr(alerts_mod, "PGAlertStore"), "R8-8: api.routers.alerts must expose PGAlertStore"


def test_set_alert_store_setter_exists() -> None:
    """R8-8 — public set_alert_store(store) lets the lifespan swap stores."""
    from hk_ipo_agent.api.routers import alerts as alerts_mod

    assert hasattr(alerts_mod, "set_alert_store"), (
        "R8-8: api.routers.alerts must expose set_alert_store"
    )


def test_pg_alert_store_has_list_and_acknowledge() -> None:
    """R8-8 — PGAlertStore implements ``list`` + ``acknowledge``."""
    from hk_ipo_agent.api.routers.alerts import PGAlertStore

    assert hasattr(PGAlertStore, "list")
    assert hasattr(PGAlertStore, "acknowledge")


def test_pg_alert_store_signature_takes_session_factory() -> None:
    """R8-8 — constructor takes session_factory (matches PG store pattern)."""
    from hk_ipo_agent.api.routers.alerts import PGAlertStore

    sig = inspect.signature(PGAlertStore.__init__)
    param_names = [p.name for p in sig.parameters.values()]
    assert "session_factory" in param_names, (
        f"R8-8: PGAlertStore.__init__ must accept session_factory (got {param_names})"
    )


def test_set_alert_store_overrides_get_alert_store() -> None:
    """R8-8 — after set_alert_store(x), get_alert_store() returns x."""
    from hk_ipo_agent.api.routers.alerts import (
        AlertStore,
        get_alert_store,
        reset_alert_store_for_test,
        set_alert_store,
    )

    reset_alert_store_for_test()
    custom = AlertStore()
    set_alert_store(custom)
    assert get_alert_store() is custom
    reset_alert_store_for_test()
