"""R8-2 — load_backtest_inputs_from_pg fills cornerstone_count from PG.

Pre-R8-2 the loader unconditionally wrote ``cornerstone_count=0`` with
the comment "filled by caller if needed". No caller actually filled it,
so the V8LiteScorer's cluster bonus
(``min(cornerstone_count * cluster_unit, cluster_cap)``) was always 0
in backtests. ADR 0005 §2's Cluster Bonus signal — the load-bearing
empirical edge (cluster≥2 IPOs: 60d mean +22% vs +14%) — was silently
zeroed out across all 374 historical samples.

Post-R8-2 the loader queries ``cornerstone_investments`` grouped by
``ipo_id`` and writes the real count into each ``BacktestInput``. We
verify by source inspection (PG-required for a real end-to-end test;
lives in integration).
"""

from __future__ import annotations

import ast
import inspect

from hk_ipo_agent.backtest.runner import load_backtest_inputs_from_pg


def test_load_backtest_inputs_no_longer_hard_codes_cornerstone_count_zero() -> None:
    """R8-2 — the function source no longer contains ``cornerstone_count=0``
    as a hard-coded literal at the BacktestInput construction site."""
    source = inspect.getsource(load_backtest_inputs_from_pg)
    tree = ast.parse(source)

    # Find every ``BacktestInput(...)`` Call and check its cornerstone_count kwarg.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.id
            if isinstance(func, ast.Name)
            else (func.attr if isinstance(func, ast.Attribute) else None)
        )
        if name != "BacktestInput":
            continue
        for kw in node.keywords:
            if kw.arg == "cornerstone_count" and isinstance(kw.value, ast.Constant):
                assert kw.value.value != 0, (
                    "R8-2: BacktestInput(cornerstone_count=0) hard-coded — must "
                    "be filled from cornerstone_investments via PG query"
                )


def test_load_backtest_inputs_queries_cornerstone_investments() -> None:
    """R8-2 — the source body references CornerstoneInvestment (the ORM
    class) so the cornerstone count actually comes from PG."""
    source = inspect.getsource(load_backtest_inputs_from_pg)
    assert "CornerstoneInvestment" in source, (
        "R8-2: load_backtest_inputs_from_pg must query the "
        "cornerstone_investments table to fill cornerstone_count"
    )


def test_load_backtest_inputs_uses_group_or_count() -> None:
    """R8-2 — the query uses ``func.count`` / ``group_by`` (not a per-IPO
    N+1 loop), so loading 374 samples doesn't trigger 374 round-trips.

    Acceptable patterns: ``func.count(``, ``.group_by(``, or a single
    bulk ``select`` followed by a Python-side ``Counter``-style aggregation.
    """
    source = inspect.getsource(load_backtest_inputs_from_pg)
    has_aggregation = (
        "func.count(" in source
        or ".group_by(" in source
        or "Counter(" in source
        or "defaultdict" in source
    )
    assert has_aggregation, (
        "R8-2: cornerstone counting must use SQL aggregation or a single "
        "bulk fetch + Python aggregation — not a per-IPO query"
    )
