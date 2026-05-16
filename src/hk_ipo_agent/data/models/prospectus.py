"""Prospectus ORM per PROJECT_SPEC.md §5.

Tables: ``prospectus_docs`` (PDF metadata + path) and ``prospectus_extractions``
(structured ProspectusExtraction JSONB).
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, Date, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from .ipo import IPOEvent


class ProspectusDoc(UUIDMixin, TimestampMixin, Base):
    """One prospectus filing version (PHIP / AP1 / AP2 / listing)."""

    __tablename__ = "prospectus_docs"

    ipo_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("ipo_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[str | None] = mapped_column(String(50))  # ProspectusVersion value
    filing_date: Mapped[date | None] = mapped_column(Date)
    pdf_path: Mapped[str | None] = mapped_column(String(500))
    page_count: Mapped[int | None] = mapped_column()

    ipo: Mapped[IPOEvent] = relationship(back_populates="prospectus_docs")
    extractions: Mapped[list[ProspectusExtractionRow]] = relationship(
        back_populates="prospectus", cascade="all, delete-orphan"
    )


class ProspectusExtractionRow(UUIDMixin, TimestampMixin, Base):
    """Persisted ``ProspectusExtraction`` Pydantic payload as JSONB.

    Class name disambiguated from ``common.schemas.ProspectusExtraction`` (the
    Pydantic model). The table name remains ``prospectus_extractions`` per spec §5.
    """

    __tablename__ = "prospectus_extractions"

    prospectus_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("prospectus_docs.id", ondelete="CASCADE"),
        nullable=False,
    )
    extraction: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    extraction_version: Mapped[str | None] = mapped_column(String(20))
    needs_human_review: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    prospectus: Mapped[ProspectusDoc] = relationship(back_populates="extractions")
