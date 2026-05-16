"""Custom exception hierarchy per PROJECT_SPEC.md §10.3.

All project exceptions inherit from `HkIpoAgentException` so that catch-all
handlers (FastAPI error middleware, LangGraph node error policy, schedulers)
can distinguish project errors from third-party / standard-library errors.
"""

from __future__ import annotations

from typing import Any


class HkIpoAgentException(Exception):
    """Root exception. All project exceptions inherit from this."""

    default_message: str = "HK IPO Agent error"

    def __init__(self, message: str | None = None, /, **context: Any) -> None:
        super().__init__(message or self.default_message)
        self.context: dict[str, Any] = context

    def __repr__(self) -> str:
        return f"{type(self).__name__}({super().__str__()!r}, context={self.context!r})"


# ============================================================================
# Configuration / settings
# ============================================================================


class ConfigurationError(HkIpoAgentException):
    """Misconfiguration: missing env var, malformed YAML, invalid override."""

    default_message = "Configuration error"


class MissingDependencyError(HkIpoAgentException):
    """A required runtime dependency (e.g. iFinDPy, LlamaParse key) is missing."""

    default_message = "Required runtime dependency is not configured"


# ============================================================================
# Data sources
# ============================================================================


class DataSourceError(HkIpoAgentException):
    """Generic upstream data source failure."""

    default_message = "Data source error"


class DataSourceUnavailableError(DataSourceError):
    """Data source endpoint unreachable / rate-limited / authentication failed."""

    default_message = "Data source unavailable"


class DataNotFoundError(DataSourceError):
    """Requested record not present in upstream data source."""

    default_message = "Requested data not found"


class DataQualityError(DataSourceError):
    """Returned data fails quality checks (missing required fields, suspicious values)."""

    default_message = "Data failed quality validation"


class LookAheadError(DataSourceError):
    """Detected look-ahead leak: attempted to read data dated after as_of_date.

    Raised by `backtest/as_of_data.AsOfDataProvider`. See PROJECT_SPEC.md §3.9.
    """

    default_message = "Look-ahead leak detected — access to future data blocked"


# ============================================================================
# Prospectus parsing / extraction
# ============================================================================


class ProspectusError(HkIpoAgentException):
    """Generic prospectus pipeline failure."""

    default_message = "Prospectus pipeline error"


class ParseError(ProspectusError):
    """PDF parsing failed (LlamaParse + PyMuPDF both exhausted)."""

    default_message = "Prospectus PDF parsing failed"


class ExtractionError(ProspectusError):
    """Structured extraction failed validation."""

    default_message = "Prospectus extraction failed validation"


class CitationRequiredError(ProspectusError):
    """A Finding was emitted without a citation. See CLAUDE.md strict constraints."""

    default_message = "Finding emitted without citation — citations are mandatory"


# ============================================================================
# LLM
# ============================================================================


class LLMError(HkIpoAgentException):
    """Generic LLM call failure."""

    default_message = "LLM call failed"


class LLMTimeoutError(LLMError):
    """LLM call exceeded timeout."""

    default_message = "LLM call timed out"


class LLMRateLimitError(LLMError):
    """LLM provider returned rate-limit error after retries exhausted."""

    default_message = "LLM rate limit exceeded"


class LLMCostExceededError(LLMError):
    """Single call or aggregate spending exceeded budget. See PROJECT_SPEC.md §12."""

    default_message = "LLM cost guard tripped"


class LLMOutputValidationError(LLMError):
    """LLM JSON output failed Pydantic validation across all retries."""

    default_message = "LLM output failed schema validation"


# ============================================================================
# Prediction registry (v1.1)
# ============================================================================


class PredictionRegistryError(HkIpoAgentException):
    """Generic prediction registry failure."""

    default_message = "Prediction registry error"


class SnapshotIntegrityError(PredictionRegistryError):
    """Snapshot SHA256 hash mismatch on read, or attempted mutation.

    See PROJECT_SPEC.md §11 (predictions immutable).
    """

    default_message = "Prediction snapshot integrity violated"


class SnapshotImmutabilityError(PredictionRegistryError):
    """Attempted UPDATE / DELETE on prediction_snapshots table."""

    default_message = "prediction_snapshots is immutable; use prediction_reviews for notes"


class SnapshotCreationFailed(PredictionRegistryError):
    """Snapshot persistence failed inside the orchestrator hard edge.

    Per ADR 0012 (Phase 7.5a) the orchestrator must propagate this so the
    graph fails rather than silently advancing to ``report`` without an
    audit trail. CLAUDE.md prediction-lifecycle constraint: "any complete
    analysis MUST create a snapshot before emitting a decision."
    """

    default_message = (
        "Snapshot creation failed in orchestrator hard edge "
        "(synthesize → create_snapshot → report)"
    )


# ============================================================================
# IPO lifecycle state machine (v1.2)
# ============================================================================


class LifecycleError(HkIpoAgentException):
    """Generic IPO lifecycle failure."""

    default_message = "IPO lifecycle error"


class InvalidStateTransition(LifecycleError):
    """Attempted transition not in VALID_TRANSITIONS. See PROJECT_SPEC.md §11."""

    default_message = "Invalid IPO lifecycle state transition"


class StaleStateError(LifecycleError):
    """IPO has been stuck in a non-terminal state past timeout (see stale_detector)."""

    default_message = "IPO state is stale"


class CodeMappingAmbiguousError(LifecycleError):
    """code_mapper returned low confidence; requires manual review."""

    default_message = "Stock code mapping ambiguous; requires human review"


# ============================================================================
# Scheduler (v1.2)
# ============================================================================


class SchedulerError(HkIpoAgentException):
    """Generic scheduler failure."""

    default_message = "Scheduler error"


class SchedulerLockError(SchedulerError):
    """Could not acquire scheduler advisory lock (overlapping run prevented)."""

    default_message = "Scheduler lock acquisition failed"


class SchedulerIdempotencyError(SchedulerError):
    """Same (snapshot_id, checkpoint_day) attempted twice. Should be a no-op."""

    default_message = "Scheduler idempotency violation"


# ============================================================================
# Learning loop (v1.1 / v1.2)
# ============================================================================


class LearningLoopError(HkIpoAgentException):
    """Generic learning loop failure."""

    default_message = "Learning loop error"


class AdjustmentNotApprovedError(LearningLoopError):
    """adjustment_applier called on a proposal without reviewer acceptance.

    See PROJECT_SPEC.md §11 prediction lifecycle constraints.
    """

    default_message = "Adjustment not approved by human reviewer"


class CalibrationRegressionError(LearningLoopError):
    """Post-apply small backtest showed regression vs baseline; rollback triggered."""

    default_message = "Calibration regression detected"


# ============================================================================
# API / Auth (v1.2.1)
# ============================================================================


class ApiError(HkIpoAgentException):
    """Base for API layer errors. Maps to RFC 7807 Problem Details."""

    default_message = "API error"
    http_status: int = 500
    problem_type: str = "about:blank"


class AuthenticationError(ApiError):
    default_message = "Authentication failed"
    http_status = 401
    problem_type = "https://api.example.com/errors/unauthorized"


class AuthorizationError(ApiError):
    default_message = "Insufficient permissions"
    http_status = 403
    problem_type = "https://api.example.com/errors/forbidden"


class ResourceNotFoundError(ApiError):
    default_message = "Resource not found"
    http_status = 404
    problem_type = "https://api.example.com/errors/not-found"


class ConflictError(ApiError):
    default_message = "Resource state conflict"
    http_status = 409
    problem_type = "https://api.example.com/errors/conflict"


class ValidationError(ApiError):
    default_message = "Validation failed"
    http_status = 422
    problem_type = "https://api.example.com/errors/validation-failed"


class RateLimitError(ApiError):
    default_message = "Rate limit exceeded"
    http_status = 429
    problem_type = "https://api.example.com/errors/rate-limited"


class CostGuardError(ApiError):
    """LLM cost guard tripped at the API layer."""

    default_message = "Cost guard exceeded"
    http_status = 429
    problem_type = "https://api.example.com/errors/cost-guard"
