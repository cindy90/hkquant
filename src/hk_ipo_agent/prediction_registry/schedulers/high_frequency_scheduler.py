"""High-frequency scheduler — runs every 15-30 min.

Per PROJECT_SPEC.md §3.11.2 + CLAUDE.md v1.2 ("high_freq 严禁做归因、
回测等重活"): this scheduler does ONLY lightweight scans:

1. Iterate active (non-terminal) lifecycle rows
2. Run state detectors (PRICING / LISTED 3-way / WITHDRAWN /
   HEARING_FAILED) — these only read external APIs, never write
   prediction_outcomes
3. Transition state via state_machine on signal
4. Resolve code_mapper on LISTED transition
5. Run event_detector with lookback=2h (event-driven supplementary,
   for IPOs that didn't get a webhook)

Anything heavy (outcome tracker, attribution, full event scan) is the
daily_scheduler's job.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...common.enums import IPOLifecycleStateType, SchedulerType, TransitionTrigger
from ...common.exceptions import InvalidStateTransition
from ...common.logging import get_logger
from ...data.models import IPOLifecycleStateRow
from ..ipo_lifecycle import StateDetectors, StateMachine, ThreeWayValidation
from .base import BaseScheduler, RunStats

logger = get_logger(__name__)

DEFAULT_LOOKBACK = timedelta(hours=2)


@dataclass(frozen=True)
class ActiveIPOContext:
    """Per-IPO info the scheduler needs to feed detectors."""

    ipo_id: UUID
    stock_code: str | None
    expected_listing_date: Any | None  # datetime.date


class _LifecycleIPORepo(Protocol):
    """Caller-provided source of per-IPO metadata not in lifecycle_states."""

    async def get_context(self, ipo_id: UUID) -> ActiveIPOContext | None: ...


class HighFrequencyScheduler(BaseScheduler):
    """Lightweight scan over active IPOs. Bound to ``SchedulerType.HIGH_FREQ``."""

    scheduler_type = SchedulerType.HIGH_FREQ

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        state_machine: StateMachine,
        state_detectors: StateDetectors,
        ipo_repo: _LifecycleIPORepo,
        code_mapper: Any | None = None,
        event_detector: Any | None = None,
        lookback: timedelta = DEFAULT_LOOKBACK,
    ) -> None:
        super().__init__(session_factory=session_factory)
        self._sm = state_machine
        self._detectors = state_detectors
        self._repo = ipo_repo
        self._code_mapper = code_mapper
        self._event_detector = event_detector
        self._lookback = lookback

    async def do_work(self, stats: RunStats) -> None:
        active = await self._list_active()
        for row in active:
            ctx = await self._repo.get_context(row.ipo_id)
            if ctx is None or ctx.stock_code is None:
                # Pre-listing without a code yet — touch and skip.
                await self._sm.touch_last_checked(row.ipo_id)
                continue
            state = IPOLifecycleStateType(row.current_state)
            try:
                advanced = await self._advance_state(state, ctx, stats)
            except Exception as exc:
                stats.record_error(
                    exc, context={"ipo_id": str(row.ipo_id), "phase": "advance_state"}
                )
                continue
            if advanced:
                stats.snapshots_processed += 1
            await self._scan_recent_events(ctx, stats)
            await self._sm.touch_last_checked(row.ipo_id)

    async def _list_active(self) -> list[IPOLifecycleStateRow]:
        stmt = select(IPOLifecycleStateRow).where(IPOLifecycleStateRow.is_terminal.is_(False))
        async with self._sf() as s:
            return list((await s.execute(stmt)).scalars().all())

    async def _advance_state(
        self,
        current_state: IPOLifecycleStateType,
        ctx: ActiveIPOContext,
        stats: RunStats,
    ) -> bool:
        """Run the appropriate detector for ``current_state``; transition on signal."""
        if current_state is IPOLifecycleStateType.PRE_LISTING:
            # Detect: pricing / withdrawn / hearing failed.
            for detector_name in ("detect_pricing", "detect_withdrawn", "detect_hearing_failed"):
                sig = await getattr(self._detectors, detector_name)(
                    ctx.ipo_id,
                    stock_code=ctx.stock_code or "",
                )
                if sig is not None:
                    return await self._do_transition(
                        ctx.ipo_id, sig.target_state, sig.evidence, sig.triggered_by
                    )
            return False
        if current_state is IPOLifecycleStateType.PRICING:
            # Detect: LISTED (three-way) / withdrawn / pricing_pulled signal via withdrawn.
            three_way: ThreeWayValidation = await self._detectors.detect_listed_three_way(
                ctx.ipo_id,
                stock_code=ctx.stock_code or "",
                expected_listing_date=ctx.expected_listing_date,
            )
            if three_way.passed:
                return await self._do_transition(
                    ctx.ipo_id,
                    IPOLifecycleStateType.LISTED,
                    three_way.evidence,
                    TransitionTrigger.AUTO_DETECTOR,
                )
            sig = await self._detectors.detect_withdrawn(
                ctx.ipo_id, stock_code=ctx.stock_code or ""
            )
            if sig is not None:
                return await self._do_transition(
                    ctx.ipo_id, sig.target_state, sig.evidence, sig.triggered_by
                )
            return False
        # LISTED → nothing to advance here (daily_scheduler handles terminate-at-360d).
        return False

    async def _do_transition(
        self,
        ipo_id: UUID,
        new_state: IPOLifecycleStateType,
        evidence: dict[str, Any],
        triggered_by: TransitionTrigger,
    ) -> bool:
        try:
            await self._sm.transition_to(
                ipo_id,
                new_state,
                triggered_by=triggered_by,
                evidence=evidence,
            )
        except InvalidStateTransition as exc:
            logger.warning(
                "high_freq_invalid_transition",
                ipo_id=str(ipo_id),
                target=new_state.value,
                error=str(exc),
            )
            return False
        # Resolve code mapping when LISTED — the code is now public.
        if new_state is IPOLifecycleStateType.LISTED and self._code_mapper is not None:
            try:
                mapping = await self._code_mapper.resolve(
                    ipo_id=ipo_id,
                    company_name_zh=evidence.get("company_name_zh", ""),
                )
                await self._code_mapper.save(mapping)
            except Exception as exc:
                logger.warning("code_mapping_failed", ipo_id=str(ipo_id), error=str(exc))
        return True

    async def _scan_recent_events(self, ctx: ActiveIPOContext, stats: RunStats) -> None:
        """Best-effort: pulls a small lookback window of events."""
        if self._event_detector is None or ctx.stock_code is None:
            return
        window_end = datetime.now(UTC).date()
        window_start = (datetime.now(UTC) - self._lookback).date()
        try:
            events = await self._event_detector.scan_events(
                ipo_id=ctx.ipo_id,
                stock_code=ctx.stock_code,
                window_start=window_start,
                window_end=window_end,
            )
            stats.events_detected += len(events)
        except Exception as exc:
            stats.record_error(exc, context={"ipo_id": str(ctx.ipo_id), "phase": "event_scan"})


__all__ = (
    "DEFAULT_LOOKBACK",
    "ActiveIPOContext",
    "HighFrequencyScheduler",
)
