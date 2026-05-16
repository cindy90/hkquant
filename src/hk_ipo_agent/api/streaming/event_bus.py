"""In-memory pub/sub event bus per ADR 0011 §Phase 7 MVP.

Per-subscriber ``asyncio.Queue`` fanout. Phase 7.5b-3 adds an optional
PG persistence hook so events land in ``realtime_events`` for replay /
audit while in-process fanout stays fast.  Phase 9 may swap to Redis
Pub/Sub for multi-worker correctness.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import async_sessionmaker

from ...common.enums import RealtimeEventType
from ...common.logging import get_logger
from ...common.schemas import RealtimeEvent
from ...data.models import RealtimeEventRow
from .event_types import is_registered

logger = get_logger(__name__)


class EventBus:
    """In-process fanout + optional PG persistence.

    When ``session_factory`` is provided (production lifespan does this),
    every published event is also INSERTed into ``realtime_events``.
    Persistence failures are logged at WARNING but do NOT block the
    in-process fanout — losing audit history is preferable to losing
    UI subscribers.
    """

    def __init__(
        self,
        *,
        max_queue: int = 1000,
        session_factory: async_sessionmaker | None = None,
    ) -> None:
        self._subscribers: list[asyncio.Queue[RealtimeEvent]] = []
        self._lock = asyncio.Lock()
        self._max_queue = max_queue
        self._sf = session_factory

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
        broadcast_count = 0
        for q in subscribers:
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(event)
                broadcast_count += 1
        if self._sf is not None:
            await self._persist(event, broadcast_count=broadcast_count)

    async def _persist(self, event: RealtimeEvent, *, broadcast_count: int) -> None:
        """Best-effort INSERT into realtime_events; never raises."""
        row = RealtimeEventRow(
            id=uuid4(),
            event_type=event.event_type.value,
            related_ipo_id=event.related_ipo_id,
            related_snapshot_id=event.related_snapshot_id,
            payload=event.payload,
            created_at=event.created_at,
            broadcast_count=broadcast_count,
        )
        try:
            async with self._sf() as s:  # type: ignore[misc]
                s.add(row)
                await s.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "event_bus_persist_failed",
                event_type=event.event_type.value, error=str(exc),
            )

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


def set_event_bus(bus: EventBus) -> None:
    """Replace the process-wide event bus — called from FastAPI lifespan."""
    _default_bus.clear()
    _default_bus.append(bus)


def reset_event_bus_for_test() -> None:
    _default_bus.clear()


__all__ = (
    "EventBus",
    "get_event_bus",
    "reset_event_bus_for_test",
    "set_event_bus",
)
