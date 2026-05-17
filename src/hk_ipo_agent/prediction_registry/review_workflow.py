"""Review-draft workflow per PROJECT_SPEC.md §3.11.

Auto-generates a ``PredictionReview`` draft for any of the major
checkpoints (30 / 90 / 180 / 360 days) or whenever critical conditions
fire (decision_correct=False AND |actual return| > 20%).

Lifecycle:
1. ``generate_draft(snapshot_id, checkpoint_day)`` is called by the
   daily scheduler when an outcome lands on a major checkpoint.
2. The draft is persisted via ``registry.attach_review`` with
   ``adjustment_status=proposed`` + ``reviewer=None``.
3. A human reviewer fills in ``what_we_got_*`` + accepts/rejects each
   proposed adjustment via the ``/api/reviews/{id}`` endpoint.
4. ``review_workflow.submit_review`` writes the final review state.

CLAUDE.md "system MUST NOT auto-apply any adjustment" — submit_review
never sets status=accepted unilaterally; the API does that explicitly
on reviewer action.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..common.enums import AdjustmentStatus
from ..common.logging import get_logger
from ..common.schemas import (
    Attribution,
    PredictionOutcome,
    PredictionReview,
    PredictionSnapshot,
)
from ..data.models import PredictionOutcomeRow
from .attribution import AttributionEngine

logger = get_logger(__name__)

MAJOR_CHECKPOINTS: tuple[int, ...] = (30, 90, 180, 360)
CRITICAL_LOSS_THRESHOLD = Decimal("-0.20")


@dataclass(frozen=True)
class DraftResult:
    """Return value of ``ReviewWorkflow.generate_draft``."""

    review_id: UUID
    skipped: bool = False
    skip_reason: str | None = None


class _RegistryProtocol(Protocol):
    async def get_snapshot(self, snapshot_id: UUID) -> PredictionSnapshot: ...
    async def attach_review(self, snapshot_id: UUID, review: PredictionReview) -> UUID: ...


class ReviewWorkflow:
    """Generates and persists review drafts at major checkpoints."""

    def __init__(
        self,
        *,
        registry: _RegistryProtocol,
        attribution: AttributionEngine,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._registry = registry
        self._attribution = attribution
        self._sf = session_factory

    async def generate_draft(
        self,
        *,
        snapshot_id: UUID,
        checkpoint_day: int,
        actual_price: Decimal,
        force: bool = False,
    ) -> DraftResult:
        """Materialise an Attribution + write a review draft.

        Returns immediately with ``skipped=True`` if ``checkpoint_day`` is
        not a major checkpoint (unless ``force=True``, used by the
        critical-loss trigger).
        """
        if not force and checkpoint_day not in MAJOR_CHECKPOINTS:
            return DraftResult(
                review_id=UUID(int=0),
                skipped=True,
                skip_reason=f"not a major checkpoint: {checkpoint_day}",
            )

        snapshot = await self._registry.get_snapshot(snapshot_id)
        outcome = await self._load_outcome(snapshot_id, checkpoint_day)
        if outcome is None:
            return DraftResult(
                review_id=UUID(int=0),
                skipped=True,
                skip_reason=f"no outcome for (snapshot={snapshot_id}, day={checkpoint_day})",
            )

        attribution = await self._attribution.attribute(
            snapshot=snapshot,
            outcome=outcome,
            actual_price=actual_price,
        )
        review = self._draft_review(snapshot, outcome, attribution)
        review_id = await self._registry.attach_review(snapshot_id, review)
        return DraftResult(review_id=review_id, skipped=False)

    async def generate_critical_draft_if_needed(
        self,
        *,
        snapshot_id: UUID,
        checkpoint_day: int,
        actual_price: Decimal,
    ) -> DraftResult:
        """Bypass major-checkpoint filter when realised return < -20%.

        Per PROJECT_SPEC.md §3.11: "high priority: decision wrong +
        realised return < -20%, immediately generate critical_review".
        """
        outcome = await self._load_outcome(snapshot_id, checkpoint_day)
        if outcome is None:
            return DraftResult(
                review_id=UUID(int=0),
                skipped=True,
                skip_reason="no outcome",
            )
        ret = outcome.return_since_listing or outcome.return_since_ipo
        if outcome.decision_correct is True or ret >= CRITICAL_LOSS_THRESHOLD:
            return DraftResult(
                review_id=UUID(int=0),
                skipped=True,
                skip_reason=f"non-critical: correct={outcome.decision_correct}, ret={ret}",
            )
        # Force-generate at any checkpoint when criteria met.
        return await self.generate_draft(
            snapshot_id=snapshot_id,
            checkpoint_day=checkpoint_day,
            actual_price=actual_price,
            force=True,
        )

    async def submit_review(
        self,
        *,
        snapshot_id: UUID,
        reviewer: str,
        what_we_got_right: str,
        what_we_got_wrong: str,
        notes_md: str = "",
        adjustment_status: AdjustmentStatus = AdjustmentStatus.PROPOSED,
        review_checkpoint_day: int = -1,
    ) -> UUID:
        """Persist a finalised review against ``snapshot_id``.

        Caller is responsible for setting ``adjustment_status=accepted``
        only after the reviewer has explicitly opted into each proposal.
        """
        # Build a stub attribution that gets overridden when the
        # learning-loop pulls the latest review for adjustment apply.
        from ..common.schemas import DebateQualityAnalysis

        attribution_stub = Attribution(
            snapshot_id=snapshot_id,
            checkpoint_day=review_checkpoint_day,
            debate_quality=DebateQualityAnalysis(
                bear_predictions_validated=0,
                bear_predictions_total=0,
                bull_predictions_validated=0,
                bull_predictions_total=0,
            ),
            primary_attribution="manual_review",
            llm_diagnosis="(human-authored review)",
            proposed_adjustments=[],
        )
        review = PredictionReview(
            snapshot_id=snapshot_id,
            review_checkpoint_day=review_checkpoint_day,
            reviewer=reviewer,
            what_we_got_right=what_we_got_right,
            what_we_got_wrong=what_we_got_wrong,
            primary_attribution="manual_review",
            attribution_details=attribution_stub,
            proposed_adjustments=[],
            adjustment_status=adjustment_status,
            notes_md=notes_md,
            created_at=datetime.now(UTC),
        )
        return await self._registry.attach_review(snapshot_id, review)

    # ------------------------------------------------------------------

    async def _load_outcome(
        self, snapshot_id: UUID, checkpoint_day: int
    ) -> PredictionOutcome | None:
        stmt = (
            select(PredictionOutcomeRow)
            .where(PredictionOutcomeRow.snapshot_id == snapshot_id)
            .where(PredictionOutcomeRow.checkpoint_day == checkpoint_day)
            .limit(1)
        )
        async with self._sf() as s:
            row = (await s.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return PredictionOutcome(
            snapshot_id=row.snapshot_id,
            checkpoint_day=row.checkpoint_day,
            return_since_ipo=float(row.return_since_ipo or 0),
            return_since_listing=(
                float(row.return_since_listing) if row.return_since_listing is not None else None
            ),
            max_drawdown=float(row.max_drawdown or 0),
            relative_return_hsi=float(row.relative_return_hsi or 0),
            relative_return_hstech=float(row.relative_return_hstech or 0),
            relative_return_industry=float(row.relative_return_industry or 0),
            events_in_window=[],  # full reconstruction not needed for review draft
            earnings_released=row.earnings_released,
            earnings_beat_extraction=row.earnings_beat_extraction,
            cornerstone_held_pct=(
                float(row.cornerstone_held_pct) if row.cornerstone_held_pct is not None else None
            ),
            cornerstone_reduced=row.cornerstone_reduced,
            price_in_predicted_range=row.price_in_predicted_range or False,
            decision_correct=row.decision_correct or False,
            recorded_at=row.recorded_at,
        )

    @staticmethod
    def _draft_review(
        snapshot: PredictionSnapshot,
        outcome: PredictionOutcome,
        attribution: Attribution,
    ) -> PredictionReview:
        ret = outcome.return_since_listing or outcome.return_since_ipo
        right = (
            f"Decision {snapshot.decision.decision.value} validated by realised "
            f"return {ret:.2%}. Price-in-range={outcome.price_in_predicted_range}."
            if outcome.decision_correct
            else "Decision NOT validated. See attribution for layer-level analysis."
        )
        wrong = (
            "All three layers (agent / valuation / debate) tracked within tolerance."
            if outcome.decision_correct
            else (
                f"Primary attribution: {attribution.primary_attribution}. "
                f"See {len(attribution.proposed_adjustments)} proposed adjustments."
            )
        )
        return PredictionReview(
            snapshot_id=snapshot.id,
            review_checkpoint_day=outcome.checkpoint_day,
            reviewer="auto_draft",  # human reviewer overwrites on accept
            what_we_got_right=right,
            what_we_got_wrong=wrong,
            primary_attribution=attribution.primary_attribution,
            attribution_details=attribution,
            proposed_adjustments=attribution.proposed_adjustments,
            adjustment_status=AdjustmentStatus.PROPOSED,
            notes_md=attribution.llm_diagnosis,
            created_at=datetime.now(UTC),
        )


def is_major_checkpoint(checkpoint_day: int) -> bool:
    """Public helper for the daily scheduler to gate review generation."""
    return checkpoint_day in MAJOR_CHECKPOINTS


def days_since_listing(listing_d: date, today: date | None = None) -> int:
    """Public helper for the daily scheduler."""
    anchor = today or date.today()
    return (anchor - listing_d).days


# Suppress unused import lint when timedelta isn't used by tests below.
_ = timedelta


__all__ = (
    "CRITICAL_LOSS_THRESHOLD",
    "MAJOR_CHECKPOINTS",
    "DraftResult",
    "ReviewWorkflow",
    "days_since_listing",
    "is_major_checkpoint",
)
