"""Event-driven scheduler — fed by external webhooks / poll loops.

Per PROJECT_SPEC.md §3.11.2: this is the "realtime trigger" layer.
Production wires this up to:
- HKEX disclosure RSS / webhook (announcement filings)
- iFind price-anomaly alerts (|d1| > 5% or |d5| > 10%)
- 披露易 (disclosure) ownership-change feed

When a webhook payload arrives the API endpoint calls
``handle_event(payload)`` which routes to the right handler:

| event kind                    | handler                                  |
|-------------------------------|------------------------------------------|
| earnings filing               | EarningsComparator.compare               |
| announcement (state-changing) | StateDetectors + StateMachine            |
| cornerstone disclosure        | (Phase 10 cornerstone tracker)           |
| price anomaly                 | event_detector + maybe critical review   |

The scheduler itself is *not* a poll loop in production — it's invoked
by ``api/streaming`` webhook endpoints. Tests + Airflow dispatch can
call ``run`` (BaseScheduler lifecycle) for a single sweep of a buffered
event queue.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...common.enums import SchedulerType
from ...common.logging import get_logger
from ..earnings_comparator import EarningsComparator, FilingNumbers
from ..ipo_lifecycle import StateMachine
from .base import BaseScheduler, RunStats

logger = get_logger(__name__)


# Supported event kinds — must match what webhook adapters emit.
EVENT_KIND_EARNINGS = "earnings_filing"
EVENT_KIND_PRICE_ANOMALY = "price_anomaly"
EVENT_KIND_CORNERSTONE = "cornerstone_disclosure"
EVENT_KIND_REGULATORY = "regulatory_filing"


@dataclass(frozen=True)
class EventPayload:
    """Normalised webhook payload.

    Adapters in ``api/streaming/`` translate HKEX RSS / iFind anomaly
    JSON / 披露易 feeds into this shape so the scheduler stays decoupled
    from each source's quirks.
    """

    kind: str
    ipo_id: UUID
    occurred_at: datetime
    payload: dict[str, Any]


class _EventQueue(Protocol):
    """Pull buffered events. Production: PG ``realtime_events`` ungrouped
    by ack_at; tests: in-memory list."""

    async def pull(self, limit: int = 100) -> list[EventPayload]: ...

    async def ack(self, event_id: UUID) -> None: ...


class _SnapshotResolver(Protocol):
    async def get_latest_snapshot_id(self, ipo_id: UUID) -> UUID | None: ...


class EventDrivenScheduler(BaseScheduler):
    """Single sweep of the event queue. Bound to ``SchedulerType.EVENT_DRIVEN``."""

    scheduler_type = SchedulerType.EVENT_DRIVEN

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        queue: _EventQueue,
        earnings_comparator: EarningsComparator,
        state_machine: StateMachine,
        snapshot_resolver: _SnapshotResolver,
        alert_router: Any | None = None,
        registry: Any | None = None,
    ) -> None:
        """R8-6: ``registry`` is injected (not pulled from a global).

        Pre-R8-6 ``_handle_earnings`` called the process-wide
        ``get_registry()``, which returns whichever registry was last
        ``set_registry``-d. That made the event-driven scheduler
        dependent on global mutation order — running it alongside a
        FastAPI app whose lifespan installs PGPredictionRegistry would
        make the scheduler silently read from PG even when the
        scheduler was started with an in-memory test registry.

        When ``registry=None`` the handler falls back to
        ``get_registry()`` for back-compat with old call sites.
        """
        super().__init__(session_factory=session_factory)
        self._queue = queue
        self._earnings = earnings_comparator
        self._sm = state_machine
        self._snapshots = snapshot_resolver
        self._alerts = alert_router
        self._registry = registry

    async def do_work(self, stats: RunStats) -> None:
        events = await self._queue.pull()
        for event in events:
            try:
                handled = await self._dispatch(event, stats)
                if handled:
                    stats.events_detected += 1
            except Exception as exc:
                stats.record_error(
                    exc, context={"event_kind": event.kind, "ipo_id": str(event.ipo_id)}
                )

    async def _dispatch(self, event: EventPayload, stats: RunStats) -> bool:
        if event.kind == EVENT_KIND_EARNINGS:
            return await self._handle_earnings(event, stats)
        if event.kind == EVENT_KIND_PRICE_ANOMALY:
            return await self._handle_price_anomaly(event, stats)
        if event.kind == EVENT_KIND_CORNERSTONE:
            return await self._handle_cornerstone(event, stats)
        if event.kind == EVENT_KIND_REGULATORY:
            return await self._handle_regulatory(event, stats)
        logger.warning("event_driven_unknown_kind", kind=event.kind)
        return False

    async def _handle_earnings(self, event: EventPayload, stats: RunStats) -> bool:
        """Translate webhook payload → FilingNumbers → comparator.

        R8-6: registry comes from ``self._registry`` (injected at
        construction). Falls back to ``get_registry()`` for back-compat
        when constructed without an explicit registry.
        """
        snapshot_id = await self._snapshots.get_latest_snapshot_id(event.ipo_id)
        if snapshot_id is None:
            logger.warning("earnings_no_snapshot", ipo_id=str(event.ipo_id))
            return False

        # R8-6: prefer the injected registry; only fall back to global if absent.
        if self._registry is not None:
            registry = self._registry
        else:
            from ..registry import get_registry as _gr

            registry = _gr()

        try:
            snap = await registry.get_snapshot(snapshot_id)
        except KeyError:
            return False
        filing = FilingNumbers(
            report_period=event.payload.get("report_period", "UNKNOWN"),
            filing_date=_parse_date(event.payload.get("filing_date")) or event.occurred_at.date(),
            actual_revenue=_decimal(event.payload.get("actual_revenue")),
            actual_net_profit=_decimal(event.payload.get("actual_net_profit")),
            actual_gross_margin=_decimal(event.payload.get("actual_gross_margin")),
            extra_kpis=event.payload.get("extra_kpis") or {},
        )
        await self._earnings.compare(snapshot=snap, filing=filing)
        return True

    async def _handle_price_anomaly(self, event: EventPayload, stats: RunStats) -> bool:
        """A price-anomaly event already came from event_detector; route to alerts
        if severity is critical."""
        if self._alerts is None:
            return True
        severity = event.payload.get("severity", "minor")
        if severity != "critical":
            return True
        from ...common.enums import AlertLevel

        try:
            await self._alerts.emit(
                level=AlertLevel.CRITICAL,
                category="price_anomaly_critical",
                message=event.payload.get("description", "未知价格异动"),
                actionable_info=(
                    "查看该 snapshot 当前 outcome；如已触发 critical_review，关注 review_draft 处理"
                ),
                related_ipo_id=event.ipo_id,
            )
        except Exception as exc:
            stats.record_error(exc, context={"phase": "alert_route"})
        return True

    async def _handle_cornerstone(self, event: EventPayload, stats: RunStats) -> bool:
        """Cornerstone disclosure → log only (Phase 10 cornerstone tracker
        consumes 披露易 deltas)."""
        logger.info(
            "cornerstone_event",
            ipo_id=str(event.ipo_id),
            payload_keys=list(event.payload.keys()),
        )
        return True

    async def _handle_regulatory(self, event: EventPayload, stats: RunStats) -> bool:
        """Regulatory filing routed to alerts as info-level."""
        if self._alerts is None:
            return True
        from ...common.enums import AlertLevel

        try:
            await self._alerts.emit(
                level=AlertLevel.INFO,
                category="regulatory_filing",
                message=event.payload.get("title", "regulatory filing"),
                actionable_info="人工 review 监管文件，决定是否影响 snapshot 决策",
                related_ipo_id=event.ipo_id,
            )
        except Exception as exc:
            stats.record_error(exc, context={"phase": "alert_route"})
        return True


# Helpers -------------------------------------------------------------


def _parse_date(d: Any) -> date | None:
    if isinstance(d, date):
        return d
    if isinstance(d, str):
        try:
            return date.fromisoformat(d[:10])
        except ValueError:
            return None
    return None


def _decimal(v: Any) -> Any:
    if v is None or v == "":
        return None
    from decimal import Decimal

    try:
        return Decimal(str(v))
    except (TypeError, ValueError):
        return None


# Suppress unused warnings — UTC used in adapter helpers.
_ = UTC


__all__ = (
    "EVENT_KIND_CORNERSTONE",
    "EVENT_KIND_EARNINGS",
    "EVENT_KIND_PRICE_ANOMALY",
    "EVENT_KIND_REGULATORY",
    "EventDrivenScheduler",
    "EventPayload",
)
