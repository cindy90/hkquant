"""Look-ahead leak defense tests (ADR 0005 §4 — migrated from NACS).

The original NACS test at `tests/test_no_lookahead.py` (legacy) guarded
`get_financials(asof)` against returning future-dated rows. The same invariant
now applies to `FinancialSnapshotRow.fiscal_year` filtering — any function
that loads financials at an as_of_date MUST not return rows whose fiscal
period ends on or after that date.

This file tests the invariant against a small in-memory list of dicts (no
DB needed). Phase 8 backtest/as_of_data.AsOfDataProvider will own the
canonical filter; this test pins the principle.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TypedDict

import pytest


class _Snap(TypedDict, total=False):
    stock_code: str
    fiscal_year: int
    fiscal_period: str
    period_end: date | None
    revenue_rmb: Decimal


def visible_financials_at(
    snapshots: list[_Snap], stock_code: str, as_of: date
) -> list[_Snap]:
    """Reference implementation of the as-of filter (NACS rule preserved).

    For NACS-style financials with only fiscal_year (no period_end), the rule
    is: fiscal_year < as_of.year. When period_end is present (post-Phase 2
    iFind ingest), use period_end < as_of instead.
    """
    out: list[_Snap] = []
    for s in snapshots:
        if s.get("stock_code") != stock_code:
            continue
        period_end = s.get("period_end")
        if period_end is not None:
            if period_end < as_of:
                out.append(s)
        elif s["fiscal_year"] < as_of.year:
            out.append(s)
    return out


@pytest.fixture
def synthetic_snaps() -> list[_Snap]:
    return [
        {"stock_code": "0001.HK", "fiscal_year": 2022, "revenue_rmb": Decimal("100")},
        {"stock_code": "0001.HK", "fiscal_year": 2023, "revenue_rmb": Decimal("150")},
        {"stock_code": "0001.HK", "fiscal_year": 2024, "revenue_rmb": Decimal("200")},
        {"stock_code": "0001.HK", "fiscal_year": 2025, "revenue_rmb": Decimal("250")},
        {"stock_code": "0002.HK", "fiscal_year": 2023, "revenue_rmb": Decimal("999")},
    ]


def test_2022_pricing_sees_no_future_years(synthetic_snaps: list[_Snap]) -> None:
    """An IPO pricing in 2022 must not read 2023+ data."""
    visible = visible_financials_at(synthetic_snaps, "0001.HK", date(2022, 1, 11))
    assert all(s["fiscal_year"] < 2022 for s in visible)


def test_2023_pricing_sees_only_2022(synthetic_snaps: list[_Snap]) -> None:
    visible = visible_financials_at(synthetic_snaps, "0001.HK", date(2023, 6, 15))
    years = sorted(s["fiscal_year"] for s in visible)
    assert years == [2022]


def test_2026_pricing_sees_all_past_years(synthetic_snaps: list[_Snap]) -> None:
    visible = visible_financials_at(synthetic_snaps, "0001.HK", date(2026, 5, 16))
    years = sorted(s["fiscal_year"] for s in visible)
    assert years == [2022, 2023, 2024, 2025]


def test_other_stocks_excluded(synthetic_snaps: list[_Snap]) -> None:
    visible = visible_financials_at(synthetic_snaps, "0001.HK", date(2026, 5, 16))
    assert all(s["stock_code"] == "0001.HK" for s in visible)


def test_period_end_takes_precedence_over_fiscal_year() -> None:
    snaps: list[_Snap] = [
        {
            "stock_code": "0001.HK",
            "fiscal_year": 2024,
            "period_end": date(2024, 12, 31),
            "revenue_rmb": Decimal("100"),
        }
    ]
    # As of 2025-01-01 the period ended Dec 31 2024 — visible
    visible = visible_financials_at(snaps, "0001.HK", date(2025, 1, 1))
    assert len(visible) == 1
    # As of 2024-12-31 the period has NOT ended yet — not visible
    visible_at_end = visible_financials_at(snaps, "0001.HK", date(2024, 12, 31))
    assert visible_at_end == []
