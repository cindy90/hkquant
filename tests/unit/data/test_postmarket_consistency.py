"""Test IPOPostMarket double-write consistency per ADR 0007.

When `returns_by_day` JSONB is populated (post-Phase 8 iFind backfill), the
scalar columns (day1/22/126/127/252) MUST match the corresponding JSONB
entries. This file pins the constraint with a helper that ETL writers can
import.
"""

from __future__ import annotations

from decimal import Decimal

import pytest


def assert_postmarket_consistent(
    *,
    scalar: dict[str, Decimal | None],
    jsonb: dict[str, str] | None,
) -> None:
    """Raise AssertionError if scalar columns disagree with JSONB returns_by_day.

    `scalar` should contain at least the spec §5 columns: day1_return,
    day22_return, day126_return, day252_return.

    JSONB shape: {"1": "0.05", "22": "0.12", ...} (str keys, str-encoded Decimals).
    """
    if jsonb is None:
        return  # NACS legacy: scalars only; JSONB unused
    mapping = {
        "day1_return": "1",
        "day22_return": "22",
        "day126_return": "126",
        "day252_return": "252",
    }
    for scalar_col, json_key in mapping.items():
        scalar_val = scalar.get(scalar_col)
        if scalar_val is None:
            continue
        if json_key not in jsonb:
            raise AssertionError(
                f"scalar {scalar_col}={scalar_val} present but jsonb key '{json_key}' missing"
            )
        json_val = Decimal(jsonb[json_key])
        if scalar_val != json_val:
            raise AssertionError(
                f"drift: {scalar_col}={scalar_val} vs returns_by_day['{json_key}']={json_val}"
            )


def test_consistent_when_jsonb_matches_scalars() -> None:
    scalar = {
        "day1_return": Decimal("0.05"),
        "day22_return": Decimal("0.12"),
        "day126_return": Decimal("0.30"),
        "day252_return": Decimal("0.45"),
    }
    jsonb = {"1": "0.05", "22": "0.12", "126": "0.30", "252": "0.45", "10": "0.08"}
    assert_postmarket_consistent(scalar=scalar, jsonb=jsonb)


def test_drift_raises() -> None:
    scalar = {"day1_return": Decimal("0.05")}
    jsonb = {"1": "0.07"}  # mismatch
    with pytest.raises(AssertionError, match="drift"):
        assert_postmarket_consistent(scalar=scalar, jsonb=jsonb)


def test_missing_jsonb_key_when_scalar_present_raises() -> None:
    scalar = {"day22_return": Decimal("0.10")}
    jsonb = {"1": "0.05"}  # no "22" key
    with pytest.raises(AssertionError, match="missing"):
        assert_postmarket_consistent(scalar=scalar, jsonb=jsonb)


def test_nacs_legacy_no_jsonb_passes() -> None:
    """NACS-migrated rows have scalars only; JSONB is None — consistency vacuously holds."""
    scalar = {
        "day1_return": Decimal("0.05"),
        "day22_return": Decimal("0.12"),
        "day126_return": Decimal("0.30"),
        "day252_return": Decimal("0.45"),
    }
    assert_postmarket_consistent(scalar=scalar, jsonb=None)


def test_null_scalars_dont_require_jsonb_entries() -> None:
    """If a scalar is None, we don't enforce a JSONB key for it."""
    scalar = {
        "day1_return": Decimal("0.05"),
        "day22_return": None,  # null — not asserted
        "day126_return": Decimal("0.30"),
    }
    jsonb = {"1": "0.05", "126": "0.30"}  # no "22" key, OK
    assert_postmarket_consistent(scalar=scalar, jsonb=jsonb)
