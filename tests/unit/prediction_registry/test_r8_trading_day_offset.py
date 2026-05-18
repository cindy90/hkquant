"""R8-7 — outcome_tracker uses trading-day offset, not calendar-day offset.

Pre-R8-7 the target date for a T+N checkpoint was
``listing_date + timedelta(days=checkpoint_day)`` — calendar days.
For T+5 starting on a Friday, that's the following Wednesday (5 cal
days = 5 weekday/weekend days). The HK exchange is closed Sat/Sun
so the "T+5 trading day" should be the following Friday.

The mismatch produces small but real errors in the realized-return
computation: the spec's CHECKPOINT_DAYS are documented as trading-day
indices, not calendar.

Post-R8-7:
  * ``BenchmarkPriceService.get_trading_day_offset(start, n)``: skip
    weekends to approximate the HK trading calendar. (HKEX-holiday
    accuracy is Phase 9; weekend skip is the load-bearing 5/7 share.)
  * ``OutcomeTracker.track`` computes target_date via the helper.

Test contract: the helper skips Sat+Sun and the tracker source uses it.
"""

from __future__ import annotations

import inspect
from datetime import date

from hk_ipo_agent.prediction_registry.benchmarks import BenchmarkPriceService


def test_get_trading_day_offset_helper_exists() -> None:
    """R8-7 — BenchmarkPriceService exposes get_trading_day_offset."""
    assert hasattr(BenchmarkPriceService, "get_trading_day_offset"), (
        "R8-7: BenchmarkPriceService must expose get_trading_day_offset"
    )


def test_trading_day_offset_skips_weekend_from_friday() -> None:
    """R8-7 — Friday + 1 trading day = next Monday (skip Sat/Sun)."""
    # 2026-05-15 is a Friday.
    friday = date(2026, 5, 15)
    out = BenchmarkPriceService.get_trading_day_offset(friday, 1)
    assert out == date(2026, 5, 18), f"expected Mon 2026-05-18, got {out}"


def test_trading_day_offset_skips_two_weekends_for_five_trading_days() -> None:
    """R8-7 — 5 trading days from Monday lands on the next Monday (skip 1 weekend)."""
    monday = date(2026, 5, 11)
    out = BenchmarkPriceService.get_trading_day_offset(monday, 5)
    # Mon T0 → Tue T+1 → Wed T+2 → Thu T+3 → Fri T+4 → Mon T+5
    assert out == date(2026, 5, 18), f"expected next-Mon 2026-05-18, got {out}"


def test_trading_day_offset_zero_is_identity() -> None:
    """R8-7 — n=0 returns the start date unchanged."""
    d = date(2026, 5, 11)
    assert BenchmarkPriceService.get_trading_day_offset(d, 0) == d


def test_trading_day_offset_handles_starting_on_saturday() -> None:
    """R8-7 — start on Saturday + 1 trading day = Monday."""
    saturday = date(2026, 5, 16)
    out = BenchmarkPriceService.get_trading_day_offset(saturday, 1)
    assert out == date(2026, 5, 18)


def test_outcome_tracker_uses_trading_day_offset() -> None:
    """R8-7 — OutcomeTracker.track source references trading-day offset.

    AST not strictly needed; substring check is enough. The pre-fix
    line was ``listing_date + timedelta(days=...)``; post-fix it
    delegates to the new helper.
    """
    from hk_ipo_agent.prediction_registry.outcome_tracker import OutcomeTracker

    source = inspect.getsource(OutcomeTracker.track)
    assert "get_trading_day_offset" in source, (
        "R8-7: OutcomeTracker.track must compute target_date via "
        "BenchmarkPriceService.get_trading_day_offset"
    )
