"""SQLAlchemy 2.0 DeclarativeBase + project-wide mixins.

Type-annotated style (PEP 593) with ``Mapped[...]`` / ``mapped_column()``.
mypy --strict friendly; no implicit Any.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

# Naming convention drives stable index / constraint names — important for Alembic
# auto-generated migrations to produce diff-friendly output.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    """Project-wide SQLAlchemy declarative base.

    All ORM classes get a generic ``__repr__`` of the form
    ``<ClassName id=... col1=val1 col2=val2>`` using up to 4 columns for brevity —
    helpful for debugging without dumping massive JSONB blobs.
    """

    metadata = metadata

    def __repr__(self) -> str:  # pragma: no cover — trivial debug helper
        cls = type(self).__name__
        cols = list(self.__table__.columns) if hasattr(self, "__table__") else []
        # Prefer id + the first 3 lightweight columns (skip JSONB / ARRAY / Text).
        lightweight = [
            c for c in cols
            if c.name == "id" or type(c.type).__name__ not in {"JSONB", "ARRAY", "Text"}
        ][:4]
        parts: list[str] = []
        for col in lightweight:
            try:
                value = getattr(self, col.name)
            except Exception:
                value = "?"
            parts.append(f"{col.name}={value!r}")
        return f"<{cls} {' '.join(parts)}>"


class UUIDMixin:
    """Adds UUID primary key (PostgreSQL native uuid type)."""

    @declared_attr  # type: ignore[arg-type]
    @classmethod
    def id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            PgUUID(as_uuid=True),
            primary_key=True,
            default=uuid.uuid4,
        )


class TimestampMixin:
    """Adds created_at + updated_at TIMESTAMPTZ columns."""

    @declared_attr  # type: ignore[arg-type]
    @classmethod
    def created_at(cls) -> Mapped[datetime]:
        return mapped_column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
            default=lambda: datetime.now(UTC),
        )

    @declared_attr  # type: ignore[arg-type]
    @classmethod
    def updated_at(cls) -> Mapped[datetime]:
        return mapped_column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
            onupdate=func.now(),
            default=lambda: datetime.now(UTC),
        )


__all__ = (
    "NAMING_CONVENTION",
    "Base",
    "TimestampMixin",
    "UUIDMixin",
    "metadata",
)
