"""Prediction registry — append-only snapshot store.

Phase 6 shipped an in-memory ``dict``-backed registry per ADR 0010 §3;
Phase 7.5a swaps the backing store for PostgreSQL while keeping the
public coroutine signatures stable.

Layout per ADR 0012 Phase 7.5a:

- ``PredictionRegistryProtocol`` — the API contract.
- ``InMemoryPredictionRegistry`` — fast unit-test backend; the Phase 6
  implementation, renamed.
- ``PGPredictionRegistry`` — production backend; persists into
  ``prediction_snapshots`` + ``prediction_reviews`` via ``AsyncSession``.
  DB triggers ``snapshot_no_update`` / ``snapshot_no_delete`` make the
  immutability constraint defensible at the storage layer.
- ``get_registry`` / ``set_registry`` — process-wide accessor. Default is
  in-memory so unit tests Just Work; the FastAPI lifespan in
  ``api/main.py`` swaps it to ``PGPredictionRegistry`` on startup.

The CLAUDE.md "prediction lifecycle" constraints — snapshot is the ONLY
way a decision can be emitted; ``prediction_snapshots`` is immutable;
reviews are the ONLY append allowed — are enforced together by Pydantic
(``FrozenModel``), this module, and the DB triggers from the v1.1
migration.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..common.schemas import PredictionReview, PredictionSnapshot
from ..data.database import async_session_factory
from ..data.models import PredictionReviewRow, PredictionSnapshotRow
from .snapshot import SnapshotIntegrityError, verify_snapshot

# Default active-tracking window per CLAUDE.md "Checkpoint 日期固定": last
# canonical checkpoint is T+360 from listing date.
DEFAULT_ACTIVE_WINDOW_DAYS = 360


@runtime_checkable
class PredictionRegistryProtocol(Protocol):
    """API contract honoured by both in-memory and PG backends.

    Callers from ``orchestrator/nodes.py`` and ``api/routers/*`` MUST
    program against this protocol so the backend swap is invisible.
    """

    async def create_snapshot(self, snapshot: PredictionSnapshot) -> UUID: ...

    async def get_snapshot(self, snapshot_id: UUID) -> PredictionSnapshot: ...

    async def list_snapshots(self, limit: int = 100) -> list[PredictionSnapshot]: ...

    async def list_active_predictions(
        self,
        as_of_date: datetime | None = None,
        window_days: int = DEFAULT_ACTIVE_WINDOW_DAYS,
    ) -> list[PredictionSnapshot]: ...

    async def attach_review(self, snapshot_id: UUID, review: PredictionReview) -> UUID: ...

    # R2-3: explicit "immutable" surface. Both backends raise.
    async def update_snapshot(  # pragma: no cover — Protocol stub
        self, snapshot_id: UUID, snapshot: PredictionSnapshot
    ) -> None: ...

    async def delete_snapshot(self, snapshot_id: UUID) -> None: ...  # pragma: no cover


# R2-3 helper: shared "immutable by design" error message for both backends.
_IMMUTABLE_REASON = (
    "snapshot is immutable by design — see ADR 0012 + CLAUDE.md §预测生命周期约束. "
    "Application code MUST NOT update/delete; corrections go through "
    "ipo_lifecycle.state_machine.record_correction (R2-4) instead."
)


# ---------------------------------------------------------------------------
# In-memory backend (Phase 6 implementation, kept for unit tests)
# ---------------------------------------------------------------------------


class InMemoryPredictionRegistry:
    """Process-local dict-backed registry. Fast, fixture-free, test-only.

    Production code must use ``PGPredictionRegistry`` (set via
    ``set_registry`` on FastAPI lifespan startup).
    """

    def __init__(self) -> None:
        self._snapshots: dict[UUID, PredictionSnapshot] = {}
        self._reviews: dict[UUID, list[PredictionReview]] = {}
        self._lock = asyncio.Lock()

    async def create_snapshot(self, snapshot: PredictionSnapshot) -> UUID:
        verify_snapshot(snapshot)
        async with self._lock:
            if snapshot.id in self._snapshots:
                raise SnapshotIntegrityError(
                    f"snapshot id {snapshot.id} already exists — registry is append-only"
                )
            self._snapshots[snapshot.id] = snapshot
        return snapshot.id

    async def get_snapshot(self, snapshot_id: UUID) -> PredictionSnapshot:
        async with self._lock:
            snap = self._snapshots.get(snapshot_id)
        if snap is None:
            raise KeyError(snapshot_id)
        verify_snapshot(snap)
        return snap

    async def list_snapshots(self, limit: int = 100) -> list[PredictionSnapshot]:
        async with self._lock:
            items = list(self._snapshots.values())
        return items[:limit]

    async def list_active_predictions(
        self,
        as_of_date: datetime | None = None,
        window_days: int = DEFAULT_ACTIVE_WINDOW_DAYS,
    ) -> list[PredictionSnapshot]:
        cutoff = (as_of_date or datetime.now(UTC)) - timedelta(days=window_days)
        async with self._lock:
            return [s for s in self._snapshots.values() if s.created_at >= cutoff]

    async def attach_review(self, snapshot_id: UUID, review: PredictionReview) -> UUID:
        async with self._lock:
            if snapshot_id not in self._snapshots:
                raise KeyError(snapshot_id)
            self._reviews.setdefault(snapshot_id, []).append(review)
        return uuid4()

    async def update_snapshot(
        self, snapshot_id: UUID, snapshot: PredictionSnapshot
    ) -> None:
        """R2-3 — explicit refusal. Snapshots are immutable by design."""
        raise NotImplementedError(_IMMUTABLE_REASON)

    async def delete_snapshot(self, snapshot_id: UUID) -> None:
        """R2-3 — explicit refusal. Snapshots are immutable by design."""
        raise NotImplementedError(_IMMUTABLE_REASON)

    def __len__(self) -> int:
        return len(self._snapshots)


# ---------------------------------------------------------------------------
# PostgreSQL backend (Phase 7.5a default)
# ---------------------------------------------------------------------------


class PGPredictionRegistry:
    """PostgreSQL-backed registry honouring snapshot immutability via DB triggers.

    Reads return Pydantic ``PredictionSnapshot`` (FrozenModel); writes
    serialise JSONB columns from the Pydantic ``model_dump(mode='json')``
    output so the SHA-256 hash computed by ``snapshot.py`` matches on
    re-read.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._sf = session_factory or async_session_factory()

    async def create_snapshot(self, snapshot: PredictionSnapshot) -> UUID:
        verify_snapshot(snapshot)
        row = self._to_row(snapshot)
        async with self._sf() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise SnapshotIntegrityError(
                    f"snapshot id {snapshot.id} or (ipo_id, as_of_date, version) "
                    f"already exists — registry is append-only"
                ) from exc
        return snapshot.id

    async def get_snapshot(self, snapshot_id: UUID) -> PredictionSnapshot:
        async with self._sf() as session:
            row = await session.get(PredictionSnapshotRow, snapshot_id)
            if row is None:
                raise KeyError(snapshot_id)
            snap = self._from_row(row)
        verify_snapshot(snap)
        return snap

    async def list_snapshots(self, limit: int = 100) -> list[PredictionSnapshot]:
        stmt = (
            select(PredictionSnapshotRow)
            .order_by(PredictionSnapshotRow.created_at.desc())
            .limit(limit)
        )
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [self._from_row(r) for r in rows]

    async def list_active_predictions(
        self,
        as_of_date: datetime | None = None,
        window_days: int = DEFAULT_ACTIVE_WINDOW_DAYS,
    ) -> list[PredictionSnapshot]:
        anchor = as_of_date or datetime.now(UTC)
        cutoff = anchor - timedelta(days=window_days)
        stmt = (
            select(PredictionSnapshotRow)
            .where(PredictionSnapshotRow.created_at >= cutoff)
            .where(PredictionSnapshotRow.created_at <= anchor)
            .order_by(PredictionSnapshotRow.created_at.desc())
        )
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [self._from_row(r) for r in rows]

    async def attach_review(self, snapshot_id: UUID, review: PredictionReview) -> UUID:
        async with self._sf() as session:
            snap = await session.get(PredictionSnapshotRow, snapshot_id)
            if snap is None:
                raise KeyError(snapshot_id)
            review_id = uuid4()
            row = PredictionReviewRow(
                id=review_id,
                snapshot_id=snapshot_id,
                review_checkpoint_day=review.review_checkpoint_day,
                reviewer=review.reviewer,
                what_we_got_right=review.what_we_got_right,
                what_we_got_wrong=review.what_we_got_wrong,
                primary_attribution=review.primary_attribution,
                attribution_details=review.attribution_details.model_dump(mode="json"),
                proposed_adjustments=[
                    a.model_dump(mode="json") for a in review.proposed_adjustments
                ],
                adjustment_status=review.adjustment_status.value,
                applied_at=review.applied_at,
                applied_version=review.applied_version,
                notes_md=review.notes_md,
            )
            session.add(row)
            await session.commit()
        return review_id

    async def update_snapshot(
        self, snapshot_id: UUID, snapshot: PredictionSnapshot
    ) -> None:
        """R2-3 — explicit refusal at the application layer.

        The DB ``snapshot_no_update`` trigger is the physical defence; this
        method is the application-layer defence so a Protocol-typed caller
        gets a typed, named error rather than AttributeError, and so that
        in-memory and PG backends present the same API surface.
        """
        raise NotImplementedError(_IMMUTABLE_REASON)

    async def delete_snapshot(self, snapshot_id: UUID) -> None:
        """R2-3 — explicit refusal at the application layer.

        DB ``snapshot_no_delete`` trigger handles physical defence.
        """
        raise NotImplementedError(_IMMUTABLE_REASON)

    # ------------------------------------------------------------------
    # Row <-> Pydantic helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_row(snap: PredictionSnapshot) -> PredictionSnapshotRow:
        return PredictionSnapshotRow(
            id=snap.id,
            ipo_id=snap.ipo_id,
            as_of_date=snap.as_of_date,
            prospectus_version=snap.prospectus_version,
            input_data_hash=snap.input_data_hash,
            input_data_snapshot=snap.input_data_snapshot,
            agent_outputs={k: v.model_dump(mode="json") for k, v in snap.agent_outputs.items()},
            valuation_output=snap.valuation_output.model_dump(mode="json"),
            debate_output=snap.debate_output.model_dump(mode="json"),
            decision=snap.decision.model_dump(mode="json"),
            system_version=snap.system_version,
            model_versions=snap.model_versions,
            config_snapshot=snap.config_snapshot,
            total_cost_usd=snap.total_cost_usd,
            runtime_seconds=snap.runtime_seconds,
            created_at=snap.created_at,
        )

    @staticmethod
    def _from_row(row: PredictionSnapshotRow) -> PredictionSnapshot:
        return PredictionSnapshot.model_validate(
            {
                "id": row.id,
                "ipo_id": row.ipo_id,
                "as_of_date": row.as_of_date,
                "prospectus_version": row.prospectus_version,
                "input_data_hash": row.input_data_hash,
                "input_data_snapshot": row.input_data_snapshot,
                "agent_outputs": row.agent_outputs,
                "valuation_output": row.valuation_output,
                "debate_output": row.debate_output,
                "decision": row.decision,
                "system_version": row.system_version,
                "model_versions": row.model_versions,
                "config_snapshot": row.config_snapshot,
                "total_cost_usd": row.total_cost_usd,
                "runtime_seconds": row.runtime_seconds,
                "created_at": row.created_at,
            }
        )


# ---------------------------------------------------------------------------
# Process-wide accessor
# ---------------------------------------------------------------------------


# Held in a mutable container to avoid `global`. Default is in-memory so
# unit tests without a DB session "Just Work"; production swaps in
# ``PGPredictionRegistry`` from ``api/main.py`` lifespan.
_default_registry: list[PredictionRegistryProtocol] = []


def get_registry() -> PredictionRegistryProtocol:
    """Return the process-wide registry. Default is ``InMemoryPredictionRegistry``."""
    if not _default_registry:
        _default_registry.append(InMemoryPredictionRegistry())
    return _default_registry[0]


def set_registry(registry: PredictionRegistryProtocol) -> None:
    """Replace the process-wide registry — called from FastAPI lifespan.

    Production usage:

        async def lifespan(app):
            set_registry(PGPredictionRegistry())
            yield
    """
    _default_registry.clear()
    _default_registry.append(registry)


def reset_registry() -> None:
    """Clear the singleton — testing only. Do NOT call from production code."""
    _default_registry.clear()


# Back-compat alias for Phase 6 callers that imported ``PredictionRegistry``
# directly. New code MUST type against ``PredictionRegistryProtocol``.
PredictionRegistry = InMemoryPredictionRegistry


__all__ = (
    "DEFAULT_ACTIVE_WINDOW_DAYS",
    "InMemoryPredictionRegistry",
    "PGPredictionRegistry",
    "PredictionRegistry",
    "PredictionRegistryProtocol",
    "get_registry",
    "reset_registry",
    "set_registry",
)
