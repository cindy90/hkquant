"""Project-wide enums per PROJECT_SPEC.md §3.3 + §6 (v1.0 / v1.1 / v1.2 / v1.2.1)."""

from __future__ import annotations

from enum import StrEnum

# ============================================================================
# v1.0 — core domain
# ============================================================================


class ListingType(StrEnum):
    """HK listing chapter / classification."""

    CH18C_COMMERCIALIZED = "18C-COMM"
    CH18C_PRE_COMMERCIAL = "18C-PRE"
    CH18A_BIOTECH = "18A"
    MAINBOARD_TECH = "MB-TECH"
    AH_DUAL = "AH"
    MAINBOARD_OTHER = "MB-OTHER"


class AgentRole(StrEnum):
    """Seven expert agents per PROJECT_SPEC.md §7."""

    FUNDAMENTAL = "fundamental"
    INDUSTRY = "industry"
    VALUATION = "valuation"
    POLICY = "policy"
    LIQUIDITY = "liquidity"
    CORNERSTONE_SIGNAL = "cornerstone_signal"
    SENTIMENT = "sentiment"


class DecisionType(StrEnum):
    """Top-level final decision."""

    PARTICIPATE = "participate"
    PARTIAL = "partial"
    SKIP = "skip"
    WAIT_FOR_SIGNAL = "wait"


class RegulatoryRegime(StrEnum):
    """HK IPO pricing rules in effect at the analysis time-point."""

    PRE_20250804 = "pre_new_pricing"
    POST_20250804 = "post_new_pricing"


class ProspectusVersion(StrEnum):
    """Prospectus filing version."""

    PHIP = "PHIP"
    AP1 = "AP1"
    AP2 = "AP2"
    AP3 = "AP3"
    LISTING = "listing"


class AllocationMechanism(StrEnum):
    """Post-2025-08-04 allocation mechanism."""

    LEGACY = "legacy"
    MECHANISM_A = "A"
    MECHANISM_B = "B"


class CornerstoneCategory(StrEnum):
    """Cornerstone investor classification (NACS v8 inherited taxonomy; see ADR 0005 §1)."""

    SOVEREIGN = "sovereign"
    LOCAL_GOVT = "local_govt"
    STRATEGIC = "strategic"
    FOREIGN_LONG_TERM = "foreign_LT"
    FAMILY_OFFICE = "family_office"
    HEDGE = "hedge"
    INSURANCE = "insurance"
    BANK_WEALTH_MGMT = "bank_wm"
    INDUSTRY_UPSTREAM = "industry_upstream"
    INDUSTRY_DOWNSTREAM = "industry_downstream"
    OTHER = "other"


# ============================================================================
# v1.1 — prediction lifecycle
# ============================================================================


class DriftSignalType(StrEnum):
    """Drift signals raised by `learning_loop/drift_detector.py`."""

    ACCURACY_DROP = "accuracy_drop"
    VALUATION_BIAS = "valuation_bias"
    AGENT_CALIBRATION_DRIFT = "agent_calibration_drift"
    MISSING_FACTOR = "missing_factor"
    REGIME_BREAK = "regime_break"
    BEAR_MISS_RATE_HIGH = "bear_miss_rate_high"


class AdjustmentType(StrEnum):
    """Adjustment proposals by `learning_loop/adjustment_proposer.py`."""

    WEIGHT_CHANGE = "weight_change"
    PROMPT_EDIT = "prompt_edit"
    FACTOR_ADD = "factor_add"
    FACTOR_REMOVE = "factor_remove"
    LOGIC_CHANGE = "logic_change"
    AGENT_DISABLE = "agent_disable"


class AdjustmentStatus(StrEnum):
    """Review status of a proposed adjustment."""

    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    IMPLEMENTED = "implemented"


class PostIPOEventType(StrEnum):
    """Categories of post-IPO key event per PROJECT_SPEC.md §3.11."""

    EARNINGS = "earnings"
    PROFIT_WARNING = "profit_warning"
    MAJOR_CONTRACT = "major_contract"
    REGULATORY = "regulatory"
    MANAGEMENT_CHANGE = "management_change"
    CORNERSTONE_DISCLOSURE = "cornerstone_disclosure"
    PLACEMENT = "placement"
    SHARE_BUYBACK = "share_buyback"
    OTHER = "other"


class EventSeverity(StrEnum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"


# ============================================================================
# v1.2 — IPO lifecycle state machine + scheduler + alerts
# ============================================================================


class IPOLifecycleStateType(StrEnum):
    """IPO lifecycle state machine states per PROJECT_SPEC.md §3.11.1."""

    PRE_LISTING = "pre_listing"
    PRICING = "pricing"
    LISTED = "listed"
    WITHDRAWN = "withdrawn"
    HEARING_FAILED = "hearing_failed"
    PRICING_PULLED = "pricing_pulled"
    TERMINATED = "terminated"


# Legal state transitions per PROJECT_SPEC.md §6 VALID_TRANSITIONS.
# Any transition not listed here MUST raise InvalidStateTransition.
VALID_TRANSITIONS: dict[IPOLifecycleStateType, list[IPOLifecycleStateType]] = {
    IPOLifecycleStateType.PRE_LISTING: [
        IPOLifecycleStateType.PRICING,
        IPOLifecycleStateType.WITHDRAWN,
        IPOLifecycleStateType.HEARING_FAILED,
    ],
    IPOLifecycleStateType.PRICING: [
        IPOLifecycleStateType.LISTED,
        IPOLifecycleStateType.WITHDRAWN,
        IPOLifecycleStateType.PRICING_PULLED,
    ],
    IPOLifecycleStateType.LISTED: [
        IPOLifecycleStateType.TERMINATED,
    ],
    # WITHDRAWN / HEARING_FAILED / PRICING_PULLED / TERMINATED are terminal.
    IPOLifecycleStateType.WITHDRAWN: [],
    IPOLifecycleStateType.HEARING_FAILED: [],
    IPOLifecycleStateType.PRICING_PULLED: [],
    IPOLifecycleStateType.TERMINATED: [],
}


class TransitionTrigger(StrEnum):
    AUTO_DETECTOR = "auto_detector"
    MANUAL_REVIEWER = "manual_reviewer"
    TIMEOUT = "timeout"
    EVENT_DRIVEN = "event_driven"
    # R2-4: explicit "this is a manual correction, NOT a normal transition"
    # marker. Bypasses VALID_TRANSITIONS validation; requires reviewer +
    # justification. Auditors filter on this value to surface every
    # human-driven retroactive fix in the lifecycle history.
    CORRECTION = "correction"


class CodeMappingConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class CodeMappingSource(StrEnum):
    HKEX_ANNOUNCEMENT = "hkex_announcement"
    IFIND_MATCH = "ifind_match"
    MANUAL = "manual"
    HYBRID = "hybrid"


class SchedulerType(StrEnum):
    HIGH_FREQ = "high_freq"
    DAILY = "daily"
    EVENT_DRIVEN = "event_driven"


class SchedulerStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AlertLevel(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class EarningsAssessment(StrEnum):
    BEAT = "beat"
    IN_LINE = "in_line"
    MISS = "miss"
    SIGNIFICANT_MISS = "significant_miss"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# ============================================================================
# v1.2.1 — UI integration (RBAC + realtime events + chat)
# ============================================================================


class UserRole(StrEnum):
    """Six application roles per PROJECT_SPEC.md §6 / §16.5."""

    VIEWER = "viewer"
    REVIEWER = "reviewer"
    SENIOR_REVIEWER = "senior_reviewer"
    OPERATOR = "operator"
    ADMIN = "admin"
    AUDITOR = "auditor"


class SSOProvider(StrEnum):
    OKTA = "okta"
    AZURE_AD = "azure_ad"
    LOCAL = "local"


class Permission(StrEnum):
    """Fine-grained permissions per PROJECT_SPEC.md §6."""

    # ---- read ----
    READ_SNAPSHOTS = "snapshots.read"
    READ_REVIEWS = "reviews.read"
    READ_PROPOSALS = "proposals.read"
    READ_AUDIT = "audit.read"
    READ_SETTINGS = "settings.read"
    # ---- write ----
    SUBMIT_REVIEW = "reviews.submit"
    PROPOSE_ADJUSTMENT = "proposals.propose"
    ACCEPT_PROPOSAL = "proposals.accept"
    REJECT_PROPOSAL = "proposals.reject"
    ACK_ALERT = "alerts.acknowledge"
    TRIGGER_ANALYSIS = "analysis.trigger"
    RUN_WHATIF = "whatif.run"
    CHAT_WITH_AGENT = "chat.use"
    # ---- system ----
    MANAGE_CONFIG = "config.manage"
    MANAGE_USERS = "users.manage"
    MANAGE_SCHEDULER = "scheduler.manage"


# Role permission matrix per PROJECT_SPEC.md §6.
# Reviewer extends Viewer; Senior / Operator extend Reviewer; Admin extends Operator + Senior.
_BASE_READ: tuple[Permission, ...] = (
    Permission.READ_SNAPSHOTS,
    Permission.READ_REVIEWS,
    Permission.READ_PROPOSALS,
    Permission.READ_SETTINGS,
)

_REVIEWER_WRITE: tuple[Permission, ...] = (
    Permission.SUBMIT_REVIEW,
    Permission.PROPOSE_ADJUSTMENT,
    Permission.ACK_ALERT,
    Permission.TRIGGER_ANALYSIS,
    Permission.RUN_WHATIF,
    Permission.CHAT_WITH_AGENT,
)

_SENIOR_EXTRA: tuple[Permission, ...] = (
    Permission.ACCEPT_PROPOSAL,
    Permission.REJECT_PROPOSAL,
)

_OPERATOR_EXTRA: tuple[Permission, ...] = (
    Permission.MANAGE_CONFIG,
    Permission.MANAGE_SCHEDULER,
)

ROLE_PERMISSIONS: dict[UserRole, frozenset[Permission]] = {
    UserRole.VIEWER: frozenset(_BASE_READ),
    UserRole.REVIEWER: frozenset((*_BASE_READ, *_REVIEWER_WRITE)),
    UserRole.SENIOR_REVIEWER: frozenset((*_BASE_READ, *_REVIEWER_WRITE, *_SENIOR_EXTRA)),
    UserRole.OPERATOR: frozenset((*_BASE_READ, *_REVIEWER_WRITE, *_OPERATOR_EXTRA)),
    UserRole.ADMIN: frozenset(
        (*_BASE_READ, *_REVIEWER_WRITE, *_SENIOR_EXTRA, *_OPERATOR_EXTRA, Permission.MANAGE_USERS)
    ),
    UserRole.AUDITOR: frozenset((*_BASE_READ, Permission.READ_AUDIT)),
}


class RealtimeEventType(StrEnum):
    """SSE / WebSocket realtime event types per PROJECT_SPEC.md §6 / §16.3."""

    # alert
    ALERT_CREATED = "alert.created"
    ALERT_ACKNOWLEDGED = "alert.acknowledged"
    # snapshot
    SNAPSHOT_CREATED = "snapshot.created"
    SNAPSHOT_UPDATED = "snapshot.updated"
    # outcome
    OUTCOME_RECORDED = "outcome.recorded"
    CHECKPOINT_COMPLETED = "checkpoint.completed"
    # state machine
    STATE_TRANSITION = "lifecycle.state_transition"
    # scheduler
    SCHEDULER_STARTED = "scheduler.started"
    SCHEDULER_COMPLETED = "scheduler.completed"
    SCHEDULER_FAILED = "scheduler.failed"
    # learning loop
    DRIFT_DETECTED = "drift.detected"
    PROPOSAL_CREATED = "proposal.created"
    PROPOSAL_ACCEPTED = "proposal.accepted"
    ADJUSTMENT_APPLIED = "adjustment.applied"
    # system
    DASHBOARD_REFRESH = "dashboard.refresh"
    DATA_SOURCE_DEGRADED = "datasource.degraded"
    COST_THRESHOLD_HIT = "cost.threshold_hit"


class ChatMessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class AuditResourceType(StrEnum):
    SNAPSHOT = "snapshot"
    REVIEW = "review"
    PROPOSAL = "proposal"
    CONFIG = "config"
    PROMPT = "prompt"
    USER = "user"
    ALERT = "alert"
    CHAT_SESSION = "chat_session"


# ============================================================================
# Fixed checkpoint days (PROJECT_SPEC.md §11 prediction lifecycle constraint)
# ============================================================================

CHECKPOINT_DAYS: tuple[int, ...] = (1, 5, 10, 22, 30, 60, 90, 126, 180, 252, 360)
"""Outcome checkpoint days, fixed by PROJECT_SPEC.md §11. Must not be modified."""

CHECKPOINT_DAY_TERMINAL: int = -1
"""Marker checkpoint for terminal-state outcomes (WITHDRAWN / HEARING_FAILED)."""
