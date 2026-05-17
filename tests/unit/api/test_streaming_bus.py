"""SSE event bus unit tests (no HTTP layer)."""

from __future__ import annotations

import asyncio

import pytest

from hk_ipo_agent.api.streaming.event_bus import EventBus
from hk_ipo_agent.common.enums import RealtimeEventType


@pytest.mark.asyncio
async def test_publish_to_subscribers() -> None:
    bus = EventBus()
    received: list = []

    async def sub() -> None:
        async for evt in bus.subscribe():
            received.append(evt)
            return  # stop after first

    task = asyncio.create_task(sub())
    # Allow subscribe() to register
    await asyncio.sleep(0.01)
    await bus.publish(RealtimeEventType.SCHEDULER_STARTED, payload={"x": 1})
    await asyncio.wait_for(task, timeout=1.0)
    assert len(received) == 1
    assert received[0].event_type == RealtimeEventType.SCHEDULER_STARTED


@pytest.mark.asyncio
async def test_publish_unregistered_raises() -> None:
    bus = EventBus()
    with pytest.raises(ValueError, match="unregistered"):
        # noinspection PyTypeChecker
        await bus.publish("not-an-enum")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_no_subscribers_publish_is_silent() -> None:
    bus = EventBus()
    # Just shouldn't raise.
    await bus.publish(RealtimeEventType.DASHBOARD_REFRESH)
