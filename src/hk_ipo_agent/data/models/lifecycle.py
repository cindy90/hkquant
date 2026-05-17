"""IPO lifecycle ORM models — v1.2 tables per PROJECT_SPEC.md §3.11.1 + §5.

Tables landed in Phase 7.5a per ADR 0012 (state machine + code mapping
schema land now; business logic in 7.5c).

- ``ipo_lifecycle_states`` — one current state per IPO; updated by
  ``ipo_lifecycle/state_machine.py`` (Phase 7.5c).
- ``ipo_state_transitions`` — append-only transition audit log; every
  ``transition_to`` call writes here.
- ``code_mappings`` — company-name → stock-code mapping with confidence
  tier; low-confidence rows raise ``requires_review=True``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDMixin


class IPOLifecycleStateRow(UUIDMixin, Base):
    """Current lifecycle state — PROJECT_SPEC.md §5 ``ipo_lifecycle_states``.

    Exactly one row per IPO (UNIQUE on ``ipo_id``). Phase 7.5c
    ``state_machine.py`` is the only writer.
    """

    __tablename__ = "ipo_lifecycle_states"

    ipo_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("ipo_events.id"),
        nullable=False,
        unique=True,
    )
    current_state: Mapped[str] = mapped_column(String(30), nullable=False)
    # enums.IPOLifecycleStateType: PRE_LISTING/PRICING/LISTED/WITHDRAWN/
    # HEARING_FAILED/PRICING_PULLED/TERMINATED
    state_entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    last_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_terminal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class IPOStateTransitionRow(UUIDMixin, Base):
    """Append-only transition audit log — PROJECT_SPEC.md §5 ``ipo_state_transitions``.

    State machine MUST write here on every ``transition_to`` call. Never
    UPDATE; corrections are new transitions with ``triggered_by='manual_reviewer'``.
    """

    __tablename__ = "ipo_state_transitions"

    ipo_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("ipo_events.id"),
        nullable=False,
    )
    from_state: Mapped[str | None] = mapped_column(String(30))
    to_state: Mapped[str] = mapped_column(String(30), nullable=False)
    transition_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    triggered_by: Mapped[str] = mapped_column(String(50), nullable=False)
    # enums.TransitionTrigger: auto_detector / manual_reviewer / timeout / event_driven
    detection_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reviewer: Mapped[str | None] = mapped_column(String(100))

    __table_args__ = (Index("idx_transitions_ipo", "ipo_id", "transition_at"),)


class CodeMappingRow(UUIDMixin, Base):
    """Company name → stock-code mapping — PROJECT_SPEC.md §5 ``code_mappings``.

    Phase 7.5c ``code_mapper.py`` writes here. Low-confidence rows MUST
    set ``requires_review=True`` and emit an alert.
    """

    __tablename__ = "code_mappings"

    ipo_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("ipo_events.id"),
        nullable=False,
        unique=True,
    )
    company_name_zh: Mapped[str | None] = mapped_column(String(200))
    company_name_en: Mapped[str | None] = mapped_column(String(200))
    hk_stock_code: Mapped[str | None] = mapped_column(String(10))
    a_share_code: Mapped[str | None] = mapped_column(String(10))
    us_adr_code: Mapped[str | None] = mapped_column(String(10))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmation_source: Mapped[str | None] = mapped_column(String(50))
    # enums.CodeMappingSource: hkex_announcement / ifind_match / manual / hybrid
    confidence: Mapped[str | None] = mapped_column(String(20))
    # enums.CodeMappingConfidence: high / medium / low
    requires_review: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


__all__ = (
    "CodeMappingRow",
    "IPOLifecycleStateRow",
    "IPOStateTransitionRow",
)
