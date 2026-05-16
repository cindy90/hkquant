"""Three-tier scheduler per PROJECT_SPEC.md §3.11.2 + ADR 0012 §7.5d.

- ``BaseScheduler`` — abstract with pg_try_advisory_lock + scheduler_runs lifecycle
- ``HighFrequencyScheduler`` — every 15-30 min, lightweight state scan
- ``DailyScheduler`` — every night, outcome tracking + reviews + stale + terminate
- ``EventDrivenScheduler`` — webhook-fed, routes earnings/anomaly/cornerstone events

CLAUDE.md v1.2 invariants enforced here:
- high_freq does NOT run outcome tracking / attribution
- daily does NOT process realtime events (event_driven's job)
- overlapping runs of the same scheduler type are blocked by advisory lock
"""

from .base import BaseScheduler, RunResult, RunStats
from .daily_scheduler import TERMINAL_DAY_THRESHOLD, DailyScheduler, IPOMetadata
from .event_driven_scheduler import (
    EVENT_KIND_CORNERSTONE,
    EVENT_KIND_EARNINGS,
    EVENT_KIND_PRICE_ANOMALY,
    EVENT_KIND_REGULATORY,
    EventDrivenScheduler,
    EventPayload,
)
from .high_frequency_scheduler import (
    DEFAULT_LOOKBACK,
    ActiveIPOContext,
    HighFrequencyScheduler,
)

__all__ = (
    "DEFAULT_LOOKBACK",
    "EVENT_KIND_CORNERSTONE",
    "EVENT_KIND_EARNINGS",
    "EVENT_KIND_PRICE_ANOMALY",
    "EVENT_KIND_REGULATORY",
    "TERMINAL_DAY_THRESHOLD",
    "ActiveIPOContext",
    "BaseScheduler",
    "DailyScheduler",
    "EventDrivenScheduler",
    "EventPayload",
    "HighFrequencyScheduler",
    "IPOMetadata",
    "RunResult",
    "RunStats",
)
