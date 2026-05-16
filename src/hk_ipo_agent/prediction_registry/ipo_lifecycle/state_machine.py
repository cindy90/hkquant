"""IPO lifecycle state machine — PROJECT_SPEC.md §3.11.1.

Single source of truth for current state + transition writes. Every
``transition_to`` call:
1. validates the (from → to) is in VALID_TRANSITIONS (raises
   ``InvalidStateTransition`` otherwise)
2. upserts the current state in ``ipo_lifecycle_states``
3. appends an immutable audit row to ``ipo_state_transitions``

The state-machine row + audit log are persisted in the same transaction
so they can never disagree.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...common.enums import IPOLifecycleStateType, TransitionTrigger
from ...common.exceptions import LifecycleError
from ...common.logging import get_logger
from ...data.models import IPOLifecycleStateRow, IPOStateTransitionRow
from .states import assert_valid_transition, initial_state, is_terminal

logger = get_logger(__name__)


class StateMachineError(LifecycleError):
    """Raised on storage-level failures (DB, optimistic locking, etc.)."""

    default_message = "IPO lifecycle state machine error"


class StateMachine:
    """Read + transition the lifecycle state for one IPO at a time."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def get_state(
        self, ipo_id: UUID
    ) -> tuple[IPOLifecycleStateType, IPOLifecycleStateRow] | None:
        """Return ``(state_enum, row)`` or None if no state row exists yet."""
        stmt = select(IPOLifecycleStateRow).where(IPOLifecycleStateRow.ipo_id == ipo_id)
        async with self._sf() as s:
            row = (await s.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return IPOLifecycleStateType(row.current_state), row

    async def initialize(
        self,
        ipo_id: UUID,
        *,
        triggered_by: TransitionTrigger = TransitionTrigger.AUTO_DETECTOR,
        metadata: dict[str, Any] | None = None,
    ) -> IPOLifecycleStateRow:
        """Create the initial PRE_LISTING row. Idempotent — returns existing if present."""
        existing = await self.get_state(ipo_id)
        if existing is not None:
            return existing[1]
        now = datetime.now(UTC)
        async with self._sf() as s:
            row = IPOLifecycleStateRow(
                ipo_id=ipo_id,
                current_state=initial_state().value,
                state_entered_at=now,
                state_metadata=metadata or {},
                last_checked_at=now,
                is_terminal=False,
            )
            s.add(row)
            s.add(IPOStateTransitionRow(
                ipo_id=ipo_id,
                from_state=None,
                to_state=initial_state().value,
                transition_at=now,
                triggered_by=triggered_by.value,
                detection_evidence={"reason": "initialize_lifecycle"},
            ))
            await s.commit()
        return row

    async def transition_to(
        self,
        ipo_id: UUID,
        new_state: IPOLifecycleStateType,
        *,
        triggered_by: TransitionTrigger,
        evidence: dict[str, Any] | None = None,
        reviewer: str | None = None,
    ) -> IPOLifecycleStateRow:
        """Validated state transition + audit log write.

        Raises ``InvalidStateTransition`` if (current → new_state) is not
        in VALID_TRANSITIONS; the state row is left untouched.
        """
        current = await self.get_state(ipo_id)
        if current is None:
            raise StateMachineError(
                f"ipo_id={ipo_id} has no lifecycle row — call initialize() first"
            )
        from_state, row = current
        assert_valid_transition(from_state, new_state)
        now = datetime.now(UTC)
        async with self._sf() as s:
            # Refresh + update in the same session for consistency.
            db_row = await s.get(IPOLifecycleStateRow, row.id)
            assert db_row is not None
            db_row.current_state = new_state.value
            db_row.state_entered_at = now
            db_row.last_checked_at = now
            db_row.is_terminal = is_terminal(new_state)
            if evidence:
                db_row.state_metadata = {**(db_row.state_metadata or {}), **evidence}
            s.add(IPOStateTransitionRow(
                id=_uuid.uuid4(),
                ipo_id=ipo_id,
                from_state=from_state.value,
                to_state=new_state.value,
                transition_at=now,
                triggered_by=triggered_by.value,
                detection_evidence=evidence or {},
                reviewer=reviewer,
            ))
            await s.commit()
            await s.refresh(db_row)
        logger.info(
            "ipo_lifecycle_transition",
            ipo_id=str(ipo_id),
            from_state=from_state.value,
            to_state=new_state.value,
            triggered_by=triggered_by.value,
        )
        return db_row

    async def touch_last_checked(self, ipo_id: UUID) -> None:
        """Update ``last_checked_at`` without changing state.

        Used by detectors when they've looked but found no signal.
        """
        async with self._sf() as s:
            stmt = select(IPOLifecycleStateRow).where(
                IPOLifecycleStateRow.ipo_id == ipo_id
            )
            row = (await s.execute(stmt)).scalar_one_or_none()
            if row is None:
                return
            row.last_checked_at = datetime.now(UTC)
            await s.commit()


__all__ = (
    "StateMachine",
    "StateMachineError",
)
