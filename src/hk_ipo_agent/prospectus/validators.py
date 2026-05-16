"""Extraction consistency validators per PROJECT_SPEC.md §3.5.

These run after ``ProspectusExtractor.extract()`` finishes and return a
list of warnings. The extraction is rejected (``needs_human_review=True``)
when any validator returns a "high" severity issue.

Validators here are deliberately conservative — they encode hard
arithmetic invariants and known-good ranges, not statistical sanity
(which the Fundamental Agent in Phase 5 will handle).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from ..common.logging import get_logger
from .schema import ProspectusExtraction

log = get_logger(__name__)


@dataclass
class ValidationIssue:
    severity: Literal["high", "medium", "low"]
    code: str
    message: str
    field: str | None = None


def validate(extraction: ProspectusExtraction) -> list[ValidationIssue]:
    """Run all validators and return issues. Mutates `extraction.needs_human_review`."""
    issues: list[ValidationIssue] = []
    issues.extend(_validate_financials_monotone(extraction))
    issues.extend(_validate_customer_concentration_bounds(extraction))
    issues.extend(_validate_risk_factor_present(extraction))
    issues.extend(_validate_shareholder_pct_sum(extraction))
    issues.extend(_validate_18c_self_consistent(extraction))

    if any(i.severity == "high" for i in issues):
        extraction.needs_human_review = True
        for issue in issues:
            if issue.severity == "high":
                extraction.review_reasons.append(f"{issue.code}: {issue.message}")
    log.info(
        "extraction_validated",
        prospectus_id=extraction.prospectus_id,
        issues=[i.code for i in issues],
        needs_review=extraction.needs_human_review,
    )
    return issues


# ---------------------------------------------------------------------------
# Individual validators
# ---------------------------------------------------------------------------


def _validate_financials_monotone(
    extraction: ProspectusExtraction,
) -> list[ValidationIssue]:
    """Catch trivially impossible: negative revenue, gross_margin > 1 etc."""
    issues: list[ValidationIssue] = []
    for snap in extraction.financials:
        if snap.revenue_rmb is not None and snap.revenue_rmb < 0:
            issues.append(
                ValidationIssue(
                    severity="high",
                    code="negative_revenue",
                    message=f"FY{snap.fiscal_year} revenue is negative: {snap.revenue_rmb}",
                    field="financials.revenue_rmb",
                )
            )
        if snap.gross_margin is not None and not (-1.0 <= snap.gross_margin <= 1.0):
            issues.append(
                ValidationIssue(
                    severity="high",
                    code="gross_margin_out_of_range",
                    message=(
                        f"FY{snap.fiscal_year} gross margin {snap.gross_margin} "
                        "outside [-1, 1] (Pydantic should have caught — defensive)"
                    ),
                    field="financials.gross_margin",
                )
            )
    return issues


def _validate_customer_concentration_bounds(
    extraction: ProspectusExtraction,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for conc in extraction.customer_concentration:
        if conc.top1_pct > conc.top5_pct + 0.001:  # allow tiny rounding
            issues.append(
                ValidationIssue(
                    severity="high",
                    code="top1_exceeds_top5",
                    message=(
                        f"FY{conc.fiscal_year} top1 ({conc.top1_pct}) > top5 ({conc.top5_pct})"
                    ),
                    field="customer_concentration",
                )
            )
    return issues


def _validate_risk_factor_present(
    extraction: ProspectusExtraction,
) -> list[ValidationIssue]:
    """A prospectus without risk factors is almost certainly an extraction error."""
    if not extraction.risk_factors:
        return [
            ValidationIssue(
                severity="medium",
                code="no_risk_factors",
                message="No risk factors extracted; prospectus always lists some",
                field="risk_factors",
            )
        ]
    return []


def _validate_shareholder_pct_sum(
    extraction: ProspectusExtraction,
) -> list[ValidationIssue]:
    """Shareholder percentages should sum to <= 1 (allow undisclosed retail tail)."""
    total = sum((s.pct_pre_ipo for s in extraction.shareholders), 0.0)
    if total > 1.05:  # tolerate small overlap noise
        return [
            ValidationIssue(
                severity="high",
                code="shareholders_pct_exceeds_one",
                message=f"Sum of shareholder pct_pre_ipo = {total:.3f} > 1.05",
                field="shareholders",
            )
        ]
    return []


def _validate_18c_self_consistent(
    extraction: ProspectusExtraction,
) -> list[ValidationIssue]:
    """If ch18c_qualification claims commercialized, financials should show revenue."""
    qual = extraction.ch18c_qualification
    if qual is None:
        return []
    issues: list[ValidationIssue] = []
    if qual.is_commercialized and qual.revenue_threshold_met:
        latest_revenue = _latest_revenue(extraction)
        if latest_revenue is not None and latest_revenue < Decimal("250000000"):
            issues.append(
                ValidationIssue(
                    severity="medium",
                    code="ch18c_revenue_inconsistent",
                    message=(
                        f"18C commercialized claim but latest revenue "
                        f"{latest_revenue} < 250M RMB threshold"
                    ),
                    field="ch18c_qualification.revenue_threshold_met",
                )
            )
    return issues


def _latest_revenue(extraction: ProspectusExtraction) -> Decimal | None:
    snaps_with_rev = [s for s in extraction.financials if s.revenue_rmb is not None]
    if not snaps_with_rev:
        return None
    latest = max(snaps_with_rev, key=lambda s: s.fiscal_year)
    return latest.revenue_rmb


__all__ = ("ValidationIssue", "validate")
