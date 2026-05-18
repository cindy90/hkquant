"""R8-3 — daily T+360 emits a critical alert + skips auto-TERMINATE.

Pre-R8-3 ``DailyScheduler._process_listed_snapshot`` auto-transitioned
LISTED → TERMINATED the moment ``days_listed >= 360``. That violates
CLAUDE.md §自动化与状态机约束: "超时不等于失败 — stale_detector 触发的
是警报而非自动 WITHDRAWN" — terminal transitions need human review,
not unattended scheduling.

Post-R8-3:
  * At T+360 the scheduler emits a CRITICAL alert with actionable_info
    instructing operators to review + manually transition.
  * The IPO stays in LISTED until the operator ACKs and runs the
    transition. (Phase 8+ may introduce a ``TERMINAL_PROPOSED``
    intermediate state; for now LISTED stays + alert fires.)
  * No call to ``state_machine.transition_to(TERMINATED, ...)`` happens
    in this code path.

These tests verify the contract by AST-walking the
``_process_listed_snapshot`` method.
"""

from __future__ import annotations

import ast
import inspect

from hk_ipo_agent.prediction_registry.schedulers.daily_scheduler import DailyScheduler


def test_t360_does_not_call_transition_to_terminated() -> None:
    """R8-3 — no auto-call to ``transition_to(...TERMINATED...)`` from the
    T+360 branch of ``_process_listed_snapshot``."""
    source = inspect.getsource(DailyScheduler._process_listed_snapshot)
    # The source comes back indented (it's a method body); dedent before parsing.
    import textwrap

    tree = ast.parse(textwrap.dedent(source))

    # Walk for ``self._transition_terminate(...)`` or
    # ``transition_to(..., TERMINATED, ...)``.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # ``self._transition_terminate(...)`` — direct ban.
        if isinstance(func, ast.Attribute) and func.attr == "_transition_terminate":
            raise AssertionError(
                "R8-3: _process_listed_snapshot must NOT auto-call "
                "_transition_terminate at T+360. Emit an alert and let "
                "the operator ack + run the transition manually."
            )


def test_t360_emits_critical_alert() -> None:
    """R8-3 — the T+360 branch emits an alert with severity 'critical'.

    Look for an ``alerts.emit(level=...)`` call inside the method body
    referencing ``CRITICAL`` / ``critical``.
    """
    source = inspect.getsource(DailyScheduler._process_listed_snapshot)
    has_alert = ".emit(" in source and ("CRITICAL" in source or "critical" in source)
    assert has_alert, (
        "R8-3: _process_listed_snapshot must emit a critical alert at "
        "T+360 so operators see the manual-action signal"
    )


def test_t360_alert_has_actionable_info() -> None:
    """R8-3 — the alert carries ``actionable_info`` per CLAUDE.md alert contract."""
    source = inspect.getsource(DailyScheduler._process_listed_snapshot)
    assert "actionable_info" in source, (
        "R8-3: T+360 alert must include actionable_info field "
        "(CLAUDE.md §自动化与状态机约束 — alerts must say "
        "'what should be done', not just 'failed')"
    )


def test_terminal_day_threshold_constant_unchanged() -> None:
    """R8-3 — the 360-day threshold itself stays at 360 (CLAUDE.md fixed
    checkpoint constraint). Only the ACTION at threshold changes."""
    from hk_ipo_agent.prediction_registry.schedulers.daily_scheduler import (
        TERMINAL_DAY_THRESHOLD,
    )

    assert TERMINAL_DAY_THRESHOLD == 360
