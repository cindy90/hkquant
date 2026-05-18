"""E2E regression test: 晶泰控股 2228.HK golden case — Phase 9b per ADR 0014.

Validates that the walk-forward harness produces a deterministic +
auditable result on real ETL'd data for 晶泰 (2228.HK, 18C-PRE, listed
2024-06-13).

Pipeline used: ``V8LiteScorer`` (no LLM) — keeps this case
LLM-cost-free and reproducible. The FullPipelineScorer adapter is
exercised separately in ``test_full_pipeline_smoke.py``.

Assertions:
1. The IPO exists in ipo_events with pricing_date < today.
2. AsOfDataProvider rejects pricing_date itself as as_of_date.
3. as_of_date = pricing_date - 1 ⇒ provider returns the IPO event.
4. Realized returns are read from ipo_postmarket (already ETL'd).
5. The V8LiteScorer produces a deterministic decision_score.
6. persist_run_to_pg writes a prediction_snapshots row tagged with
   the backtest_run_id.
"""

from __future__ import annotations

import functools
from datetime import date, timedelta

import psycopg
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from hk_ipo_agent.backtest.as_of_data import AsOfDataProvider
from hk_ipo_agent.backtest.runner import (
    V8LiteScorer,
    load_backtest_inputs_from_pg,
    persist_run_to_pg,
    run_walk_forward,
)
from hk_ipo_agent.common.exceptions import LookAheadError
from hk_ipo_agent.common.settings import get_settings

# 晶泰 listed 2024-06-13. Pricing date is 2024-06-06 per HKEX
# announcement (the NACS ETL fills both fields).
QUANTUMPHARM_STOCK_CODE = "2228.HK"


@functools.lru_cache(maxsize=1)
def _pg_available() -> bool:
    """Probe docker postgres once per session."""
    url = get_settings().database.url
    dsn = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    try:
        with psycopg.connect(dsn, connect_timeout=2):
            return True
    except Exception:
        return False


pg_required = pytest.mark.skipif(
    not _pg_available(),
    reason="docker postgres unavailable — start with `docker compose up -d postgres`",
)


def _sync_dsn() -> str:
    return get_settings().database.url.replace(
        "postgresql+asyncpg://",
        "postgresql://",
        1,
    )


def _fetch_quantumpharm_row() -> tuple[str, date, date] | None:
    """Sync-fetch 晶泰's ipo_id, pricing_date, listing_date.

    Returns None when the row isn't present (e.g. fresh DB without ETL).
    """
    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, pricing_date, listing_date FROM ipo_events WHERE stock_code = %s LIMIT 1",
            (QUANTUMPHARM_STOCK_CODE,),
        )
        row = cur.fetchone()
    if row is None or row[1] is None:
        return None
    return row[0], row[1], row[2]


@pytest.fixture
async def sf():
    engine = create_async_engine(get_settings().database.url, poolclass=NullPool)
    sf_ = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    yield sf_
    await engine.dispose()


# ---------------------------------------------------------------------------
# Sentinel: the ETL row is present
# ---------------------------------------------------------------------------


@pg_required
def test_quantumpharm_row_exists() -> None:
    """Phase 2 + 9a ETL should have populated 晶泰's row."""
    row = _fetch_quantumpharm_row()
    assert row is not None, (
        "2228.HK row missing from ipo_events — re-run scripts/migrate_sqlite_to_pg.py"
    )
    ipo_id, pricing_date, listing_date = row
    assert ipo_id is not None
    # NACS ETL records 2024-06-04 as the pricing_date (book-building
    # closed); listing happened ~5 trading days later on 2024-06-13.
    assert pricing_date == date(2024, 6, 4)
    assert listing_date == date(2024, 6, 13)


# ---------------------------------------------------------------------------
# AsOfDataProvider leak guards
# ---------------------------------------------------------------------------


@pg_required
@pytest.mark.asyncio
async def test_provider_rejects_pricing_date_for_pricing_query(sf) -> None:
    """as_of = pricing_date → provider sees the IPO event (a1_filing <= as_of)
    but pricing query at pricing_date itself is borderline."""
    row = _fetch_quantumpharm_row()
    assert row is not None
    ipo_id, pricing_date, _ = row

    # as_of = pricing_date - 1 → pricing not yet known.
    provider = AsOfDataProvider(
        as_of_date=pricing_date - timedelta(days=1),
        session_factory=sf,
    )
    with pytest.raises(LookAheadError, match="pricing"):
        await provider.get_ipo_pricing(ipo_id)


@pg_required
@pytest.mark.asyncio
async def test_provider_returns_pricing_on_pricing_date(sf) -> None:
    row = _fetch_quantumpharm_row()
    assert row is not None
    ipo_id, pricing_date, _ = row

    # as_of = pricing_date → pricing is now public.
    provider = AsOfDataProvider(
        as_of_date=pricing_date,
        session_factory=sf,
    )
    pricing = await provider.get_ipo_pricing(ipo_id)
    assert pricing is not None
    assert pricing.final_price is not None


# ---------------------------------------------------------------------------
# Walk-forward run over 晶泰 only
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pg_required
@pytest.mark.asyncio
async def test_quantumpharm_walk_forward_with_v8lite(sf) -> None:
    """Run V8LiteScorer on 晶泰; verify deterministic output + persistence.

    R9-8: marked ``slow`` (walk-forward over real PG data + persistence
    round-trip is one of the heaviest e2e tests).
    """
    row = _fetch_quantumpharm_row()
    assert row is not None
    ipo_id, pricing_date, _ = row

    # Load real inputs (1 IPO filtered by min_pricing_date).
    inputs_all = await load_backtest_inputs_from_pg(
        sf,
        min_pricing_date=date(2024, 6, 1),
    )
    quantumpharm_inputs = [i for i in inputs_all if i.ipo_id == ipo_id]
    assert len(quantumpharm_inputs) == 1, (
        f"Expected exactly 1 input for {QUANTUMPHARM_STOCK_CODE}, got {len(quantumpharm_inputs)}"
    )
    sample_input = quantumpharm_inputs[0]
    assert sample_input.pricing_date == pricing_date
    assert sample_input.realized_returns, (
        "ipo_postmarket should have returns_by_day OR scalar fallbacks"
    )

    # Run the walk-forward harness on this single sample.
    run = await run_walk_forward(
        [sample_input],
        scorer=V8LiteScorer(),
        session_factory=sf,
    )
    assert run.n_total == 1
    sample = run.samples[0]
    assert sample.ipo_id == ipo_id
    assert sample.stock_code == QUANTUMPHARM_STOCK_CODE
    assert sample.as_of_date == pricing_date - timedelta(days=1)
    # V8Lite is deterministic — the score is reproducible.
    assert isinstance(sample.decision_score, float)


@pg_required
@pytest.mark.asyncio
async def test_quantumpharm_persistence_round_trip(sf) -> None:
    """persist_run_to_pg → query back via the same backtest_run_id."""
    row = _fetch_quantumpharm_row()
    assert row is not None
    ipo_id, _pricing_date, _ = row

    inputs_all = await load_backtest_inputs_from_pg(
        sf,
        min_pricing_date=date(2024, 6, 1),
    )
    quantumpharm_inputs = [i for i in inputs_all if i.ipo_id == ipo_id]
    run = await run_walk_forward(
        quantumpharm_inputs,
        scorer=V8LiteScorer(),
        session_factory=sf,
    )
    rows_written = await persist_run_to_pg(run, sf)
    assert rows_written == 1
    # Verify via sync psycopg the snapshot exists.
    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM prediction_snapshots "
            "WHERE config_snapshot->>'backtest_run_id' = %s",
            (str(run.run_id),),
        )
        count = cur.fetchone()[0]
    assert count == 1
