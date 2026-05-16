"""In-memory prediction registry (Phase 6) per ADR 0010 §3.

The CLAUDE.md "prediction lifecycle" constraints demand that:
- Any complete analysis MUST create a snapshot before emitting a decision
- ``prediction_snapshots`` data must be immutable (no UPDATE)

Phase 6 ships an in-memory ``dict``-backed registry that enforces #2 at
the Python layer (Pydantic ``FrozenModel`` + this class rejecting any
mutation). Phase 7.5 replaces the backing store with PostgreSQL + DB
trigger.

The public API surface MUST stay stable across the swap — callers from
``orchestrator/nodes.py`` won't know the difference.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from ..common.schemas import PredictionSnapshot
from .snapshot import SnapshotIntegrityError, verify_snapshot


class PredictionRegistry:
    """Append-only snapshot store.

    Phase 6 implementation: in-memory dict.
    Phase 7.5 replacement: AsyncSession on ``prediction_snapshots`` table.
    """

    def __init__(self) -> None:
        self._store: dict[UUID, PredictionSnapshot] = {}
        self._lock = asyncio.Lock()

    async def create_snapshot(self, snapshot: PredictionSnapshot) -> UUID:
        """Persist ``snapshot``; reject if id already exists.

        Returns the snapshot's UUID for caller convenience.
        """
        verify_snapshot(snapshot)  # Defensive: catches caller-side tampering
        async with self._lock:
            if snapshot.id in self._store:
                raise SnapshotIntegrityError(
                    f"snapshot id {snapshot.id} already exists — registry is append-only"
                )
            self._store[snapshot.id] = snapshot
        return snapshot.id

    async def get_snapshot(self, snapshot_id: UUID) -> PredictionSnapshot:
        """Fetch + verify integrity. Raises ``KeyError`` if not found."""
        async with self._lock:
            snap = self._store.get(snapshot_id)
        if snap is None:
            raise KeyError(snapshot_id)
        verify_snapshot(snap)
        return snap

    async def list_snapshots(self) -> list[PredictionSnapshot]:
        """Return all snapshots (read-only view)."""
        async with self._lock:
            return list(self._store.values())

    def __len__(self) -> int:
        return len(self._store)


# Module-level singleton (held in a mutable container to avoid `global`).
# Phase 7.5 will replace this with a session-scoped DB-backed instance.
_default_registry: list[PredictionRegistry] = []


def get_registry() -> PredictionRegistry:
    """Return the process-wide singleton (in-memory)."""
    if not _default_registry:
        _default_registry.append(PredictionRegistry())
    return _default_registry[0]


def reset_registry() -> None:
    """Clear the singleton — testing only. Do NOT call from production code."""
    _default_registry.clear()


__all__ = (
    "PredictionRegistry",
    "get_registry",
    "reset_registry",
)
