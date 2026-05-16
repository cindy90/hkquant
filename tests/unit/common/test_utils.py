"""Tests for `hk_ipo_agent.common.utils`."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import BaseModel

from hk_ipo_agent.common.utils import (
    canonical_json,
    coerce_decimal,
    safe_div,
    sha256_hex,
    utcnow,
)


def test_utcnow_is_tz_aware() -> None:
    assert utcnow().tzinfo is UTC


def test_canonical_json_sorted_keys() -> None:
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'


def test_canonical_json_encodes_decimal_as_string() -> None:
    assert canonical_json({"x": Decimal("1.50")}) == '{"x":"1.50"}'


def test_canonical_json_encodes_uuid_and_datetime() -> None:
    u = UUID("00000000-0000-0000-0000-000000000000")
    d = datetime(2026, 5, 16, 14, 30, 0, tzinfo=UTC)
    assert (
        canonical_json({"u": u, "d": d})
        == '{"d":"2026-05-16T14:30:00+00:00","u":"00000000-0000-0000-0000-000000000000"}'
    )


def test_sha256_hex_deterministic() -> None:
    a = sha256_hex({"x": 1, "y": [1, 2, 3]})
    b = sha256_hex({"y": [1, 2, 3], "x": 1})
    assert a == b
    assert len(a) == 64  # SHA256 hex digest length


def test_sha256_hex_sensitive_to_value_change() -> None:
    assert sha256_hex({"x": 1}) != sha256_hex({"x": 2})


@pytest.mark.parametrize(
    ("inp", "expected"),
    [
        (None, None),
        (Decimal("3.14"), Decimal("3.14")),
        (1, Decimal("1")),
        (1.5, Decimal("1.5")),
        ("2.718", Decimal("2.718")),
    ],
)
def test_coerce_decimal(inp: object, expected: Decimal | None) -> None:
    assert coerce_decimal(inp) == expected  # type: ignore[arg-type]


def test_safe_div_handles_zero() -> None:
    assert safe_div(10, 0) is None
    assert safe_div(10, 2) == 5.0
    assert safe_div(Decimal("5"), Decimal("2")) == 2.5


def test_canonical_json_encodes_bytes_as_str() -> None:
    """_CanonicalJSONEncoder bytes branch: decode as UTF-8."""
    assert canonical_json({"raw": b"hello"}) == '{"raw":"hello"}'


def test_canonical_json_encodes_pydantic_model_via_model_dump() -> None:
    """_CanonicalJSONEncoder Pydantic model branch: use model_dump(mode='json')."""

    class _Toy(BaseModel):
        x: int
        y: str

    toy = _Toy(x=7, y="hi")
    # canonical_json should serialize via toy.model_dump(mode='json')
    out = canonical_json({"obj": toy})
    assert out == '{"obj":{"x":7,"y":"hi"}}'


def test_canonical_json_encodes_set_and_frozenset() -> None:
    """_CanonicalJSONEncoder set/frozenset branch: sorted list."""
    assert canonical_json({"s": {3, 1, 2}}) == '{"s":[1,2,3]}'
    assert canonical_json({"fs": frozenset(["b", "a"])}) == '{"fs":["a","b"]}'
