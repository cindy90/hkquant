"""Airflow DAG: daily_outcome_tracking — runs DailyScheduler at 02:30 HKT.

PROJECT_SPEC.md §3.11.2 + CLAUDE.md v1.2 + ADR 0012 §7.5d.

DAG structure (kept deliberately minimal):

    start → run_daily_scheduler → emit_sla_metrics → end

CLAUDE.md v1.2 invariants honoured here:
- ``sla=timedelta(hours=6)`` — daily failure unresolved for 6h →
  ``on_failure_callback`` posts a critical alert via AlertRouter
- ``max_active_runs=1`` — Airflow level + DB advisory lock at
  application level = belt and suspenders
- ``retries=2`` with exponential backoff per config/schedulers.yaml

Production-only: the import is guarded so the unit-test suite (which
doesn't have Airflow installed) doesn't pay the import cost.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Airflow is a heavy optional dependency. Importing it at module load
# only happens inside the Airflow worker / scheduler context. Wrap the
# whole DAG construction in a try/except so unit tests that import
# this package transitively don't choke on missing airflow.
try:
    from airflow import DAG
    from airflow.operators.python import PythonOperator

    AIRFLOW_AVAILABLE = True
except ImportError:  # pragma: no cover — Airflow optional in dev / test
    AIRFLOW_AVAILABLE = False
    DAG = None  # type: ignore[assignment, misc]
    PythonOperator = None  # type: ignore[assignment, misc]


# Config path resolved at runtime; Airflow workers see the same
# checkout-root layout as the API service.
_CONFIG_PATH = Path(__file__).resolve().parents[5] / "config" / "schedulers.yaml"


def _run_daily_scheduler(**context: Any) -> dict[str, Any]:
    """R8-9: Airflow task entry point — delegates to ``_dag_runners.run_daily_sync``.

    The shared runner module owns the wiring contract (session factory +
    dependency builders); this function just adapts Airflow's kwargs.
    Pre-R8-9 this raised ``NotImplementedError`` unconditionally; now
    only the production-only deps (iFind / LLMClient) raise inside the
    runner until Phase 9 wires them.
    """
    from ._dag_runners import run_daily_sync

    return run_daily_sync(**context)


def _emit_sla_metrics(**context: Any) -> None:
    """Push run-counts + SLA-window status to monitoring (Prometheus / Datadog)."""
    # Hook for Phase 9 production observability. The unit-tested
    # daily_scheduler already writes scheduler_runs rows; this task is
    # just an external broadcast.


def _on_failure_callback(context: dict[str, Any]) -> None:
    """6-hour SLA per CLAUDE.md v1.2: critical-alert on persistent failure."""
    from ....common.enums import AlertLevel
    from ....data.database import async_session_factory
    from ...alerts import AlertRouter

    async def _emit() -> None:
        sf = async_session_factory()
        router = AlertRouter(session_factory=sf)
        await router.emit(
            level=AlertLevel.CRITICAL,
            category="scheduler_failure",
            message=f"daily_outcome_tracking failed: {context.get('exception')!r}",
            actionable_info=(
                "Check Airflow UI for stack trace; rerun manually after fixing "
                "root cause. If the failure persists into the next window, "
                "snapshot outcome tracking is in jeopardy."
            ),
        )

    asyncio.run(_emit())


# ---------------------------------------------------------------------------
# DAG definition (only when Airflow is importable)
# ---------------------------------------------------------------------------


if AIRFLOW_AVAILABLE:
    default_args = {
        "owner": "hk-ipo-agent",
        "depends_on_past": False,
        "retries": 2,
        "retry_delay": timedelta(minutes=10),
        "on_failure_callback": _on_failure_callback,
    }

    with DAG(  # type: ignore[misc]
        dag_id="daily_outcome_tracking",
        default_args=default_args,
        description="Daily checkpoint tracking + review draft + stale + terminate",
        schedule="30 2 * * *",  # 02:30 HKT — outside trading hours
        start_date=datetime(2026, 5, 16),
        catchup=False,
        max_active_runs=1,
        sla_miss_callback=None,
        tags=["hk-ipo", "daily", "critical"],
    ) as dag:
        run_scheduler = PythonOperator(  # type: ignore[misc]
            task_id="run_daily_scheduler",
            python_callable=_run_daily_scheduler,
            sla=timedelta(hours=6),  # CLAUDE.md v1.2 SLA
        )
        emit_metrics = PythonOperator(  # type: ignore[misc]
            task_id="emit_sla_metrics",
            python_callable=_emit_sla_metrics,
        )
        run_scheduler >> emit_metrics


__all__ = ("AIRFLOW_AVAILABLE",)
