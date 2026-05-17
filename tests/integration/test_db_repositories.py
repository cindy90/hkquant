"""Integration tests for the data repositories against live PostgreSQL.

These tests assume the NACS SQLite migration has already been applied
(see ``scripts/migrate_sqlite_to_pg.py``). The current NACS SQLite
yields **384 IPO events / 1,311 cornerstone investors (post-alias-merge)
/ 1,609 cornerstone investments** — counts checked against this snapshot
of the data. Phase 9a archived the SQLite to ``legacy/data/``; the
migrate script falls back transparently.

Run with:
    docker compose up -d postgres
    uv run python scripts/migrate_sqlite_to_pg.py --no-backup
    uv run pytest tests/integration -v -m integration

Skip if PG is unavailable so CI without the docker compose stack still passes.
"""

from __future__ import annotations

from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from hk_ipo_agent.data.builders import CornerstoneProfileBuilder, SponsorTrackBuilder
from hk_ipo_agent.data.database import async_session_factory, dispose_engine
from hk_ipo_agent.data.repositories import (
    CornerstoneInvestmentRepository,
    CornerstoneInvestorRepository,
    IPOEventRepository,
    IPOPostMarketRepository,
    IPOPricingRepository,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    """Yield a fresh AsyncSession per test, skipping if PG is unreachable.

    Disposes the cached engine before + after each test so pytest-asyncio's
    per-function event loop doesn't collide with a long-lived AsyncEngine.
    """
    await dispose_engine()
    factory = async_session_factory()
    try:
        async with factory() as s:
            try:
                await s.execute(text("SELECT 1"))
            except Exception as exc:
                pytest.skip(f"PostgreSQL not reachable: {exc}")
            yield s
    finally:
        await dispose_engine()


# ---------------------------------------------------------------------------
# Sanity: NACS migration counts are present in PG
# ---------------------------------------------------------------------------


async def test_ipo_events_count_matches_nacs_corpus(session: AsyncSession) -> None:
    """Current NACS ETL loads 384 IPO events (was 399 in Phase 2 spec;
    actual SQLite contents are 384 after Phase 9 ETL bug fix).
    Uses ``>=`` lower bound to be robust against later spot-inserts
    from other tests in the same suite."""
    repo = IPOEventRepository(session)
    count = await repo.count()
    assert count >= 384, f"expected >= 384 IPOs from NACS migration, got {count}"


async def test_cornerstone_corpus_intact(session: AsyncSession) -> None:
    """Post-alias-merge: ~1,311 cornerstone_investors + ~1,609
    cornerstone_investments. Uses ``>=`` to be robust against
    cross-test inserts; the lower bound proves the ETL ran."""
    inv_repo = CornerstoneInvestorRepository(session)
    link_repo = CornerstoneInvestmentRepository(session)
    assert await inv_repo.count() >= 1300
    assert await link_repo.count() >= 1600


async def test_ipo_pricing_and_postmarket_aligned(session: AsyncSession) -> None:
    """Every IPO has a pricing row; nearly every IPO has a postmarket row."""
    ipo_repo = IPOEventRepository(session)
    pricing_repo = IPOPricingRepository(session)
    postmarket_repo = IPOPostMarketRepository(session)
    n_ipos = await ipo_repo.count()
    n_pricing = await pricing_repo.count()
    n_postmarket = await postmarket_repo.count()
    # Pricing is 1-1 with IPO event; postmarket is 1-1 for listed IPOs
    # (398 vs 399 because one row was unmigratable from NACS source).
    assert n_pricing == n_ipos
    assert n_postmarket in {n_ipos - 1, n_ipos}


# ---------------------------------------------------------------------------
# Repository contract — CRUD primitives
# ---------------------------------------------------------------------------


async def test_repository_list_with_filter_and_order(session: AsyncSession) -> None:
    repo = IPOEventRepository(session)
    page = await repo.list(limit=5, order_by="-listing_date")
    assert len(page) == 5
    listing_dates = [ipo.listing_date for ipo in page if ipo.listing_date]
    assert listing_dates == sorted(listing_dates, reverse=True)


async def test_repository_listed_between_range(session: AsyncSession) -> None:
    repo = IPOEventRepository(session)
    listed_2024 = await repo.list_listed_between(date(2024, 1, 1), date(2024, 12, 31))
    for ipo in listed_2024:
        assert ipo.listing_date is not None
        assert date(2024, 1, 1) <= ipo.listing_date <= date(2024, 12, 31)


async def test_repository_find_by_stock_code_roundtrip(session: AsyncSession) -> None:
    repo = IPOEventRepository(session)
    # Pick any existing stock_code
    sample = (await repo.list(limit=1))[0]
    assert sample.stock_code is not None
    found = await repo.find_by_stock_code(sample.stock_code)
    assert found is not None
    assert found.id == sample.id


# ---------------------------------------------------------------------------
# Cornerstone cluster bonus (ADR 0005 §2)
# ---------------------------------------------------------------------------


async def test_cluster_bonus_detection_finds_industry_capital_syndicates(
    session: AsyncSession,
) -> None:
    """At least one IPO in the corpus should have a 2+ ultimate_holder cluster.

    This is the data-side proof that NACS Cluster Bonus signal survives
    migration. If this assertion ever fires zero, the ultimate_holder field
    mapping in the ETL is broken.
    """
    builder = CornerstoneProfileBuilder()
    # Spot-check a handful of IPOs; finding even one cluster is enough.
    repo = IPOEventRepository(session)
    sample = await repo.list(limit=50, order_by="-listing_date")
    saw_cluster = False
    for ipo in sample:
        report = await builder.cluster_report_for_ipo(ipo.id)
        if report.has_cluster:
            saw_cluster = True
            break
    # NACS data has clusters, so we EXPECT at least one in any 50-IPO window
    assert saw_cluster, (
        "No ultimate_holder cluster detected in 50 sampled IPOs — ETL field "
        "mapping for cornerstone_investors.ultimate_holder may be broken"
    )


async def test_cornerstone_coverage_stats(session: AsyncSession) -> None:
    builder = CornerstoneProfileBuilder()
    stats = await builder.coverage_stats()
    assert stats["investor_count"] >= 1300
    assert stats["investment_count"] >= 1600
    # ~80% of investors had aliases per NACS migration log
    assert stats["with_aliases"] > 0


# ---------------------------------------------------------------------------
# Sponsor track record — exercises join across ipo_events + ipo_postmarket
# ---------------------------------------------------------------------------


async def test_sponsor_track_record_smoke(session: AsyncSession) -> None:
    """Quick smoke test on the sponsor builder's compute method.

    Doesn't validate specific numbers (the sponsor_primary string match is
    approximate); just verifies the query runs and returns shape.
    """
    builder = SponsorTrackBuilder()
    record = await builder.compute(
        sponsor_name="dummy",
        as_of_date=date(2025, 1, 1),
        lookback_days=730,
    )
    assert record.sponsor_name == "dummy"
    assert record.lookback_days == 730
    assert record.as_of_date == date(2025, 1, 1)
    # cases_count is the count of all IPOs in window (approximate match)
    assert record.cases_count >= 0
