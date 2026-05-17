"""Core Pydantic models per PROJECT_SPEC.md §6 (v1.0 / v1.1 / v1.2 / v1.2.1)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .enums import (
    AdjustmentStatus,
    AdjustmentType,
    AgentRole,
    AlertLevel,
    AuditResourceType,
    ChatMessageRole,
    CodeMappingConfidence,
    CodeMappingSource,
    Confidence,
    DecisionType,
    DriftSignalType,
    EarningsAssessment,
    EventSeverity,
    IPOLifecycleStateType,
    ListingType,
    PostIPOEventType,
    RealtimeEventType,
    SchedulerStatus,
    SchedulerType,
    SSOProvider,
    TransitionTrigger,
    UserRole,
)

# ============================================================================
# Helpers
# ============================================================================


class StrictModel(BaseModel):
    """Project-wide base model. Strict validation; forbid unknown fields by default."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=True,
        use_enum_values=False,
    )


class FrozenModel(StrictModel):
    """Immutable variant used for snapshot payloads."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=True,
        frozen=True,
    )


# ============================================================================
# v1.0 — Prospectus extraction
# ============================================================================


class Citation(StrictModel):
    """Page-level citation back to source document. Mandatory for all Findings."""

    page: int = Field(ge=1)
    section: str | None = None
    chunk_id: str | None = None
    text_snippet: str | None = None


class FinancialSnapshot(StrictModel):
    """One financial period for a company."""

    fiscal_year: int = Field(ge=1990, le=2100)
    fiscal_period: Literal["FY", "H1", "Q1", "Q2", "Q3", "5M", "9M"]
    revenue_rmb: Decimal | None = None
    gross_profit_rmb: Decimal | None = None
    gross_margin: float | None = Field(default=None, ge=-1.0, le=1.0)
    rd_expense_rmb: Decimal | None = None
    rd_pct_of_revenue: float | None = Field(default=None, ge=0.0)
    net_profit_rmb: Decimal | None = None
    adjusted_net_profit_rmb: Decimal | None = None
    operating_cash_flow_rmb: Decimal | None = None
    cash_balance_rmb: Decimal | None = None
    citation: Citation


class ShareholderEntry(StrictModel):
    name: str
    pct_pre_ipo: float = Field(ge=0.0, le=1.0)
    is_controlling: bool
    is_pre_ipo_investor: bool
    last_round_valuation_rmb: Decimal | None = None
    last_round_date: date | None = None
    has_buyback_clause: bool = False
    citation: Citation


class CustomerConcentration(StrictModel):
    fiscal_year: int = Field(ge=1990, le=2100)
    top1_pct: float = Field(ge=0.0, le=1.0)
    top5_pct: float = Field(ge=0.0, le=1.0)
    top1_name: str | None = None
    citation: Citation


class RiskFactor(StrictModel):
    category: Literal["business", "industry", "financial", "regulatory", "macro", "structural"]
    description: str
    severity: Literal["high", "medium", "low"]
    citation: Citation


class Ch18CQualification(StrictModel):
    is_commercialized: bool
    revenue_threshold_met: bool
    rd_intensity_met: bool
    market_cap_threshold_hkd: Decimal
    lead_investors: list[str] = Field(default_factory=list)
    citation: Citation


class ProspectusExtraction(StrictModel):
    """Complete structured extraction from a prospectus PDF.

    The single most important data object in the system. All agents consume this.
    """

    prospectus_id: str
    company_name_zh: str
    company_name_en: str | None = None
    stock_code: str | None = None
    listing_type: ListingType
    industry_code: str
    industry_description: str

    # Business
    business_model: str
    revenue_streams: list[dict[str, Any]] = Field(default_factory=list)
    customer_concentration: list[CustomerConcentration] = Field(default_factory=list)
    supplier_concentration: list[CustomerConcentration] = Field(default_factory=list)

    # Financials
    financials: list[FinancialSnapshot] = Field(default_factory=list)

    # Shareholder structure
    shareholders: list[ShareholderEntry] = Field(default_factory=list)
    pre_ipo_valuation_rmb: Decimal | None = None
    last_round_date: date | None = None

    # 18C / 18A specific
    ch18c_qualification: Ch18CQualification | None = None

    # AH specific
    a_share_code: str | None = None
    a_share_price_at_filing: Decimal | None = None

    # Use of proceeds
    use_of_proceeds: list[dict[str, Any]] = Field(default_factory=list)

    # Risks
    risk_factors: list[RiskFactor] = Field(default_factory=list)

    # Intermediaries
    sponsors: list[str] = Field(default_factory=list)

    # Metadata
    extraction_version: str
    extracted_at: datetime
    needs_human_review: bool = False
    review_reasons: list[str] = Field(default_factory=list)


# ============================================================================
# v1.0 — Agent output
# ============================================================================


class Finding(StrictModel):
    """A single agent finding. Citations are mandatory."""

    statement: str
    evidence: str
    citations: list[Citation] = Field(min_length=1)
    confidence: Confidence


class DataSource(StrictModel):
    source: Literal[
        "prospectus",
        "ifind",
        "hkex",
        "kb_cornerstones",
        "kb_sponsors",
        "kb_comparables",
        "web_search",
        "themes",
    ]
    detail: str


class AgentOutput(StrictModel):
    """Output contract for every expert agent."""

    agent_role: AgentRole
    scores: dict[str, float]
    overall_score: float = Field(ge=0.0, le=100.0)
    key_findings: list[Finding] = Field(default_factory=list)
    uncertainty_flags: list[str] = Field(default_factory=list)
    data_sources_used: list[DataSource] = Field(default_factory=list)
    cost_usd: Decimal = Decimal("0")
    runtime_seconds: float = Field(ge=0.0)


# ============================================================================
# v1.0 — Valuation
# ============================================================================


class ValuationDistribution(StrictModel):
    p10: Decimal
    p25: Decimal
    p50: Decimal
    p75: Decimal
    p90: Decimal
    mean: Decimal
    std: Decimal


class SingleModelValuation(StrictModel):
    model_name: str
    applicable: bool
    valuation_distribution: ValuationDistribution
    key_assumptions: dict[str, Any] = Field(default_factory=dict)
    citations: list[Citation] = Field(default_factory=list)


class ValuationEnsembleOutput(StrictModel):
    company_id: str
    single_models: list[SingleModelValuation]
    weights_used: dict[str, float]
    ensemble_distribution: ValuationDistribution
    implied_price_range: dict[str, Decimal]  # {low, fair, high}
    notes: list[str] = Field(default_factory=list)


# ============================================================================
# v1.0 — Debate
# ============================================================================


class DebateRound(StrictModel):
    round_number: int = Field(ge=1)
    bull_argument: str
    bear_argument: str
    devil_challenge: str
    resolution: str | None = None


class DebateOutput(StrictModel):
    rounds: list[DebateRound] = Field(default_factory=list)
    final_consensus: str
    unresolved_issues: list[str] = Field(default_factory=list)


# ============================================================================
# v1.0 — Final decision
# ============================================================================


class TriggerRule(StrictModel):
    condition: str
    action: str
    severity: AlertLevel


class FinalDecision(StrictModel):
    decision: DecisionType
    confidence: float = Field(ge=0.0, le=1.0)
    suggested_allocation_pct: float | None = Field(default=None, ge=0.0, le=1.0)
    price_range_low: Decimal
    price_range_fair: Decimal
    price_range_high: Decimal
    expected_return_6m: ValuationDistribution
    expected_return_12m: ValuationDistribution

    scorecard: dict[str, float] = Field(default_factory=dict)

    key_reasons_for: list[str] = Field(default_factory=list)
    key_reasons_against: list[str] = Field(default_factory=list)

    trigger_rules: list[TriggerRule] = Field(default_factory=list)

    references_to_agent_outputs: list[str] = Field(default_factory=list)


# ============================================================================
# v1.1 — Prediction lifecycle
# ============================================================================


class PredictionSnapshot(FrozenModel):
    """Immutable prediction snapshot. Frozen at Pydantic layer; DB trigger enforces too.

    See PROJECT_SPEC.md §11 / §3.11.
    """

    id: UUID
    ipo_id: UUID
    as_of_date: date
    prospectus_version: str

    input_data_hash: str
    input_data_snapshot: dict[str, Any]

    agent_outputs: dict[str, AgentOutput]
    valuation_output: ValuationEnsembleOutput
    debate_output: DebateOutput
    decision: FinalDecision

    system_version: str
    model_versions: dict[str, str]
    config_snapshot: dict[str, Any]
    total_cost_usd: Decimal
    runtime_seconds: float = Field(ge=0.0)

    created_at: datetime


class PostIPOEvent(StrictModel):
    event_date: date
    event_type: PostIPOEventType
    severity: EventSeverity
    description: str
    source_url: str | None = None
    price_impact_1d: float | None = None
    price_impact_5d: float | None = None


class PredictionOutcome(StrictModel):
    """T+N checkpoint outcome per PROJECT_SPEC.md §3.11."""

    snapshot_id: UUID
    checkpoint_day: int  # -1 (terminal) or one of CHECKPOINT_DAYS

    return_since_ipo: float
    return_since_listing: float | None = None
    max_drawdown: float
    relative_return_hsi: float
    relative_return_hstech: float
    relative_return_industry: float

    events_in_window: list[PostIPOEvent] = Field(default_factory=list)
    earnings_released: bool = False
    earnings_beat_extraction: bool | None = None

    cornerstone_held_pct: float | None = None
    cornerstone_reduced: bool | None = None
    # R2-5: explicit uncertainty marker per CLAUDE.md «基石减持检测的不确定性必须
    # 显式标注». Set to True whenever the underlying cornerstone-tracking data
    # source (iFind disclosure scan / HKEX filing parse) returned a partial or
    # ambiguous result. Downstream reviewers and the learning loop must NOT
    # treat ``cornerstone_reduced`` as authoritative when this flag is True.
    cornerstone_tracking_unreliable: bool = False

    price_in_predicted_range: bool
    decision_correct: bool

    recorded_at: datetime


class AgentErrorAnalysis(StrictModel):
    agent_role: AgentRole
    score_calibration: float
    findings_accuracy: float = Field(ge=0.0, le=1.0)
    critical_misses: list[str] = Field(default_factory=list)
    critical_correct_calls: list[str] = Field(default_factory=list)


class ValuationErrorAnalysis(StrictModel):
    model_name: str
    predicted_p50: Decimal
    actual_price: Decimal
    pct_error: float
    in_p10_p90_range: bool


class DebateQualityAnalysis(StrictModel):
    bear_predictions_validated: int = Field(ge=0)
    bear_predictions_total: int = Field(ge=0)
    bull_predictions_validated: int = Field(ge=0)
    bull_predictions_total: int = Field(ge=0)
    unaddressed_critical_risks: list[str] = Field(default_factory=list)


class ProposedAdjustment(StrictModel):
    target_path: str
    adjustment_type: AdjustmentType
    current_value: Any
    proposed_value: Any
    rationale: str
    evidence_snapshot_ids: list[UUID] = Field(default_factory=list)
    expected_impact: str
    confidence: Confidence


class Attribution(StrictModel):
    snapshot_id: UUID
    checkpoint_day: int

    agent_errors: list[AgentErrorAnalysis] = Field(default_factory=list)
    valuation_errors: list[ValuationErrorAnalysis] = Field(default_factory=list)
    debate_quality: DebateQualityAnalysis

    primary_attribution: str
    llm_diagnosis: str
    proposed_adjustments: list[ProposedAdjustment] = Field(default_factory=list)


class PredictionReview(StrictModel):
    """Human review note (the only append allowed against prediction lifecycle data)."""

    snapshot_id: UUID
    review_checkpoint_day: int
    reviewer: str

    what_we_got_right: str
    what_we_got_wrong: str

    primary_attribution: str
    attribution_details: Attribution

    proposed_adjustments: list[ProposedAdjustment] = Field(default_factory=list)
    adjustment_status: AdjustmentStatus
    applied_at: datetime | None = None
    applied_version: str | None = None

    notes_md: str = ""
    created_at: datetime


class DriftSignal(StrictModel):
    detection_time: datetime
    signal_type: DriftSignalType
    severity: AlertLevel
    affected_dimensions: dict[str, str] = Field(default_factory=dict)
    metric_value: float
    threshold: float
    sample_count: int = Field(ge=0)
    evidence: str
    related_snapshot_ids: list[UUID] = Field(default_factory=list)


# ============================================================================
# v1.2 — Lifecycle state machine + scheduler + alerts
# ============================================================================


class IPOLifecycleState(StrictModel):
    ipo_id: UUID
    current_state: IPOLifecycleStateType
    state_entered_at: datetime
    state_metadata: dict[str, Any] = Field(default_factory=dict)
    last_checked_at: datetime
    is_terminal: bool


class StateTransition(StrictModel):
    ipo_id: UUID
    from_state: IPOLifecycleStateType | None = None
    to_state: IPOLifecycleStateType
    transition_at: datetime
    triggered_by: TransitionTrigger
    detection_evidence: dict[str, Any] = Field(default_factory=dict)
    reviewer: str | None = None


class CodeMapping(StrictModel):
    ipo_id: UUID
    company_name_zh: str
    company_name_en: str | None = None
    hk_stock_code: str | None = None
    a_share_code: str | None = None
    us_adr_code: str | None = None
    confirmed_at: datetime
    confirmation_source: CodeMappingSource
    confidence: CodeMappingConfidence
    requires_review: bool = False


class SchedulerRun(StrictModel):
    scheduler_type: SchedulerType
    run_id: str
    started_at: datetime
    completed_at: datetime | None = None
    snapshots_processed: int = Field(default=0, ge=0)
    events_detected: int = Field(default=0, ge=0)
    errors_encountered: int = Field(default=0, ge=0)
    error_details: list[dict[str, Any]] | None = None
    status: SchedulerStatus


class Alert(StrictModel):
    """Alert with mandatory actionable_info — see CLAUDE.md v1.2 constraints."""

    level: AlertLevel
    category: str
    related_ipo_id: UUID | None = None
    related_snapshot_id: UUID | None = None
    message: str
    actionable_info: str  # required: what to do, not just "failed"
    detected_at: datetime
    acknowledged_at: datetime | None = None
    acknowledged_by: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EarningsComparison(StrictModel):
    snapshot_id: UUID
    report_period: str
    filing_date: date

    actual_revenue: Decimal | None = None
    predicted_revenue_from_prospectus: Decimal | None = None
    revenue_deviation_pct: float | None = None

    actual_net_profit: Decimal | None = None
    predicted_net_profit: Decimal | None = None
    profit_deviation_pct: float | None = None

    actual_gross_margin: float | None = None
    predicted_gross_margin: float | None = None
    margin_deviation_pp: float | None = None

    qualitative_deviations: list[str] = Field(default_factory=list)
    overall_assessment: EarningsAssessment
    confidence: Confidence
    notes: str = ""
    requires_human_review: bool = False


# ============================================================================
# v1.2.1 — UI integration (auth + audit + chat + whatif + realtime + dashboard)
# ============================================================================


class UserAccount(StrictModel):
    id: UUID
    email: str
    display_name: str | None = None
    sso_provider: SSOProvider
    sso_subject: str
    is_active: bool = True
    roles: list[UserRole] = Field(default_factory=list)
    last_login_at: datetime | None = None


class AuditLog(StrictModel):
    id: UUID
    user_id: UUID | None = None
    user_email: str | None = None
    action: str
    resource_type: AuditResourceType | None = None
    resource_id: str | None = None
    before_state: dict[str, Any] | None = None
    after_state: dict[str, Any] | None = None
    diff: dict[str, Any] | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    request_id: str | None = None
    api_endpoint: str | None = None
    success: bool = True
    error_message: str | None = None
    occurred_at: datetime


class ChatSession(StrictModel):
    id: UUID
    user_id: UUID
    snapshot_id: UUID | None = None
    ipo_id: UUID | None = None
    title: str
    created_at: datetime
    last_active_at: datetime
    archived: bool = False


class ChatMessage(StrictModel):
    id: UUID
    session_id: UUID
    role: ChatMessageRole
    content: str
    content_json: dict[str, Any] | None = None
    citations: list[Citation] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)
    cost_usd: Decimal | None = None
    tokens_input: int | None = Field(default=None, ge=0)
    tokens_output: int | None = Field(default=None, ge=0)
    model_used: str | None = None
    runtime_ms: int | None = Field(default=None, ge=0)
    sequence: int = Field(ge=0)
    created_at: datetime


class WhatIfRequest(StrictModel):
    """What-If valuation request per PROJECT_SPEC.md §16.9."""

    snapshot_id: UUID
    modified_assumptions: dict[str, Any]


class WhatIfResponse(StrictModel):
    calculation_id: UUID
    original_distribution: ValuationDistribution
    new_distribution: ValuationDistribution
    delta_summary: dict[str, float]
    affected_models: list[str] = Field(default_factory=list)
    cost_usd: Decimal
    runtime_ms: int = Field(ge=0)


class RealtimeEvent(StrictModel):
    event_type: RealtimeEventType
    related_ipo_id: UUID | None = None
    related_snapshot_id: UUID | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class APIError(StrictModel):
    """RFC 7807 Problem Details. See PROJECT_SPEC.md §16.8."""

    type: str
    title: str
    status: int
    detail: str
    instance: str | None = None
    request_id: str | None = None
    validation_errors: list[dict[str, Any]] | None = None


class PaginationMeta(StrictModel):
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    has_next: bool


class PaginatedResponse(StrictModel):
    """All list endpoints MUST use this envelope. See PROJECT_SPEC.md §16."""

    data: list[Any]
    meta: PaginationMeta


class DashboardSummary(StrictModel):
    critical_alerts_count: int = Field(ge=0)
    pending_reviews_count: int = Field(ge=0)
    pending_proposals_count: int = Field(ge=0)
    overdue_checkpoints_count: int = Field(ge=0)
    active_snapshots: list[dict[str, Any]] = Field(default_factory=list)
    upcoming_events: list[dict[str, Any]] = Field(default_factory=list)
    system_health: dict[str, str] = Field(default_factory=dict)
    cost_summary: dict[str, Decimal] = Field(default_factory=dict)
