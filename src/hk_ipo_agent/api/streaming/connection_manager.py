"""SSE connection manager — heartbeat + format helpers per PROJECT_SPEC.md §16.3."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from ...common.schemas import RealtimeEvent
from .event_bus import get_event_bus

HEARTBEAT_SECONDS: float = 15.0


def format_sse(event: RealtimeEvent) -> str:
    """Format a ``RealtimeEvent`` as an SSE message frame."""
    data = event.model_dump(mode="json")
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event.event_type.value}\ndata: {payload}\n\n"


async def stream_events() -> AsyncIterator[str]:
    """Async generator yielding SSE-formatted strings.

    Multiplexes bus events + heartbeat so neither starves the other.
    """
    bus = get_event_bus()
    sub_iter = bus.subscribe()

    async def next_event() -> RealtimeEvent | None:
        try:
            return await sub_iter.__anext__()
        except StopAsyncIteration:
            return None

    while True:
        try:
            evt = await asyncio.wait_for(next_event(), timeout=HEARTBEAT_SECONDS)
        except TimeoutError:
            yield ":heartbeat\n\n"
            continue
        if evt is None:
            break
        yield format_sse(evt)


__all__ = ("HEARTBEAT_SECONDS", "format_sse", "stream_events")
