"""Generic shared utilities."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID


def utcnow() -> datetime:
    """Current UTC datetime, tz-aware. Use everywhere instead of naive ``datetime.utcnow()``."""
    return datetime.now(UTC)


class _CanonicalJSONEncoder(json.JSONEncoder):
    """JSON encoder that handles Decimal / UUID / datetime / set / frozenset deterministically."""

    def default(self, o: Any) -> Any:  # noqa: PLR0911
        if isinstance(o, Decimal):
            # Stringify Decimal to keep precision (also matches JS-safety guidance §16.x).
            return str(o)
        if isinstance(o, UUID):
            return str(o)
        if isinstance(o, datetime):
            return o.isoformat()
        if isinstance(o, frozenset | set):
            return sorted(o)
        if hasattr(o, "model_dump"):  # Pydantic v2 model
            return o.model_dump(mode="json")
        if isinstance(o, bytes):
            return o.decode("utf-8")
        return super().default(o)


def canonical_json(data: Any) -> str:
    """Return a deterministic JSON encoding (sorted keys, no whitespace).

    Suitable for hashing snapshot payloads — same input → same SHA256.
    """
    return json.dumps(data, cls=_CanonicalJSONEncoder, sort_keys=True, separators=(",", ":"))


def sha256_hex(data: Any) -> str:
    """SHA256 hex digest of a JSON-serializable value via canonical encoding."""
    payload = canonical_json(data).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def coerce_decimal(value: int | float | str | Decimal | None) -> Decimal | None:
    """Best-effort conversion to Decimal preserving precision for str / int inputs."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        # Round-trip via string to avoid float-binary noise.
        return Decimal(str(value))
    return Decimal(value)


def safe_div(numerator: float | Decimal, denominator: float | Decimal) -> float | None:
    """Return ``numerator / denominator`` or None if denominator is zero."""
    try:
        d = float(denominator)
        if d == 0.0:
            return None
        return float(numerator) / d
    except (TypeError, ValueError):
        return None
