"""In-memory pub/sub event bus per ADR 0011 §Phase 7 MVP.

Per-subscriber ``asyncio.Queue`` fanout. Phase 7.5 may swap to
Redis Pub/Sub for multi-worker correctness.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from ...common.enums import RealtimeEventType
from ...common.schemas import RealtimeEvent
from .event_types import is_registered


class EventBus:
    """In-process fanout. Subscribers receive ``RealtimeEvent`` instances."""

    def __init__(self, *, max_queue: int = 1000) -> None:
        self._subscribers: list[asyncio.Queue[RealtimeEvent]] = []
        self._lock = asyncio.Lock()
        self._max_queue = max_queue

    async def publish(
        self,
        event_type: RealtimeEventType,
        *,
        payload: dict[str, Any] | None = None,
        related_ipo_id: Any = None,
        related_snapshot_id: Any = None,
    ) -> None:
        if not is_registered(event_type):
            raise ValueError(f"unregistered SSE event type: {event_type}")
        event = RealtimeEvent(
            event_type=event_type,
            related_ipo_id=related_ipo_id,
            related_snapshot_id=related_snapshot_id,
            payload=payload or {},
            created_at=datetime.now(UTC),
        )
        async with self._lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(event)

    async def subscribe(self) -> AsyncIterator[RealtimeEvent]:
        """Yield events until cancelled. Caller responsible for closing."""
        queue: asyncio.Queue[RealtimeEvent] = asyncio.Queue(maxsize=self._max_queue)
        async with self._lock:
            self._subscribers.append(queue)
        try:
            while True:
                event = await queue.get()
                yield event
        finally:
            async with self._lock:
                self._subscribers.remove(queue)


_default_bus: list[EventBus] = []


def get_event_bus() -> EventBus:
    if not _default_bus:
        _default_bus.append(EventBus())
    return _default_bus[0]


def reset_event_bus_for_test() -> None:
    _default_bus.clear()


__all__ = (
    "EventBus",
    "get_event_bus",
    "reset_event_bus_for_test",
)
