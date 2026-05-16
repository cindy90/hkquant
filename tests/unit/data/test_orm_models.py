"""Smoke tests for v1.0 SQLAlchemy ORM models per ADR 0006 §Progress (Phase 1).

These tests run against `Base.metadata` only — no DB connection required. The
live PG migration is verified separately via `make migrate` (Phase 1 DONE).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from hk_ipo_agent.data.models import (
    Base,
    Company,
    ComparableCompany,
    CornerstoneInvestment,
    CornerstoneInvestor,
    FinancialSnapshotRow,
    IPOAllocation,
    IPOEvent,
    IPOPostMarket,
    IPOPricing,
    ProspectusDoc,
    ProspectusExtractionRow,
    Sponsor,
    metadata,
)

EXPECTED_V10_TABLES: frozenset[str] = frozenset(
    {
        "ipo_events",
        "ipo_pricings",
        "ipo_allocations",
        "ipo_postmarket",
        "cornerstone_investors",
        "cornerstone_investments",
        "comparable_companies",
        "sponsors",
        "prospectus_docs",
        "prospectus_extractions",
        "companies",
        "financial_snapshots",
    }
)


def test_metadata_contains_all_v10_tables() -> None:
    """ADR 0006 lists exactly these 12 tables for Phase 1 base ORM."""
    actual = set(metadata.tables)
    missing = EXPECTED_V10_TABLES - actual
    assert not missing, f"Missing tables: {missing}"


def test_no_v11_tables_yet() -> None:
    """v1.1 / v1.2 / v1.2.1 ORM tables MUST NOT appear in Phase 1 (ADR 0006)."""
    actual = set(metadata.tables)
    forbidden = {
        "prediction_snapshots",
        "prediction_outcomes",
        "post_ipo_events",
        "prediction_reviews",
        "ipo_lifecycle_states",
        "ipo_state_transitions",
        "code_mappings",
        "scheduler_runs",
        "alerts",
        "earnings_comparisons",
        "user_accounts",
        "user_roles",
        "audit_logs",
        "chat_sessions",
        "chat_messages",
        "whatif_calculations",
        "realtime_events",
        "api_rate_limit_state",
        "config_versions",
    }
    leaked = forbidden & actual
    assert not leaked, f"Phase 1 must not yet declare these tables: {leaked}"


def test_naming_convention_applied() -> None:
    """Indices follow the `ix_*` naming convention from `base.NAMING_CONVENTION`."""
    # Find one indexed table and verify naming
    ix_names = {
        idx.name
        for table in metadata.tables.values()
        for idx in table.indexes
    }
    # We expect at least one of the explicit indexes we declared
    assert "ix_ipo_allocations_ipo_tranche" in ix_names
    assert "ix_cornerstone_investments_ipo_investor" in ix_names


def test_ipo_event_instantiation() -> None:
    """Instantiate an IPOEvent in-memory (no DB)."""
    e = IPOEvent(
        stock_code="2228.HK",
        company_name_zh="晶泰控股",
        listing_type="18C-COMM",
        listing_date=date(2024, 6, 13),
    )
    assert e.stock_code == "2228.HK"
    assert e.company_name_zh == "晶泰控股"


def test_cornerstone_investor_instantiation() -> None:
    c = CornerstoneInvestor(
        name_zh="中投公司",
        name_en="CIC",
        category="sovereign",
        home_country="CN",
        signal_strength_score=Decimal("90.0"),
    )
    assert c.category == "sovereign"


def test_relationship_backref_declared() -> None:
    """Verify the IPOEvent.pricing / postmarket relationships are defined."""
    mapper = IPOEvent.__mapper__
    rel_names = {rel.key for rel in mapper.relationships}
    assert {"pricing", "postmarket", "allocations", "cornerstone_investments", "prospectus_docs"} <= rel_names


def test_cornerstone_investment_relationships() -> None:
    mapper = CornerstoneInvestment.__mapper__
    rel_names = {rel.key for rel in mapper.relationships}
    assert "ipo" in rel_names
    assert "investor" in rel_names


def test_prospectus_extraction_row_links_to_doc() -> None:
    mapper = ProspectusExtractionRow.__mapper__
    rel_names = {rel.key for rel in mapper.relationships}
    assert "prospectus" in rel_names


def test_financial_snapshot_unique_per_company_period() -> None:
    """Unique composite index on (company_id, fiscal_year, fiscal_period)."""
    idx_names = {idx.name for idx in FinancialSnapshotRow.__table__.indexes}
    assert "ix_financial_snapshots_company_period" in idx_names
    target = next(
        idx for idx in FinancialSnapshotRow.__table__.indexes
        if idx.name == "ix_financial_snapshots_company_period"
    )
    assert target.unique is True


def test_all_v10_classes_share_base() -> None:
    """All ORM classes inherit from the project Base (single metadata)."""
    for cls in (
        IPOEvent,
        IPOPricing,
        IPOAllocation,
        IPOPostMarket,
        CornerstoneInvestor,
        CornerstoneInvestment,
        ComparableCompany,
        Sponsor,
        ProspectusDoc,
        ProspectusExtractionRow,
        Company,
        FinancialSnapshotRow,
    ):
        assert issubclass(cls, Base)
