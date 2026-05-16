"""SSE event type registry per PROJECT_SPEC.md §16.3 + CLAUDE.md UI 集成约束.

CLAUDE.md HARD constraint: only events registered in this module may be
emitted. Add new types here AND in ``common/enums.py::RealtimeEventType``.
"""

from __future__ import annotations

from ...common.enums import RealtimeEventType

REGISTERED_EVENTS: frozenset[RealtimeEventType] = frozenset(RealtimeEventType)


def is_registered(event_type: RealtimeEventType) -> bool:
    """Return True iff ``event_type`` is a known SSE event."""
    return event_type in REGISTERED_EVENTS


__all__ = ("REGISTERED_EVENTS", "is_registered")
