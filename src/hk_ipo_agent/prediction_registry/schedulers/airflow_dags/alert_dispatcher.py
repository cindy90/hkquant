"""Airflow DAG: alert_dispatcher — runs EventDrivenScheduler every 5 min.

PROJECT_SPEC.md §3.11.2 + ADR 0012 §7.5d.

This DAG is the safety-net for the realtime webhook path: HKEX RSS
poll + iFind anomaly poll + 披露易 poll are all wrapped in adapters
that buffer events into the queue, and this DAG sweeps that queue.

Also dispatches stale-detector alerts from the daily run that haven't
been ack'd within their level-appropriate window.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

try:
    from airflow import DAG
    from airflow.operators.python import PythonOperator

    AIRFLOW_AVAILABLE = True
except ImportError:  # pragma: no cover
    AIRFLOW_AVAILABLE = False
    DAG = None  # type: ignore[assignment, misc]
    PythonOperator = None  # type: ignore[assignment, misc]


def _run_event_driven_scheduler(**context: Any) -> dict[str, Any]:
    """Single sweep of the event queue."""
    raise NotImplementedError(
        "Production DAG body must wire EventDrivenScheduler with the real "
        "EventQueue adapter (HKEX RSS / iFind webhook / 披露易 poll) + "
        "EarningsComparator + AlertRouter. See api/main.py lifespan."
    )


def _dispatch_unacked_alerts(**context: Any) -> dict[str, Any]:
    """Re-emit alerts that have been open > escalation_window per category.

    Phase 9 hooks this up to the on-call rotation in PagerDuty.
    """
    raise NotImplementedError(
        "Production DAG body queries alerts table for open rows > escalation "
        "window and re-emits at next severity tier."
    )


if AIRFLOW_AVAILABLE:
    with DAG(  # type: ignore[misc]
        dag_id="alert_dispatcher",
        default_args={
            "owner": "hk-ipo-agent",
            "retries": 1,
            "retry_delay": timedelta(minutes=1),
        },
        description="Event-queue sweep + unacked-alert escalation",
        schedule="*/5 * * * *",
        start_date=datetime(2026, 5, 16),
        catchup=False,
        max_active_runs=1,
        tags=["hk-ipo", "event_driven", "alerts"],
    ) as dag:
        sweep_events = PythonOperator(  # type: ignore[misc]
            task_id="run_event_driven_scheduler",
            python_callable=_run_event_driven_scheduler,
        )
        dispatch_alerts = PythonOperator(  # type: ignore[misc]
            task_id="dispatch_unacked_alerts",
            python_callable=_dispatch_unacked_alerts,
        )
        sweep_events >> dispatch_alerts


__all__ = ("AIRFLOW_AVAILABLE",)
