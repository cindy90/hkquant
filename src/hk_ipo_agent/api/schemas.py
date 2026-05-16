"""API-layer request / response Pydantic models per PROJECT_SPEC.md §16.

Kept distinct from ``common/schemas.py`` (internal domain models) so that
UI clients see a stable contract and we can evolve internal models
without breaking the OpenAPI surface.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..common.enums import DecisionType, ListingType, UserRole
from ..common.schemas import (
    APIError,
    DashboardSummary,
    PaginatedResponse,
    PaginationMeta,
    PredictionSnapshot,
    WhatIfRequest,
    WhatIfResponse,
)


class APIBase(BaseModel):
    """Shared base — strict, no unknown fields."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class HealthResponse(APIBase):
    status: str = "ok"
    version: str
    environment: str
    uptime_seconds: float


class ReadyResponse(APIBase):
    ready: bool
    checks: dict[str, str] = Field(default_factory=dict)


class IPOListItem(APIBase):
    ipo_id: str
    company_name_zh: str
    company_name_en: str | None = None
    stock_code: str | None = None
    listing_type: ListingType
    industry_code: str
    pricing_date: date | None = None
    listing_date: date | None = None
    decision: DecisionType | None = None
    overall_score: float | None = None


class AnalysisRequest(APIBase):
    ipo_id: str
    prospectus_id: str
    as_of_date: date


class AnalysisStartResponse(APIBase):
    ipo_id: str
    run_id: UUID
    accepted_at: datetime


class SnapshotSummary(APIBase):
    """Lighter projection of ``PredictionSnapshot`` for list views."""

    id: UUID
    ipo_id: UUID
    as_of_date: date
    decision: DecisionType
    confidence: float
    price_range_low: Decimal
    price_range_fair: Decimal
    price_range_high: Decimal
    total_cost_usd: Decimal
    created_at: datetime


def snapshot_to_summary(snapshot: PredictionSnapshot) -> SnapshotSummary:
    """Project a full snapshot to summary view."""
    d = snapshot.decision
    return SnapshotSummary(
        id=snapshot.id,
        ipo_id=snapshot.ipo_id,
        as_of_date=snapshot.as_of_date,
        decision=d.decision,
        confidence=d.confidence,
        price_range_low=d.price_range_low,
        price_range_fair=d.price_range_fair,
        price_range_high=d.price_range_high,
        total_cost_usd=snapshot.total_cost_usd,
        created_at=snapshot.created_at,
    )


class ChatSessionCreate(APIBase):
    snapshot_id: UUID | None = None
    ipo_id: UUID | None = None
    title: str = "新会话"


class ChatMessageSend(APIBase):
    role: str = Field(pattern=r"^(user|assistant|system|tool)$")
    content: str = Field(min_length=1)
    content_json: dict[str, Any] | None = None


class AuditQuery(APIBase):
    user_id: UUID | None = None
    resource_type: str | None = None
    since: datetime | None = None
    limit: int = Field(default=50, ge=1, le=500)


class AlertAck(APIBase):
    note: str = ""


class LoginRequest(APIBase):
    email: str
    password: str


class LoginResponse(APIBase):
    access_token: str
    token_type: str = "bearer"
    expires_in_seconds: int
    user_id: UUID
    email: str
    roles: list[UserRole]


__all__ = (
    "APIBase",
    "APIError",
    "AlertAck",
    "AnalysisRequest",
    "AnalysisStartResponse",
    "AuditQuery",
    "ChatMessageSend",
    "ChatSessionCreate",
    "DashboardSummary",
    "HealthResponse",
    "IPOListItem",
    "LoginRequest",
    "LoginResponse",
    "PaginatedResponse",
    "PaginationMeta",
    "ReadyResponse",
    "SnapshotSummary",
    "WhatIfRequest",
    "WhatIfResponse",
    "snapshot_to_summary",
)
