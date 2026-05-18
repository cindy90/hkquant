"""Daily scheduler — runs every night (02:00-03:00 HKT).

The heavy lifting per PROJECT_SPEC.md §3.11.2:

1. For every LISTED snapshot: compute ``days_since_listing`` and call
   ``outcome_tracker.track`` for each newly-reached CHECKPOINT_DAYS day
2. Major-checkpoint review_workflow trigger (T+30 / 90 / 180 / 360)
3. Critical-loss review (decision wrong + |actual| > -20%)
4. ``stale_detector.scan`` for silent-expiry alerts
5. 360-day terminate: transition LISTED → TERMINATED
6. Back-fill missed checkpoints (catch-up after scheduler downtime)
7. Terminal-state handlers: WITHDRAWN / HEARING_FAILED / PRICING_PULLED
   → terminal_review_draft + checkpoint_day=-1 outcome
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...common.enums import (
    CHECKPOINT_DAYS,
    IPOLifecycleStateType,
    SchedulerType,
    TransitionTrigger,
)
from ...common.logging import get_logger
from ...data.models import IPOLifecycleStateRow, PredictionSnapshotRow
from ..ipo_lifecycle import StaleDetector, StateMachine, TerminalHandler, days_in_state
from ..outcome_tracker import OutcomeTracker, TrackResult
from ..review_workflow import MAJOR_CHECKPOINTS, ReviewWorkflow
from .base import BaseScheduler, RunStats

logger = get_logger(__name__)

TERMINAL_DAY_THRESHOLD = 360  # Days post-listing → transition to TERMINATED.


@dataclass(frozen=True)
class IPOMetadata:
    """Slim view of an IPO row used by the daily loop."""

    ipo_id: UUID
    stock_code: str
    listing_date: date
    industry_peers: list[str]
    actual_price_at_checkpoint: dict[int, Any]  # optional cached prices


class _IPOMetadataRepo(Protocol):
    async def get_metadata(self, ipo_id: UUID) -> IPOMetadata | None: ...


class DailyScheduler(BaseScheduler):
    """Heavy-work daily run. Bound to ``SchedulerType.DAILY``."""

    scheduler_type = SchedulerType.DAILY

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        state_machine: StateMachine,
        outcome_tracker: OutcomeTracker,
        review_workflow: ReviewWorkflow,
        stale_detector: StaleDetector,
        terminal_handler: TerminalHandler,
        ipo_repo: _IPOMetadataRepo,
        alert_router: Any | None = None,
    ) -> None:
        super().__init__(session_factory=session_factory)
        self._sm = state_machine
        self._tracker = outcome_tracker
        self._reviews = review_workflow
        self._stale = stale_detector
        self._terminal = terminal_handler
        self._repo = ipo_repo
        self._alerts = alert_router

    async def do_work(self, stats: RunStats) -> None:
        # 1-2. Process LISTED snapshots → outcome tracking + major-checkpoint reviews
        listed_pairs = await self._list_listed_snapshots()
        for snap_id, ipo_id in listed_pairs:
            try:
                await self._process_listed_snapshot(snap_id, ipo_id, stats)
            except Exception as exc:
                stats.record_error(
                    exc, context={"snapshot_id": str(snap_id), "phase": "listed_processing"}
                )

        # 3. Terminal-state handlers (WITHDRAWN / HEARING_FAILED / PRICING_PULLED)
        terminal_rows = await self._list_terminal_snapshots_without_outcome()
        for row in terminal_rows:
            try:
                await self._terminal.handle(
                    ipo_id=row.ipo_id,
                    terminal_state=IPOLifecycleStateType(row.current_state),
                )
            except Exception as exc:
                stats.record_error(
                    exc, context={"ipo_id": str(row.ipo_id), "phase": "terminal_handler"}
                )

        # 4. Stale-detector scan
        try:
            signals = await self._stale.scan()
            for sig in signals:
                stats.events_detected += 1
                await self._route_stale(sig)
        except Exception as exc:
            stats.record_error(exc, context={"phase": "stale_detector"})

    async def _process_listed_snapshot(
        self,
        snapshot_id: UUID,
        ipo_id: UUID,
        stats: RunStats,
    ) -> None:
        meta = await self._repo.get_metadata(ipo_id)
        if meta is None:
            return
        today = datetime.now(UTC).date()
        days_listed = (today - meta.listing_date).days

        # R8-3: at T+360, emit a CRITICAL alert and STOP — do NOT auto-terminate.
        # CLAUDE.md §自动化与状态机约束 forbids unattended terminal transitions
        # (e.g. "超时不等于失败"). Pre-R8-3 the scheduler unilaterally called
        # state_machine.transition_to(TERMINATED, ...); now the operator
        # reviews the alert + manually runs the transition. The IPO stays
        # LISTED until that happens.
        if days_listed >= TERMINAL_DAY_THRESHOLD:
            if self._alerts is not None:
                await self._alerts.emit(
                    level="critical",
                    category="t_plus_360_terminate_proposed",
                    message=(
                        f"IPO {ipo_id} reached T+{days_listed} days post-listing — "
                        f"proposed for TERMINATED transition. Pre-R8-3 this would "
                        f"have auto-transitioned without review."
                    ),
                    actionable_info=(
                        "Review the IPO's final-day outcome + cornerstone activity, "
                        "then either manually transition to TERMINATED via the state "
                        "machine UI OR extend tracking by adjusting the IPO's "
                        "listing_date checkpoint. Do NOT let the daily scheduler "
                        "make this decision unattended."
                    ),
                    related_ipo_id=ipo_id,
                )
            else:
                logger.warning(
                    "t360_alert_skipped_no_router",
                    ipo_id=str(ipo_id),
                    days_listed=days_listed,
                )
            stats.snapshots_processed += 1
            return

        # For each canonical checkpoint that has come due (including any
        # missed ones), run outcome_tracker. Idempotency is enforced by
        # the UNIQUE (snapshot_id, checkpoint_day) in prediction_outcomes.
        for day in CHECKPOINT_DAYS:
            if day > days_listed:
                break  # not yet due — break early (CHECKPOINT_DAYS is sorted)
            result: TrackResult = await self._tracker.track(
                snapshot_id=snapshot_id,
                checkpoint_day=day,
                stock_code=meta.stock_code,
                listing_date=meta.listing_date,
                industry_peers=meta.industry_peers,
                ipo_id=ipo_id,
            )
            if not result.skipped:
                stats.snapshots_processed += 1
                # R8-4: resolve actual_price once; short-circuit ALL review
                # draft generation when the price is None (no cached price
                # AND _fallback_price now returns None instead of fake $0).
                actual_price = self._actual_price_for(meta, day) or self._fallback_price(meta)
                if actual_price is None:
                    logger.info(
                        "review_draft_skipped_no_price",
                        ipo_id=str(ipo_id),
                        snapshot_id=str(snapshot_id),
                        checkpoint_day=day,
                        hint=(
                            "actual_price unavailable; backfill the price cache "
                            "(BenchmarkPriceService) then re-run the daily scheduler"
                        ),
                    )
                    continue
                # Major checkpoint → generate review draft
                if day in MAJOR_CHECKPOINTS:
                    await self._reviews.generate_draft(
                        snapshot_id=snapshot_id,
                        checkpoint_day=day,
                        actual_price=actual_price,
                    )
                # Critical-loss bypass at any checkpoint
                await self._reviews.generate_critical_draft_if_needed(
                    snapshot_id=snapshot_id,
                    checkpoint_day=day,
                    actual_price=actual_price,
                )

    async def _transition_terminate(self, ipo_id: UUID) -> None:
        try:
            await self._sm.transition_to(
                ipo_id,
                IPOLifecycleStateType.TERMINATED,
                triggered_by=TransitionTrigger.AUTO_DETECTOR,
                evidence={"reason": "reached_t_plus_360"},
            )
        except Exception as exc:
            logger.warning("terminate_transition_failed", ipo_id=str(ipo_id), error=str(exc))

    async def _list_listed_snapshots(self) -> list[tuple[UUID, UUID]]:
        """Returns ``[(snapshot_id, ipo_id), ...]`` for all LISTED IPOs."""
        async with self._sf() as s:
            stmt = (
                select(PredictionSnapshotRow.id, PredictionSnapshotRow.ipo_id)
                .join(
                    IPOLifecycleStateRow,
                    IPOLifecycleStateRow.ipo_id == PredictionSnapshotRow.ipo_id,
                )
                .where(IPOLifecycleStateRow.current_state == IPOLifecycleStateType.LISTED.value)
            )
            rows = (await s.execute(stmt)).all()
            return [(row[0], row[1]) for row in rows]

    async def _list_terminal_snapshots_without_outcome(self) -> list[IPOLifecycleStateRow]:
        """Terminal IPOs (WITHDRAWN/HEARING_FAILED/PRICING_PULLED).

        The terminal handler is idempotent on checkpoint_day=-1 so we
        don't filter further — calling it on already-handled rows is
        a no-op.
        """
        terminal_states = (
            IPOLifecycleStateType.WITHDRAWN.value,
            IPOLifecycleStateType.HEARING_FAILED.value,
            IPOLifecycleStateType.PRICING_PULLED.value,
        )
        async with self._sf() as s:
            stmt = select(IPOLifecycleStateRow).where(
                IPOLifecycleStateRow.current_state.in_(terminal_states)
            )
            return list((await s.execute(stmt)).scalars().all())

    async def _route_stale(self, signal: Any) -> None:
        """Emit a stale_detector signal through the alert router (if any)."""
        if self._alerts is None:
            logger.info(
                "stale_signal",
                ipo_id=str(signal.ipo_id),
                state=signal.state.value,
                days=signal.days_in_state,
            )
            return
        category = (
            "stale_pre_listing"
            if signal.state is IPOLifecycleStateType.PRE_LISTING
            else "stale_pricing"
        )
        await self._alerts.emit(
            level=signal.severity,
            category=category,
            message=signal.message,
            actionable_info=signal.actionable_info,
            related_ipo_id=signal.ipo_id,
        )

    @staticmethod
    def _actual_price_for(meta: IPOMetadata, day: int) -> Any | None:
        return meta.actual_price_at_checkpoint.get(day)

    @staticmethod
    def _fallback_price(meta: IPOMetadata) -> Any | None:
        """R8-4: no fake-zero sentinel — return None when no real price.

        Pre-R8-4 this returned ``Decimal("0")`` so callers always got
        a "valid" price. That violated CLAUDE.md §自动化与状态机约束:
        "数据源失败有序降级... 禁止用估算值代替真实数据。" Every
        downstream review treated $0 as the actual closing price (i.e.
        the IPO crashed 100%) and flagged a critical loss spuriously.

        Now the helper returns None and callers (``_process_listed_snapshot``)
        short-circuit the review-draft generation for that checkpoint —
        a missed-price condition is surfaced, not papered over.
        """
        del meta  # unused; the signal is purely "we have no price"
        return None


# Suppress unused-import warning.
_ = days_in_state


__all__ = (
    "TERMINAL_DAY_THRESHOLD",
    "DailyScheduler",
    "IPOMetadata",
)
