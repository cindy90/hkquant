"""Airflow DAG: monthly_learning_cycle — runs the Phase 10 learning loop.

PROJECT_SPEC.md §3.12 (learning_loop) + ADR 0012 §7.5d.

Schedule: 1st of each month 03:00 HKT. The DAG itself is a stub here
in 7.5d because the learning_loop module is Phase 10; this file ships
the cron + structure so the DAG is registered, and the actual
``do_work`` lands when Phase 10 modules exist.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

try:
    from airflow import DAG
    from airflow.operators.python import PythonOperator

    AIRFLOW_AVAILABLE = True
except ImportError:  # pragma: no cover
    AIRFLOW_AVAILABLE = False
    DAG = None  # type: ignore[assignment, misc]
    PythonOperator = None  # type: ignore[assignment, misc]


def _run_learning_cycle(**context: Any) -> dict[str, Any]:
    """R8-9: delegate to the shared monthly-learning runner.

    Phase 10 already shipped drift_detector + adjustment_proposer; the
    runner imports + smoke-checks them, then raises with a clear hint
    that operator wiring of the full propose → review → apply flow is
    Phase 10 commissioning (CLAUDE.md: NEVER auto-apply).
    """
    from ._dag_runners import run_monthly_learning_sync

    return run_monthly_learning_sync(**context)


if AIRFLOW_AVAILABLE:
    with DAG(  # type: ignore[misc]
        dag_id="monthly_learning_cycle",
        default_args={"owner": "hk-ipo-agent", "retries": 0},
        description="Phase 10 learning loop — drift + attribution aggregation",
        schedule="0 3 1 * *",  # 1st of month, 03:00 HKT
        start_date=datetime(2026, 5, 16),
        catchup=False,
        max_active_runs=1,
        tags=["hk-ipo", "learning_loop", "phase_10"],
    ) as dag:
        PythonOperator(  # type: ignore[misc]
            task_id="run_learning_cycle",
            python_callable=_run_learning_cycle,
        )


__all__ = ("AIRFLOW_AVAILABLE",)
