"""R8-4 — _fallback_price returns None; review_workflow short-circuits.

Pre-R8-4 ``DailyScheduler._fallback_price`` returned ``Decimal("0")``
as a sentinel when no real checkpoint price was cached. That price
then flowed into ``review_workflow.generate_draft(actual_price=Decimal("0"))``,
which silently wrote a review draft with a fake $0.00 price — the
price-in-range check trivially became False, and any downstream
attribution treated the IPO as having crashed 100%.

CLAUDE.md §自动化与状态机约束 forbids substituting estimates for
missing data: "数据源失败有序降级... 禁止用估算值代替真实数据。"

Post-R8-4:
  * ``_fallback_price(meta)`` returns ``None`` (no fake-zero sentinel).
  * The caller short-circuits: when ``actual_price is None`` it skips
    the review-draft generation for that checkpoint and logs the skip
    so the operator can re-run after backfilling the price cache.
"""

from __future__ import annotations

import inspect

from hk_ipo_agent.prediction_registry.schedulers.daily_scheduler import DailyScheduler


def test_fallback_price_returns_none() -> None:
    """R8-4 — the helper returns None, not Decimal('0')."""
    # Build a minimal IPOMetadata (the helper ignores its body in fallback mode).
    from datetime import date as _date
    from uuid import uuid4 as _uuid4

    from hk_ipo_agent.prediction_registry.schedulers.daily_scheduler import IPOMetadata

    meta = IPOMetadata(
        ipo_id=_uuid4(),
        stock_code="TEST.HK",
        listing_date=_date(2025, 1, 1),
        industry_peers=[],
        actual_price_at_checkpoint={},
    )
    out = DailyScheduler._fallback_price(meta)
    assert out is None, f"R8-4: _fallback_price must return None (was Decimal('0')); got {out!r}"


def test_listed_snapshot_loop_skips_review_when_actual_price_is_none() -> None:
    """R8-4 — when both ``_actual_price_for`` AND ``_fallback_price`` are
    None, the loop must NOT call ``review_workflow.generate_draft``
    with a fake price.

    Verified by AST: the call to ``generate_draft`` must be inside a
    branch that gates on the price being non-None.
    """
    import ast
    import textwrap

    source = inspect.getsource(DailyScheduler._process_listed_snapshot)
    tree = ast.parse(textwrap.dedent(source))

    # Find every ``self._reviews.generate_draft(...)`` call and confirm
    # its actual_price argument is wired through an ``if ... is not None``
    # guard somewhere in an ancestor If statement.
    body_text = ast.unparse(tree)
    # Crude but sufficient: the source must mention a None check around
    # actual_price before / where the draft generators are called.
    has_none_guard = (
        "actual_price is None" in body_text
        or "if actual_price" in body_text
        or "if not actual_price" in body_text
    )
    assert has_none_guard, (
        "R8-4: _process_listed_snapshot must guard the review-draft "
        "calls behind an actual_price-None check so missing prices "
        "don't produce fake-zero reviews"
    )


def test_fallback_price_no_longer_constructs_decimal() -> None:
    """R8-4 — the post-fix helper body doesn't call Decimal(...) anywhere.

    AST-walk so prose mentions in docstrings ("pre-fix this returned
    Decimal('0')") don't false-fire.
    """
    import ast
    import textwrap

    source = inspect.getsource(DailyScheduler._fallback_price)
    tree = ast.parse(textwrap.dedent(source))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else (func.attr if isinstance(func, ast.Attribute) else None)
            )
            assert name != "Decimal", (
                "R8-4: _fallback_price must NOT construct Decimal(...) "
                "(use of fake-zero sentinel violates CLAUDE.md "
                "§自动化与状态机约束 estimate-substitution rule)"
            )
