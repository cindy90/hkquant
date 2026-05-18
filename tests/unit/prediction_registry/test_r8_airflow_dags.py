"""R8-9 — 4 Airflow DAG ``_run_*`` callables actually do work, not raise NotImplementedError.

Pre-R8-9 each DAG file had:

    def _run_high_freq_scheduler(**context):
        raise NotImplementedError(
            "Production DAG body must wire real state_detectors / code_mapper..."
        )

That defeats the point of shipping the DAG — Airflow would import the file
fine but every scheduled run would crash. CLAUDE.md §自动化与状态机约束:
"生产环境必须用 Airflow" — so the DAG bodies have to actually run the
scheduler.

Post-R8-9 each ``_run_*`` callable:
  * Builds the scheduler from a shared service-locator factory.
  * Calls ``asyncio.run(scheduler.run())`` to perform one cycle.
  * Returns a small dict summary for Airflow's task log.
  * Stays gracefully importable even when Airflow itself isn't installed
    (the existing ``AIRFLOW_AVAILABLE`` gate is preserved).

These tests verify the callables exist + don't unconditionally raise.
"""

from __future__ import annotations

import ast
import inspect


def _airflow_dag_module(name: str):
    """Import one of the 4 DAG modules by short name."""
    import importlib

    return importlib.import_module(
        f"hk_ipo_agent.prediction_registry.schedulers.airflow_dags.{name}"
    )


_DAG_MODULES = (
    "high_freq_state_check",
    "daily_outcome_tracking",
    "alert_dispatcher",
    "monthly_learning_cycle",
)


def test_all_dag_modules_import() -> None:
    """R8-9 — all 4 DAG modules import cleanly (Airflow-optional)."""
    for name in _DAG_MODULES:
        mod = _airflow_dag_module(name)
        assert hasattr(mod, "AIRFLOW_AVAILABLE")


def test_run_callables_no_longer_unconditionally_raise() -> None:
    """R8-9 — each module's ``_run_*`` top-level callable's body
    does NOT consist of a single ``raise NotImplementedError(...)``.

    The body may contain a NotImplementedError as a fallback inside an
    if-branch, but the whole function body must not be one bare raise.
    """
    for name in _DAG_MODULES:
        mod = _airflow_dag_module(name)
        # Find the _run_* callable.
        candidates = [
            attr for attr in dir(mod) if attr.startswith("_run_") and callable(getattr(mod, attr))
        ]
        assert candidates, f"R8-9: {name} has no _run_* callable"
        import textwrap

        for attr in candidates:
            fn = getattr(mod, attr)
            source = inspect.getsource(fn)
            tree = ast.parse(textwrap.dedent(source))
            # Find the function def node.
            fn_node = None
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == attr:
                    fn_node = node
                    break
            assert fn_node is not None
            # Check body: must NOT be a single Raise statement.
            non_docstring_body = [
                stmt
                for stmt in fn_node.body
                if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant))
            ]
            if len(non_docstring_body) == 1 and isinstance(non_docstring_body[0], ast.Raise):
                raise AssertionError(
                    f"R8-9: {name}.{attr} body is a single ``raise`` — must "
                    "do real work (build scheduler + run)"
                )


def test_run_callables_delegate_to_shared_runner() -> None:
    """R8-9 — each ``_run_*`` body delegates to the shared
    ``_dag_runners`` module (which owns the scheduler-class wiring).

    Pre-R8-9 the bodies were each a bare ``raise NotImplementedError``;
    post-R8-9 they're 1-line delegations to the shared runner so the
    DAGs share the same wiring contract.
    """
    for name in _DAG_MODULES:
        mod = _airflow_dag_module(name)
        source_blob = ""
        for attr in dir(mod):
            if attr.startswith("_run_") and callable(getattr(mod, attr)):
                source_blob += inspect.getsource(getattr(mod, attr))
        assert "_dag_runners" in source_blob, (
            f"R8-9: {name} _run_* must delegate to the shared _dag_runners module"
        )


def test_shared_dag_runners_module_exists_and_exposes_4_runners() -> None:
    """R8-9 — _dag_runners exposes one sync runner per DAG."""
    from hk_ipo_agent.prediction_registry.schedulers.airflow_dags import _dag_runners

    for name in (
        "run_high_freq_sync",
        "run_daily_sync",
        "run_alert_dispatch_sync",
        "run_monthly_learning_sync",
    ):
        assert hasattr(_dag_runners, name), f"R8-9: _dag_runners must expose {name}"
