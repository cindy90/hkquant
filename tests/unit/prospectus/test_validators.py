"""Unit tests for prospectus.validators."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from hk_ipo_agent.common.enums import ListingType
from hk_ipo_agent.prospectus.schema import (
    Ch18CQualification,
    Citation,
    CustomerConcentration,
    FinancialSnapshot,
    ProspectusExtraction,
    RiskFactor,
    ShareholderEntry,
)
from hk_ipo_agent.prospectus.validators import validate


def _base_extraction() -> ProspectusExtraction:
    return ProspectusExtraction(
        prospectus_id="P-TEST",
        company_name_zh="测试公司",
        listing_type=ListingType.MAINBOARD_TECH,
        industry_code="TECH",
        industry_description="x",
        business_model="x",
        extraction_version="0.1.0",
        extracted_at=datetime.now(UTC),
    )


def test_clean_extraction_yields_only_no_risk_warning() -> None:
    e = _base_extraction()
    issues = validate(e)
    # Only the medium-severity 'no_risk_factors' warning expected on minimal data
    assert all(i.severity in {"low", "medium"} for i in issues)
    assert any(i.code == "no_risk_factors" for i in issues)
    assert e.needs_human_review is False  # no high-severity


def test_negative_revenue_flagged_high() -> None:
    e = _base_extraction()
    e.financials = [
        FinancialSnapshot(
            fiscal_year=2024,
            fiscal_period="FY",
            revenue_rmb=Decimal("-100"),
            citation=Citation(page=1),
        )
    ]
    e.risk_factors = [
        RiskFactor(
            category="business",
            description="x",
            severity="medium",
            citation=Citation(page=1),
        )
    ]
    issues = validate(e)
    assert any(i.code == "negative_revenue" for i in issues)
    assert e.needs_human_review is True


def test_top1_exceeds_top5_flagged_high() -> None:
    e = _base_extraction()
    e.customer_concentration = [
        CustomerConcentration(
            fiscal_year=2024,
            top1_pct=0.5,
            top5_pct=0.3,  # inconsistent with top1
            citation=Citation(page=1),
        )
    ]
    issues = validate(e)
    assert any(i.code == "top1_exceeds_top5" for i in issues)


def test_shareholder_pct_sum_above_one_flagged() -> None:
    e = _base_extraction()
    e.shareholders = [
        ShareholderEntry(
            name=f"S{i}",
            pct_pre_ipo=0.3,
            is_controlling=False,
            is_pre_ipo_investor=False,
            citation=Citation(page=1),
        )
        for i in range(5)  # sums to 1.5
    ]
    issues = validate(e)
    assert any(i.code == "shareholders_pct_exceeds_one" for i in issues)


def test_no_risk_factors_flagged_medium() -> None:
    e = _base_extraction()
    issues = validate(e)
    issue = next(i for i in issues if i.code == "no_risk_factors")
    assert issue.severity == "medium"


def test_ch18c_revenue_inconsistency_flagged_medium() -> None:
    e = _base_extraction()
    e.listing_type = ListingType.CH18C_COMMERCIALIZED
    e.ch18c_qualification = Ch18CQualification(
        is_commercialized=True,
        revenue_threshold_met=True,
        rd_intensity_met=True,
        market_cap_threshold_hkd=Decimal("4000000000"),
        citation=Citation(page=1),
    )
    e.financials = [
        FinancialSnapshot(
            fiscal_year=2024,
            fiscal_period="FY",
            revenue_rmb=Decimal("50000000"),  # 50M < 250M threshold
            citation=Citation(page=1),
        )
    ]
    e.risk_factors = [
        RiskFactor(
            category="business",
            description="x",
            severity="medium",
            citation=Citation(page=1),
        )
    ]
    issues = validate(e)
    assert any(i.code == "ch18c_revenue_inconsistent" for i in issues)
