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


class BaseRepository(Generic[T]):
    """Async CRUD primitives over one ORM model."""

    model: type[T]

    def __init__(self, session: AsyncSession) -> None:
        if not hasattr(type(self), "model"):
            raise NotImplementedError(
                f"{type(self).__name__} must set class attribute `model`"
            )
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

    async def upsert(self, values: dict[str, Any], *, conflict_cols: list[str]) -> None:
        """Idempotent INSERT ... ON CONFLICT DO UPDATE.

        Args:
            values: column → value mapping for the row.
            conflict_cols: columns that form the unique conflict target.
        """
        stmt = pg_insert(self.model.__table__).values(**values)
        update_cols = {k: stmt.excluded[k] for k in values if k not in conflict_cols}
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
    ) -> int:
        """Bulk idempotent UPSERT. Returns total row count."""
        if not rows:
            return 0
        total = 0
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            stmt = pg_insert(self.model.__table__).values(batch)
            sample = batch[0]
            update_cols = {k: stmt.excluded[k] for k in sample if k not in conflict_cols}
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
