"""R5-6 — Decimal fields serialize to JSON as string for JS-safe precision.

CLAUDE.md §UI 集成约束 §13: money / decimal-typed fields on the wire
must be string-encoded so a JS client doesn't lose precision on the
17th significant digit. Pre-R5-6 StrictModel's default serializer used
Pydantic's number encoding, dropping precision when Decimal('1.000000001')
was emitted to JSON as 1.000000001 (which JS rounds to 1.0000000010).
"""

from __future__ import annotations

import json
from decimal import Decimal

from hk_ipo_agent.common.schemas import StrictModel


class _Money(StrictModel):
    """Test model exercising Decimal serialization."""

    amount: Decimal
    label: str = "USD"
    note: float = 0.5
    optional_amount: Decimal | None = None


def test_decimal_serializes_as_string_in_model_dump_json() -> None:
    """R5-6 — primary contract: model_dump_json emits Decimal as string."""
    m = _Money(amount=Decimal("12.345"))
    data = json.loads(m.model_dump_json())
    assert data["amount"] == "12.345"
    assert isinstance(data["amount"], str)


def test_decimal_serializes_as_string_preserves_full_precision() -> None:
    """R5-6 — 17-digit-edge case (where JS Number loses precision)."""
    m = _Money(amount=Decimal("1.000000000000000001"))
    data = json.loads(m.model_dump_json())
    assert data["amount"] == "1.000000000000000001"


def test_non_decimal_fields_unaffected_by_serializer() -> None:
    """R5-6 — str / float / int fields untouched."""
    m = _Money(amount=Decimal("1"), label="HKD", note=0.5)
    data = json.loads(m.model_dump_json())
    assert data["label"] == "HKD"
    assert data["note"] == 0.5


def test_none_decimal_serializes_as_null() -> None:
    """R5-6 — None remains null; the serializer only kicks in on Decimal."""
    m = _Money(amount=Decimal("1"), optional_amount=None)
    data = json.loads(m.model_dump_json())
    assert data["optional_amount"] is None


def test_decimal_in_python_mode_dump_unchanged() -> None:
    """R5-6 — ``model_dump(mode='python')`` keeps Decimal native (only JSON path is affected)."""
    m = _Money(amount=Decimal("12.345"))
    py = m.model_dump(mode="python")
    assert isinstance(py["amount"], Decimal)
    assert py["amount"] == Decimal("12.345")
