"""Generic async repository base.

Per PROJECT_SPEC.md §3.4 — every per-entity repository inherits from this
class. Sessions are injected (not owned) so callers control transaction
boundaries.

Usage:
    async with async_session_factory()() as session:
        repo = IPOEventRepository(session)
        ipo = await repo.get(some_uuid)
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from ...common.exceptions import DataNotFoundError
from ..models.base import Base

T = TypeVar("T", bound=Base)


# R7-8: columns excluded from the UPDATE set on conflict by default.
# ``id`` is the PK (overwriting it makes no semantic sense even if conflict
# targets a different UNIQUE constraint). ``created_at`` records the first
# time we saw the row — overwriting destroys that signal.
_UPSERT_DEFAULT_EXCLUDED: frozenset[str] = frozenset({"id", "created_at"})


def _build_update_cols(
    stmt: Any,
    sample: dict[str, Any],
    *,
    conflict_cols: list[str],
    update_columns: list[str] | None,
) -> dict[str, Any]:
    """R7-8 — assemble the ``ON CONFLICT DO UPDATE SET`` mapping.

    If ``update_columns`` is provided, ONLY those columns get the
    ``excluded.*`` reference. Otherwise every column from the input row
    is included EXCEPT the conflict targets AND the default exclusion set
    (``id`` / ``created_at``).
    """
    if update_columns is not None:
        return {k: stmt.excluded[k] for k in update_columns if k in sample}
    return {
        k: stmt.excluded[k]
        for k in sample
        if k not in conflict_cols and k not in _UPSERT_DEFAULT_EXCLUDED
    }


class BaseRepository(Generic[T]):
    """Async CRUD primitives over one ORM model."""

    model: type[T]

    def __init__(self, session: AsyncSession) -> None:
        if not hasattr(type(self), "model"):
            raise NotImplementedError(f"{type(self).__name__} must set class attribute `model`")
        self.session = session

    # ---------------------------------------------------------------- read

    async def get(self, entity_id: UUID) -> T | None:
        """Return one entity by primary key, or None."""
        return await self.session.get(self.model, entity_id)

    async def get_or_raise(self, entity_id: UUID) -> T:
        """Like `get` but raises DataNotFoundError on miss."""
        entity = await self.get(entity_id)
        if entity is None:
            raise DataNotFoundError(
                f"{type(self).__name__}: id={entity_id} not found",
                model=self.model.__tablename__,
                entity_id=str(entity_id),
            )
        return entity

    async def find_one(self, **filters: Any) -> T | None:
        """Return one row matching all equality filters, or None."""
        stmt = select(self.model).filter_by(**filters)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_one_or_raise(self, **filters: Any) -> T:
        try:
            stmt = select(self.model).filter_by(**filters)
            result = await self.session.execute(stmt)
            return result.scalar_one()
        except NoResultFound as exc:
            raise DataNotFoundError(
                f"{type(self).__name__}: filters={filters} not found",
                model=self.model.__tablename__,
                filters=filters,
            ) from exc

    async def list(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
        order_by: str | None = None,
        **filters: Any,
    ) -> list[T]:
        """List entities optionally filtered + ordered + paginated."""
        stmt = select(self.model)
        if filters:
            stmt = stmt.filter_by(**filters)
        if order_by:
            descending = order_by.startswith("-")
            col_name = order_by.lstrip("-")
            col = getattr(self.model, col_name)
            stmt = stmt.order_by(col.desc() if descending else col.asc())
        if offset:
            stmt = stmt.offset(offset)
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count(self, **filters: Any) -> int:
        stmt = select(func.count()).select_from(self.model)
        if filters:
            stmt = stmt.filter_by(**filters)
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    # --------------------------------------------------------------- write

    async def add(self, entity: T) -> T:
        """Add a new entity to the session (NOT flushed)."""
        self.session.add(entity)
        return entity

    async def add_all(self, entities: list[T]) -> list[T]:
        self.session.add_all(entities)
        return entities

    async def delete(self, entity_id: UUID) -> bool:
        """Delete by primary key. Returns True if a row was removed."""
        stmt = delete(self.model).where(self.model.id == entity_id)  # type: ignore[attr-defined]
        result = await self.session.execute(stmt)
        return (result.rowcount or 0) > 0

    async def upsert(
        self,
        values: dict[str, Any],
        *,
        conflict_cols: list[str],
        update_columns: list[str] | None = None,
    ) -> None:
        """Idempotent INSERT ... ON CONFLICT DO UPDATE.

        Args:
            values: column → value mapping for the row.
            conflict_cols: columns that form the unique conflict target.
            update_columns: R7-8 override — if provided, ONLY these columns
                are written on conflict; the default exclusion (``id``,
                ``created_at``) is bypassed. Use when the caller has stronger
                guarantees about which columns may legitimately change.

        R7-8: by default ``id`` and ``created_at`` are excluded from the
        UPDATE column set. Pre-R7-8 the default included every input column
        not in ``conflict_cols``, which clobbered the original insert
        timestamp on every re-upsert — breaking the TimestampMixin contract
        that ``created_at`` records "first time we saw this row".
        """
        stmt = pg_insert(self.model.__table__).values(**values)
        update_cols = _build_update_cols(
            stmt, values, conflict_cols=conflict_cols, update_columns=update_columns
        )
        if update_cols:
            stmt = stmt.on_conflict_do_update(
                index_elements=conflict_cols,
                set_=update_cols,
            )
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=conflict_cols)
        await self.session.execute(stmt)

    async def bulk_upsert(
        self,
        rows: list[dict[str, Any]],
        *,
        conflict_cols: list[str],
        batch_size: int = 500,
        update_columns: list[str] | None = None,
    ) -> int:
        """Bulk idempotent UPSERT. Returns total row count.

        R7-8: same default exclusion semantics as ``upsert`` —
        ``id`` and ``created_at`` are not overwritten unless
        ``update_columns`` explicitly lists them.
        """
        if not rows:
            return 0
        total = 0
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            stmt = pg_insert(self.model.__table__).values(batch)
            sample = batch[0]
            update_cols = _build_update_cols(
                stmt, sample, conflict_cols=conflict_cols, update_columns=update_columns
            )
            if update_cols:
                stmt = stmt.on_conflict_do_update(
                    index_elements=conflict_cols,
                    set_=update_cols,
                )
            else:
                stmt = stmt.on_conflict_do_nothing(index_elements=conflict_cols)
            await self.session.execute(stmt)
            total += len(batch)
        return total
