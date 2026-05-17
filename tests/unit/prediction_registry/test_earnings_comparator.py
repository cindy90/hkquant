"""EarningsComparator tests — Phase 7.5c-2 per ADR 0012."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from hk_ipo_agent.common.enums import (
    AgentRole,
    DecisionType,
    EarningsAssessment,
    ListingType,
)
from hk_ipo_agent.common.schemas import (
    AgentOutput,
    DebateOutput,
    FinalDecision,
    ProspectusExtraction,
    SingleModelValuation,
    ValuationDistribution,
    ValuationEnsembleOutput,
)
from hk_ipo_agent.common.settings import get_settings
from hk_ipo_agent.data.models import EarningsComparisonRow
from hk_ipo_agent.prediction_registry.earnings_comparator import (
    FIRST_RUN_REVIEW_THRESHOLD,
    EarningsComparator,
    FilingNumbers,
    load_mapping_rules,
)
from hk_ipo_agent.prediction_registry.snapshot import build_snapshot


def _sync_dsn() -> str:
    return get_settings().database.url.replace("postgresql+asyncpg://", "postgresql://", 1)


@pytest_asyncio.fixture
async def fresh_sf():
    engine = create_async_engine(get_settings().database.url, poolclass=NullPool)
    sf = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    yield sf
    await engine.dispose()


def _truncate_earnings() -> None:
    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "TRUNCATE TABLE earnings_comparisons, prediction_snapshots, "
            "ipo_events RESTART IDENTITY CASCADE"
        )
        conn.commit()


def _seed_snapshot(
    predicted_revenue: float = 100.0, predicted_profit: float = 20.0, predicted_gm: float = 0.40
):
    """Build a snapshot with one financial_snapshot row in the extraction."""
    d = ValuationDistribution(
        p10=Decimal("9"),
        p25=Decimal("9.5"),
        p50=Decimal("10"),
        p75=Decimal("10.5"),
        p90=Decimal("11"),
        mean=Decimal("10"),
        std=Decimal("0.5"),
    )
    ext = ProspectusExtraction(
        prospectus_id=f"P-EC-{uuid.uuid4().hex[:6]}",
        company_name_zh="测试",
        listing_type=ListingType.MAINBOARD_TECH,
        industry_code="TECH",
        industry_description="AI",
        business_model="B2B",
        extraction_version="0.0.1",
        extracted_at=datetime.now(UTC),
    )
    snap = build_snapshot(
        ipo_id=uuid.uuid4(),
        extraction=ext,
        agent_outputs={
            "fundamental": AgentOutput(
                agent_role=AgentRole.FUNDAMENTAL,
                scores={"x": 70.0},
                overall_score=70.0,
                runtime_seconds=0.1,
            ),
        },
        valuation=ValuationEnsembleOutput(
            company_id="P-EC-1",
            single_models=[
                SingleModelValuation(model_name="x", applicable=True, valuation_distribution=d)
            ],
            weights_used={"x": 1.0},
            ensemble_distribution=d,
            implied_price_range={"low": Decimal("9"), "fair": Decimal("10"), "high": Decimal("11")},
        ),
        debate=DebateOutput(final_consensus="balanced"),
        decision=FinalDecision(
            decision=DecisionType.PARTICIPATE,
            confidence=0.7,
            suggested_allocation_pct=0.02,
            price_range_low=Decimal("9"),
            price_range_fair=Decimal("10"),
            price_range_high=Decimal("11"),
            expected_return_6m=d,
            expected_return_12m=d,
        ),
        total_cost_usd=Decimal("0.1"),
        runtime_seconds=5.0,
    )
    # Inject a financial snapshot blob into the input_data_snapshot dict.
    return snap.model_copy(
        update={
            "input_data_snapshot": {
                **snap.input_data_snapshot,
                "extraction": {
                    **snap.input_data_snapshot["extraction"],
                    "financial_snapshots": [
                        {
                            "fiscal_year": 2024,
                            "fiscal_period": "FY",
                            "revenue_rmb": str(predicted_revenue),
                            "adjusted_net_profit_rmb": str(predicted_profit),
                            "gross_margin": str(predicted_gm),
                        }
                    ],
                },
            },
        }
    )


def _seed_ipo_and_snapshot_in_db(snap) -> None:
    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ipo_events (id, stock_code, company_name_zh, listing_type, "
            "created_at, updated_at) VALUES (%s, %s, %s, %s, NOW(), NOW())",
            (snap.ipo_id, "TEST.HK", "Test", "mainboard_tech"),
        )
        cur.execute(
            "INSERT INTO prediction_snapshots "
            "(id, ipo_id, as_of_date, prospectus_version, input_data_hash, "
            " input_data_snapshot, agent_outputs, valuation_output, debate_output, "
            " decision, system_version, model_versions, config_snapshot, "
            " total_cost_usd, runtime_seconds, created_at) "
            "VALUES (%s, %s, %s, 'PHIP', %s, '{}'::jsonb, '{}'::jsonb, "
            " '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, '0.0.1', '{}'::jsonb, '{}'::jsonb, "
            " 0.0, 0.0, NOW())",
            (snap.id, snap.ipo_id, snap.as_of_date, "0" * 64),
        )
        conn.commit()


# ===========================================================================
# mapping_rules.yaml loading
# ===========================================================================


def test_load_mapping_rules_finds_default_industries() -> None:
    rules = load_mapping_rules()
    assert "default" in rules
    assert "ai_software" in rules
    assert "biotech_18a" in rules


def test_first_run_review_threshold_is_three() -> None:
    """CLAUDE.md v1.2 enforcement."""
    assert FIRST_RUN_REVIEW_THRESHOLD == 3


# ===========================================================================
# Comparison logic
# ===========================================================================


@pytest.mark.asyncio
async def test_compare_writes_row_with_deviations(fresh_sf) -> None:
    _truncate_earnings()
    snap = _seed_snapshot(predicted_revenue=100.0, predicted_profit=20.0, predicted_gm=0.40)
    _seed_ipo_and_snapshot_in_db(snap)
    comparator = EarningsComparator(session_factory=fresh_sf)
    filing = FilingNumbers(
        report_period="FY2025",
        filing_date=date(2026, 3, 31),
        actual_revenue=Decimal("110"),  # +10%
        actual_net_profit=Decimal("18"),  # -10%
        actual_gross_margin=Decimal("0.38"),  # -2pp
        extra_kpis={"arr_rmb": 50},
    )
    result = await comparator.compare(snapshot=snap, filing=filing)
    assert result.snapshot_id == snap.id
    assert result.report_period == "FY2025"
    assert abs(result.revenue_deviation_pct - 0.10) < 0.0001
    assert abs(result.profit_deviation_pct - (-0.10)) < 0.0001


@pytest.mark.asyncio
async def test_first_three_runs_require_human_review(fresh_sf) -> None:
    """CLAUDE.md v1.2: first 3 comparisons MUST flag requires_human_review."""
    _truncate_earnings()
    comparator = EarningsComparator(session_factory=fresh_sf)
    for i in range(FIRST_RUN_REVIEW_THRESHOLD):
        snap = _seed_snapshot()
        _seed_ipo_and_snapshot_in_db(snap)
        result = await comparator.compare(
            snapshot=snap,
            filing=FilingNumbers(
                report_period=f"FY202{i}",
                filing_date=date(2026, 3, 31),
                actual_revenue=Decimal("100"),
                actual_net_profit=Decimal("20"),
                actual_gross_margin=Decimal("0.40"),
                extra_kpis={},
            ),
        )
        assert result.requires_human_review is True, f"run {i} should require review"

    # 4th run: should NOT force review.
    snap = _seed_snapshot()
    _seed_ipo_and_snapshot_in_db(snap)
    result = await comparator.compare(
        snapshot=snap,
        filing=FilingNumbers(
            report_period="FY2099",
            filing_date=date(2026, 3, 31),
            actual_revenue=Decimal("100"),
            actual_net_profit=Decimal("20"),
            actual_gross_margin=Decimal("0.40"),
            extra_kpis={},
        ),
    )
    assert result.requires_human_review is False


@pytest.mark.asyncio
async def test_assess_significant_miss_when_revenue_down_25pct(fresh_sf) -> None:
    _truncate_earnings()
    snap = _seed_snapshot(predicted_revenue=100.0, predicted_profit=20.0)
    _seed_ipo_and_snapshot_in_db(snap)
    comparator = EarningsComparator(session_factory=fresh_sf)
    result = await comparator.compare(
        snapshot=snap,
        filing=FilingNumbers(
            report_period="FY2025",
            filing_date=date(2026, 3, 31),
            actual_revenue=Decimal("75"),  # -25%
            actual_net_profit=Decimal("12"),  # -40%
            actual_gross_margin=Decimal("0.30"),
            extra_kpis={},
        ),
    )
    assert result.overall_assessment is EarningsAssessment.SIGNIFICANT_MISS


@pytest.mark.asyncio
async def test_assess_beat_when_revenue_up_10pct(fresh_sf) -> None:
    _truncate_earnings()
    snap = _seed_snapshot(predicted_revenue=100.0, predicted_profit=20.0)
    _seed_ipo_and_snapshot_in_db(snap)
    comparator = EarningsComparator(session_factory=fresh_sf)
    result = await comparator.compare(
        snapshot=snap,
        filing=FilingNumbers(
            report_period="FY2025",
            filing_date=date(2026, 3, 31),
            actual_revenue=Decimal("110"),
            actual_net_profit=Decimal("24"),  # +20%
            actual_gross_margin=Decimal("0.42"),
            extra_kpis={},
        ),
    )
    assert result.overall_assessment is EarningsAssessment.BEAT


@pytest.mark.asyncio
async def test_compare_idempotent_on_snapshot_period(fresh_sf) -> None:
    """Re-running with same (snapshot_id, report_period) → no duplicate row."""
    _truncate_earnings()
    snap = _seed_snapshot()
    _seed_ipo_and_snapshot_in_db(snap)
    comparator = EarningsComparator(session_factory=fresh_sf)
    filing = FilingNumbers(
        report_period="FY2025",
        filing_date=date(2026, 3, 31),
        actual_revenue=Decimal("100"),
        actual_net_profit=Decimal("20"),
        actual_gross_margin=Decimal("0.40"),
        extra_kpis={},
    )
    await comparator.compare(snapshot=snap, filing=filing)
    await comparator.compare(snapshot=snap, filing=filing)
    async with fresh_sf() as s:
        rows = (
            (
                await s.execute(
                    select(EarningsComparisonRow).where(
                        EarningsComparisonRow.snapshot_id == snap.id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_compare_handles_missing_predictions(fresh_sf) -> None:
    """If the prospectus had no financials, deviation fields are None."""
    _truncate_earnings()
    # Build a snapshot WITHOUT a financial_snapshots entry.
    snap = _seed_snapshot()
    snap = snap.model_copy(
        update={
            "input_data_snapshot": {
                **snap.input_data_snapshot,
                "extraction": {
                    **snap.input_data_snapshot["extraction"],
                    "financial_snapshots": [],
                },
            },
        }
    )
    _seed_ipo_and_snapshot_in_db(snap)
    comparator = EarningsComparator(session_factory=fresh_sf)
    result = await comparator.compare(
        snapshot=snap,
        filing=FilingNumbers(
            report_period="FY2025",
            filing_date=date(2026, 3, 31),
            actual_revenue=Decimal("100"),
            actual_net_profit=Decimal("20"),
            actual_gross_margin=Decimal("0.40"),
            extra_kpis={},
        ),
    )
    assert result.revenue_deviation_pct is None
    assert result.profit_deviation_pct is None
