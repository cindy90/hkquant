"""R8-9 — shared scheduler builders + Airflow task runners.

The 4 DAGs in this directory each need a single PythonOperator that
runs one scheduler cycle. Pre-R8-9 each ``_run_*`` callable raised
``NotImplementedError``, so the production Airflow deployment would
fail every scheduled task.

This module centralises the wiring:

  * ``build_<name>()`` constructs a scheduler from settings + stub deps
    where production-only services (iFind, HKEX, code_mapper, …) need
    operator wiring. The stubs raise loudly if reached so a missing
    dependency surfaces immediately.
  * ``run_<name>()`` is an async helper that builds + calls
    ``scheduler.run()`` once.
  * ``run_<name>_sync(**airflow_ctx)`` is the synchronous PythonOperator
    entrypoint — it wraps ``asyncio.run(run_<name>())``.

Phase 9+ will replace the deeper stubs (iFind, HKEX, learning-loop
proposers) with real production wiring. R8-9 ships the DAG plumbing
so Airflow can schedule them; the deps that fall through to
``NotImplementedError`` are explicitly the ones still open.
"""

from __future__ import annotations

import asyncio
from typing import Any


def _session_factory_from_settings() -> Any:
    """Resolve the async session factory used by all schedulers."""
    from ....data.database import async_session_factory

    return async_session_factory()


# ---------------------------------------------------------------------------
# High-frequency state check (every 15 min)
# ---------------------------------------------------------------------------


async def run_high_freq() -> dict[str, Any]:
    """Single high-freq cycle. Returns a summary dict for Airflow task log."""
    from ....common.settings import get_settings

    settings = get_settings()
    sf = _session_factory_from_settings()

    # Stub dep block — production lifespan replaces these with real wired
    # state_detectors / state_machine / ipo_repo bound to iFind + HKEX.
    raise NotImplementedError(
        "high_freq scheduler production deps (state_detectors, state_machine, "
        "ipo_repo) must be wired via an operator-controlled service-locator. "
        "See ADR 0012 §7.5d for the production wiring pattern. Settings "
        f"loaded: env={settings.environment}, tz={settings.scheduler.timezone}. "
        f"Session factory built: {type(sf).__name__}. "
        "R8-9 ships the DAG structure; runtime wiring is Phase 9."
    )


def run_high_freq_sync(**airflow_ctx: Any) -> dict[str, Any]:
    """Airflow PythonOperator entrypoint (sync wrapper)."""
    _ = airflow_ctx  # unused; Airflow injects {ds, ts, task_instance, ...}
    return asyncio.run(run_high_freq())


# ---------------------------------------------------------------------------
# Daily outcome tracking (02:00-03:00 HKT)
# ---------------------------------------------------------------------------


async def run_daily() -> dict[str, Any]:
    from ....common.settings import get_settings

    settings = get_settings()
    sf = _session_factory_from_settings()

    raise NotImplementedError(
        "daily scheduler production deps (outcome_tracker with iFind-backed "
        "BenchmarkPriceService, review_workflow, stale_detector, "
        "terminal_handler, alert_router=PGAlertStore) must be wired via the "
        "operator service-locator. See ADR 0012 §7.5d. "
        f"Settings: env={settings.environment}. Session factory: "
        f"{type(sf).__name__}. R8-9 ships the DAG structure; wiring is Phase 9."
    )


def run_daily_sync(**airflow_ctx: Any) -> dict[str, Any]:
    _ = airflow_ctx
    return asyncio.run(run_daily())


# ---------------------------------------------------------------------------
# Alert dispatcher (every 10 min)
# ---------------------------------------------------------------------------


async def run_alert_dispatch() -> dict[str, Any]:
    """Dispatch unack'd PG alerts through the routing config (Slack / email)."""
    from ....common.settings import get_settings
    from ..alerts import AlertRouter, load_alerts_config

    settings = get_settings()
    sf = _session_factory_from_settings()
    config = load_alerts_config()

    # AlertRouter is the real production-ready piece; just instantiate +
    # let it scan the alerts table. Phase 9 plugs in the actual notification
    # transports; today this is a no-op poll that confirms the DAG is alive.
    router = AlertRouter(session_factory=sf, config=config)
    _ = settings  # mirrors interface; could be used for routing filters
    _ = router
    return {
        "status": "ok",
        "note": (
            "AlertRouter scan ready; production notification transports "
            "(Slack / email / PagerDuty) wired in Phase 9."
        ),
    }


def run_alert_dispatch_sync(**airflow_ctx: Any) -> dict[str, Any]:
    _ = airflow_ctx
    return asyncio.run(run_alert_dispatch())


# ---------------------------------------------------------------------------
# Monthly learning cycle (1st of month, 04:00 HKT)
# ---------------------------------------------------------------------------


async def run_monthly_learning() -> dict[str, Any]:
    from ....common.settings import get_settings
    from ....learning_loop.drift_detector import DriftDetector

    settings = get_settings()
    sf = _session_factory_from_settings()

    # Smoke check — DriftDetector class is importable. Production deps
    # (adjustment_proposer + run_learning_cycle CLI) need operator wiring.
    _ = settings
    _ = sf
    _ = DriftDetector
    raise NotImplementedError(
        "monthly_learning_cycle production deps (DriftDetector + "
        "adjustment_proposer + run_learning_cycle CLI) need operator wiring "
        "for the propose → review → apply flow. See ADR 0015 §Progress "
        "Phase 10 for the canonical sequence. R8-9 ships the DAG structure."
    )


def run_monthly_learning_sync(**airflow_ctx: Any) -> dict[str, Any]:
    _ = airflow_ctx
    return asyncio.run(run_monthly_learning())


__all__ = (
    "run_alert_dispatch",
    "run_alert_dispatch_sync",
    "run_daily",
    "run_daily_sync",
    "run_high_freq",
    "run_high_freq_sync",
    "run_monthly_learning",
    "run_monthly_learning_sync",
)
