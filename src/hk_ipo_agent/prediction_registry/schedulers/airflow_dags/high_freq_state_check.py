"""Airflow DAG: high_freq_state_check — runs HighFrequencyScheduler every 15 min.

PROJECT_SPEC.md §3.11.2 + ADR 0012 §7.5d.

DAG structure (minimal — single PythonOperator):

    start → run_high_freq_scheduler → end

No SLA configured (failure of a single 15-min run isn't a critical
incident; multiple consecutive failures are escalated via the
``on_failure_alert_after_runs`` knob in config/schedulers.yaml — the
``alert_dispatcher`` DAG polls scheduler_runs for that pattern).
"""

from __future__ import annotations

import asyncio
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


def _run_high_freq_scheduler(**context: Any) -> dict[str, Any]:
    """Run HighFrequencyScheduler once. Production lifespan wires real
    iFind / HKEX / code_mapper / event_detector deps."""
    raise NotImplementedError(
        "Production DAG body must wire real state_detectors / code_mapper / "
        "event_detector against the iFind + HKEX clients. See api/main.py "
        "lifespan for the canonical service-locator pattern."
    )


if AIRFLOW_AVAILABLE:
    with DAG(  # type: ignore[misc]
        dag_id="high_freq_state_check",
        default_args={
            "owner": "hk-ipo-agent",
            "retries": 3,
            "retry_delay": timedelta(seconds=60),
        },
        description="15-min lightweight scan: state detectors + 2h event lookback",
        schedule="*/15 * * * *",
        start_date=datetime(2026, 5, 16),
        catchup=False,
        max_active_runs=1,
        tags=["hk-ipo", "high_freq"],
    ) as dag:
        PythonOperator(  # type: ignore[misc]
            task_id="run_high_freq_scheduler",
            python_callable=_run_high_freq_scheduler,
        )


# Suppress unused-import warning when Airflow isn't loaded.
_ = asyncio


__all__ = ("AIRFLOW_AVAILABLE",)
