"""Terminal-state handlers — PROJECT_SPEC.md §3.11.1.

When an IPO enters WITHDRAWN / HEARING_FAILED / PRICING_PULLED, this
handler:
1. writes a ``prediction_outcomes`` row with ``checkpoint_day = -1``
   (sentinel for terminal outcome) so downstream attribution can
   reason about survivorship-bias-free samples
2. emits a ``terminal_review_draft`` focused on "what signal did we
   miss?" — the failure mode is qualitatively different from a normal
   post-listing miss
"""

from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...common.enums import AdjustmentStatus, IPOLifecycleStateType
from ...common.logging import get_logger
from ...common.schemas import (
    Attribution,
    DebateQualityAnalysis,
    PredictionReview,
    PredictionSnapshot,
)
from ...data.models import PredictionOutcomeRow, PredictionSnapshotRow

logger = get_logger(__name__)

TERMINAL_CHECKPOINT_DAY = -1


class _RegistryProtocol(Protocol):
    async def get_snapshot(self, snapshot_id: UUID) -> PredictionSnapshot: ...
    async def attach_review(
        self, snapshot_id: UUID, review: PredictionReview
    ) -> UUID: ...


@dataclass(frozen=True)
class TerminalResult:
    """Return value of ``TerminalHandler.handle``."""

    outcome_id: UUID | None
    review_id: UUID | None
    skipped: bool = False
    skip_reason: str | None = None


class TerminalHandler:
    """Generates terminal outcome + draft review for WITHDRAWN / HEARING_FAILED."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        registry: _RegistryProtocol,
    ) -> None:
        self._sf = session_factory
        self._registry = registry

    async def handle(
        self,
        *,
        ipo_id: UUID,
        terminal_state: IPOLifecycleStateType,
    ) -> TerminalResult:
        """Process a freshly transitioned terminal state. Idempotent on snapshot."""
        if terminal_state not in {
            IPOLifecycleStateType.WITHDRAWN,
            IPOLifecycleStateType.HEARING_FAILED,
            IPOLifecycleStateType.PRICING_PULLED,
        }:
            return TerminalResult(
                outcome_id=None, review_id=None,
                skipped=True,
                skip_reason=f"state {terminal_state.value} is not a terminal-handler target",
            )

        snap_row = await self._latest_snapshot(ipo_id)
        if snap_row is None:
            return TerminalResult(
                outcome_id=None, review_id=None,
                skipped=True, skip_reason=f"no snapshot for ipo_id={ipo_id}",
            )

        # 1. Terminal outcome row
        outcome_id = await self._write_terminal_outcome(snap_row.id, terminal_state)
        # 2. Terminal review draft
        snapshot = await self._registry.get_snapshot(snap_row.id)
        review_id = await self._attach_terminal_review(
            snapshot, terminal_state=terminal_state,
        )
        return TerminalResult(outcome_id=outcome_id, review_id=review_id)

    async def _latest_snapshot(self, ipo_id: UUID) -> PredictionSnapshotRow | None:
        stmt = (
            select(PredictionSnapshotRow)
            .where(PredictionSnapshotRow.ipo_id == ipo_id)
            .order_by(PredictionSnapshotRow.created_at.desc())
            .limit(1)
        )
        async with self._sf() as s:
            return (await s.execute(stmt)).scalar_one_or_none()

    async def _write_terminal_outcome(
        self, snapshot_id: UUID, terminal_state: IPOLifecycleStateType,
    ) -> UUID | None:
        """Idempotent: if a -1 outcome row already exists, return None."""
        check_stmt = (
            select(PredictionOutcomeRow.id)
            .where(PredictionOutcomeRow.snapshot_id == snapshot_id)
            .where(PredictionOutcomeRow.checkpoint_day == TERMINAL_CHECKPOINT_DAY)
            .limit(1)
        )
        async with self._sf() as s:
            existing = (await s.execute(check_stmt)).scalar_one_or_none()
            if existing is not None:
                return None
            row = PredictionOutcomeRow(
                id=_uuid.uuid4(),
                snapshot_id=snapshot_id,
                checkpoint_day=TERMINAL_CHECKPOINT_DAY,
                return_since_ipo=None,
                return_since_listing=None,
                max_drawdown=None,
                relative_return_hsi=None,
                relative_return_hstech=None,
                relative_return_industry=None,
                events_in_window=[{"terminal_state": terminal_state.value}],
                earnings_released=False,
                price_in_predicted_range=False,
                decision_correct=False,  # locked-but-never-deployed capital = effective miss
                recorded_at=datetime.now(UTC),
            )
            s.add(row)
            await s.commit()
        return row.id

    async def _attach_terminal_review(
        self,
        snapshot: PredictionSnapshot,
        *,
        terminal_state: IPOLifecycleStateType,
    ) -> UUID:
        """Generate a terminal review_draft focused on missed signals."""
        if snapshot.decision.decision.value in ("participate", "partial"):
            wrong = (
                f"We recommended {snapshot.decision.decision.value} but the IPO "
                f"reached terminal state {terminal_state.value}. Capital that would "
                "have been allocated was effectively locked without an upside path."
            )
        else:
            wrong = (
                f"We recommended {snapshot.decision.decision.value}. The IPO reaching "
                f"{terminal_state.value} validates that decision."
            )
        right = (
            f"Terminal-state outcome ({terminal_state.value}) recorded. Lifecycle "
            "completed without listing; survivorship bias avoided per ADR 0012."
        )
        # The terminal attribution is intentionally a stub — Phase 10
        # learning loop will run a richer analysis when it scans the
        # terminal_review_draft set.
        stub_attribution = Attribution(
            snapshot_id=snapshot.id,
            checkpoint_day=TERMINAL_CHECKPOINT_DAY,
            debate_quality=DebateQualityAnalysis(
                bear_predictions_validated=0, bear_predictions_total=0,
                bull_predictions_validated=0, bull_predictions_total=0,
            ),
            primary_attribution="terminal_no_listing",
            llm_diagnosis=(
                f"Terminal state {terminal_state.value} reached. "
                "Phase 10 learning loop should diagnose missed signals."
            ),
            proposed_adjustments=[],
        )
        review = PredictionReview(
            snapshot_id=snapshot.id,
            review_checkpoint_day=TERMINAL_CHECKPOINT_DAY,
            reviewer="auto_terminal_handler",
            what_we_got_right=right,
            what_we_got_wrong=wrong,
            primary_attribution="terminal_no_listing",
            attribution_details=stub_attribution,
            proposed_adjustments=[],
            adjustment_status=AdjustmentStatus.PROPOSED,
            notes_md=(
                "Terminal-state review_draft. Operator should fill in what specific "
                "signal (cornerstone disclosure quality, sponsor track record, regulatory "
                "regime indicators) we should have weighted differently."
            ),
            created_at=datetime.now(UTC),
        )
        return await self._registry.attach_review(snapshot.id, review)


# Suppress unused-import lint.
_ = Decimal


__all__ = (
    "TERMINAL_CHECKPOINT_DAY",
    "TerminalHandler",
    "TerminalResult",
)
