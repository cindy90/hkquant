"""SQLAlchemy ORM models.

Layout per ADR 0006 (phased ORM rollout) + ADR 0012 (Phase 7.5 sub-stages):

- v1.0 base tables — Phase 1 (ipo / cornerstone / sponsor / comparable / prospectus / company)
- v1.1 prediction registry — Phase 7.5a (5 tables, snapshot has DB trigger)
- v1.2 lifecycle + operations — Phase 7.5a (lifecycle 3 tables + operations 3 tables)
- v1.2.1 UI integration — Phase 7.5a (user 2 + audit 1 + chat 2 + ui_support 3 tables;
  audit has DB trigger sharing the snapshot-trigger function)

All ORM classes register against the shared ``Base.metadata`` automatically;
explicit imports below are for Alembic env.py to pick up models reliably.
"""

from .audit import AuditLogRow
from .base import NAMING_CONVENTION, Base, TimestampMixin, UUIDMixin, metadata
from .chat import ChatMessageRow, ChatSessionRow
from .company import Company, FinancialSnapshotRow
from .comparable import ComparableCompany
from .cornerstone import CornerstoneInvestment, CornerstoneInvestor
from .ipo import IPOAllocation, IPOEvent, IPOPostMarket, IPOPricing
from .lifecycle import (
    CodeMappingRow,
    IPOLifecycleStateRow,
    IPOStateTransitionRow,
)
from .operations import (
    AlertRow,
    EarningsComparisonRow,
    SchedulerRunRow,
)
from .prediction import (
    ConfigVersionRow,
    PostIPOEventRow,
    PredictionOutcomeRow,
    PredictionReviewRow,
    PredictionSnapshotRow,
)
from .prospectus import ProspectusDoc, ProspectusExtractionRow
from .sponsor import Sponsor
from .ui_support import (
    APIRateLimitStateRow,
    RealtimeEventRow,
    WhatIfCalculationRow,
)
from .user import UserAccountRow, UserRoleRow

__all__ = (  # noqa: RUF022  — grouped by schema version, not alphabetical
    # base
    "NAMING_CONVENTION",
    "Base",
    "TimestampMixin",
    "UUIDMixin",
    "metadata",
    # v1.0 base tables
    "ComparableCompany",
    "Company",
    "CornerstoneInvestment",
    "CornerstoneInvestor",
    "FinancialSnapshotRow",
    "IPOAllocation",
    "IPOEvent",
    "IPOPostMarket",
    "IPOPricing",
    "ProspectusDoc",
    "ProspectusExtractionRow",
    "Sponsor",
    # v1.1 prediction registry
    "ConfigVersionRow",
    "PostIPOEventRow",
    "PredictionOutcomeRow",
    "PredictionReviewRow",
    "PredictionSnapshotRow",
    # v1.2 lifecycle + operations
    "AlertRow",
    "CodeMappingRow",
    "EarningsComparisonRow",
    "IPOLifecycleStateRow",
    "IPOStateTransitionRow",
    "SchedulerRunRow",
    # v1.2.1 UI integration
    "APIRateLimitStateRow",
    "AuditLogRow",
    "ChatMessageRow",
    "ChatSessionRow",
    "RealtimeEventRow",
    "UserAccountRow",
    "UserRoleRow",
    "WhatIfCalculationRow",
)
