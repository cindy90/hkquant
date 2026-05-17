"""BaseScheduler abstract per PROJECT_SPEC.md §3.11.2.

Every concrete scheduler (high_freq / daily / event_driven) inherits
from ``BaseScheduler`` and gets for free:

- DB advisory lock (``pg_try_advisory_lock``) keyed on scheduler_type
  → CLAUDE.md v1.2 "重叠运行用 DB advisory lock 拦截"
- ``scheduler_runs`` row written at run start + completion
- run_id UNIQUE for idempotency anchor
- error capture + escalation (subclasses override ``alert_on_failure``)

CLAUDE.md v1.2 constraints honoured here:
- high_freq scheduler MUST NOT do heavy work; the abstract gives the
  subclass a free hand but the daily_scheduler vs high_freq separation
  is enforced by which method each subclass implements
- "调度器失败必须升级": ``alert_on_failure`` integration with AlertRouter
"""

from __future__ import annotations

import abc
import asyncio
import hashlib
import socket
import uuid as _uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...common.enums import SchedulerStatus, SchedulerType
from ...common.exceptions import SchedulerError, SchedulerLockError
from ...common.logging import get_logger
from ...data.models import SchedulerRunRow

logger = get_logger(__name__)


@dataclass
class RunStats:
    """Mutable counters; subclasses bump them inside ``do_work``."""

    snapshots_processed: int = 0
    events_detected: int = 0
    errors_encountered: int = 0
    error_details: list[dict[str, Any]] | None = None

    def record_error(self, err: Exception, *, context: dict[str, Any] | None = None) -> None:
        self.errors_encountered += 1
        self.error_details = self.error_details or []
        self.error_details.append(
            {
                "error_type": type(err).__name__,
                "message": str(err),
                "context": context or {},
            }
        )


@dataclass
class RunResult:
    """Snapshot of a single scheduler run."""

    run_id: str
    scheduler_type: SchedulerType
    status: SchedulerStatus
    stats: RunStats
    started_at: datetime
    completed_at: datetime | None
    locked: bool  # True iff advisory lock was acquired


def _lock_key_for(scheduler_type: SchedulerType) -> int:
    """Stable 63-bit signed int from scheduler_type for pg_try_advisory_lock."""
    digest = hashlib.sha256(f"hk_ipo:scheduler:{scheduler_type.value}".encode()).digest()
    # PostgreSQL advisory lock keys are bigint (-2^63 .. 2^63 - 1).
    return int.from_bytes(digest[:8], "big", signed=True)


class BaseScheduler(abc.ABC):
    """Abstract scheduler with advisory-lock + scheduler_runs lifecycle."""

    scheduler_type: SchedulerType

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._sf = session_factory

    async def run(self) -> RunResult:
        """Single run with lock + scheduler_runs lifecycle.

        Returns the result regardless of success / failure; ``status``
        reflects what happened. Advisory-lock contention returns
        immediately with ``locked=False`` so a competing replica can
        log + back off without raising.
        """
        run_id = self._new_run_id()
        started = datetime.now(UTC)
        async with self._try_lock() as got_lock:
            if not got_lock:
                logger.warning(
                    "scheduler_lock_contention",
                    scheduler_type=self.scheduler_type.value,
                    run_id=run_id,
                )
                return RunResult(
                    run_id=run_id,
                    scheduler_type=self.scheduler_type,
                    status=SchedulerStatus.FAILED,  # didn't actually run
                    stats=RunStats(),
                    started_at=started,
                    completed_at=started,
                    locked=False,
                )

            await self._write_run_start(run_id, started)
            stats = RunStats()
            try:
                await self.do_work(stats)
                final_status = SchedulerStatus.COMPLETED
            except Exception as exc:
                stats.record_error(exc, context={"phase": "do_work"})
                final_status = SchedulerStatus.FAILED
                logger.exception(
                    "scheduler_run_failed",
                    scheduler_type=self.scheduler_type.value,
                    run_id=run_id,
                )
                await self.alert_on_failure(run_id=run_id, error=exc)
            completed = datetime.now(UTC)
            await self._write_run_complete(run_id, completed, stats, final_status)
            return RunResult(
                run_id=run_id,
                scheduler_type=self.scheduler_type,
                status=final_status,
                stats=stats,
                started_at=started,
                completed_at=completed,
                locked=True,
            )

    # ------------------------------------------------------------------
    # Hooks for subclasses
    # ------------------------------------------------------------------

    @abc.abstractmethod
    async def do_work(self, stats: RunStats) -> None:
        """Subclass-specific work loop. Bump ``stats`` counters in-place."""

    async def alert_on_failure(self, *, run_id: str, error: Exception) -> None:
        """Default: log only. Subclasses with an AlertRouter override this."""
        logger.error(
            "scheduler_alert_default",
            scheduler_type=self.scheduler_type.value,
            run_id=run_id,
            error=str(error),
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _new_run_id(self) -> str:
        """Globally-unique run_id: scheduler_type + host + uuid4."""
        host = socket.gethostname()
        return f"{self.scheduler_type.value}:{host}:{_uuid.uuid4()}"

    @asynccontextmanager
    async def _try_lock(self) -> AsyncIterator[bool]:
        """Attempt to acquire the per-scheduler PG advisory lock.

        Yields True iff acquired; releases automatically on exit.
        We use ``pg_try_advisory_lock`` (non-blocking) so a competing
        replica returns immediately rather than waiting.
        """
        key = _lock_key_for(self.scheduler_type)
        # Each scheduler run gets its own session so the lock is bound
        # to *this* connection and released by ``COMMIT`` / connection close.
        async with self._sf() as s:
            result = await s.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key})
            got = bool(result.scalar_one())
            try:
                yield got
            finally:
                if got:
                    try:
                        await s.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
                        await s.commit()
                    except Exception as exc:
                        logger.warning(
                            "scheduler_lock_release_failed",
                            scheduler_type=self.scheduler_type.value,
                            error=str(exc),
                        )

    async def _write_run_start(self, run_id: str, started_at: datetime) -> None:
        row = SchedulerRunRow(
            id=_uuid.uuid4(),
            scheduler_type=self.scheduler_type.value,
            run_id=run_id,
            started_at=started_at,
            completed_at=None,
            snapshots_processed=0,
            events_detected=0,
            errors_encountered=0,
            error_details=None,
            status=SchedulerStatus.RUNNING.value,
        )
        async with self._sf() as s:
            s.add(row)
            await s.commit()

    async def _write_run_complete(
        self,
        run_id: str,
        completed_at: datetime,
        stats: RunStats,
        status: SchedulerStatus,
    ) -> None:
        async with self._sf() as s:
            await s.execute(
                update(SchedulerRunRow)
                .where(SchedulerRunRow.run_id == run_id)
                .values(
                    completed_at=completed_at,
                    snapshots_processed=stats.snapshots_processed,
                    events_detected=stats.events_detected,
                    errors_encountered=stats.errors_encountered,
                    error_details=stats.error_details,
                    status=status.value,
                )
            )
            await s.commit()


__all__ = (
    "UUID",
    "BaseScheduler",
    "RunResult",
    "RunStats",
    "SchedulerError",
    "SchedulerLockError",
    "_lock_key_for",
    "asyncio",
)
