"""as_of_data.py tests — Phase 8a per ADR 0013.

The DONE-condition for 8a: any caller-side attempt to read a field with
a date column post-dating ``as_of_date`` raises ``LookAheadError``.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import psycopg
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from hk_ipo_agent.backtest.as_of_data import (
    DEFAULT_DISCLOSURE_LAG_DAYS,
    AsOfDataProvider,
    AsOfPolicy,
)
from hk_ipo_agent.common.exceptions import LookAheadError
from hk_ipo_agent.common.settings import get_settings

from .conftest import pg_required


def _sync_dsn() -> str:
    return get_settings().database.url.replace("postgresql+asyncpg://", "postgresql://", 1)


@pytest_asyncio.fixture
async def fresh_sf():
    engine = create_async_engine(get_settings().database.url, poolclass=NullPool)
    sf = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    yield sf
    await engine.dispose()


def _truncate_data() -> None:
    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "TRUNCATE TABLE cornerstone_investments, prospectus_docs, "
            "ipo_pricings, ipo_events, financial_snapshots, companies "
            "RESTART IDENTITY CASCADE"
        )
        conn.commit()


def _seed_ipo(
    ipo_id: uuid.UUID,
    *,
    a1_date: date | None,
    pricing_date: date | None,
) -> None:
    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ipo_events (id, stock_code, company_name_zh, listing_type, "
            "a1_filing_date, pricing_date, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())",
            (ipo_id, "TEST.HK", "Test", "mainboard_tech", a1_date, pricing_date),
        )
        if pricing_date is not None:
            cur.execute(
                "INSERT INTO ipo_pricings (id, ipo_id, final_price, created_at, updated_at) "
                "VALUES (%s, %s, %s, NOW(), NOW())",
                (uuid.uuid4(), ipo_id, Decimal("5.28")),
            )
        conn.commit()


# ===========================================================================
# Constructor guards
# ===========================================================================


def test_constructor_rejects_future_as_of_date(fresh_sf) -> None:
    with pytest.raises(LookAheadError, match="future"):
        AsOfDataProvider(
            as_of_date=date.today() + timedelta(days=1),
            session_factory=fresh_sf,
        )


def test_default_disclosure_lag_is_30_days() -> None:
    """Spec: financials disclosed ~30d after period_end."""
    assert DEFAULT_DISCLOSURE_LAG_DAYS == 30


def test_policy_dataclass_is_customisable() -> None:
    policy = AsOfPolicy(disclosure_lag_days=60, strict_unknown_columns=False)
    assert policy.disclosure_lag_days == 60
    assert policy.strict_unknown_columns is False


# ===========================================================================
# IPO event visibility
# ===========================================================================


@pg_required
@pytest.mark.asyncio
async def test_get_ipo_event_hides_pre_a1_filing(fresh_sf) -> None:
    _truncate_data()
    ipo_id = uuid.uuid4()
    _seed_ipo(ipo_id, a1_date=date(2024, 6, 1), pricing_date=None)
    provider = AsOfDataProvider(
        as_of_date=date(2024, 1, 1),  # before a1_filing
        session_factory=fresh_sf,
    )
    assert await provider.get_ipo_event(ipo_id) is None


@pg_required
@pytest.mark.asyncio
async def test_get_ipo_event_returns_after_a1_filing(fresh_sf) -> None:
    _truncate_data()
    ipo_id = uuid.uuid4()
    _seed_ipo(ipo_id, a1_date=date(2024, 6, 1), pricing_date=None)
    provider = AsOfDataProvider(
        as_of_date=date(2024, 7, 1),  # after a1_filing
        session_factory=fresh_sf,
    )
    row = await provider.get_ipo_event(ipo_id)
    assert row is not None
    assert row.id == ipo_id


# ===========================================================================
# Pricing visibility — the most leak-prone case
# ===========================================================================


@pg_required
@pytest.mark.asyncio
async def test_get_ipo_pricing_raises_before_pricing_date(fresh_sf) -> None:
    """Adversarial: caller trying to fetch pricing pre-pricing_date should
    NOT silently get an empty result — that's a future leak."""
    _truncate_data()
    ipo_id = uuid.uuid4()
    _seed_ipo(ipo_id, a1_date=date(2024, 4, 1), pricing_date=date(2024, 6, 6))
    provider = AsOfDataProvider(
        as_of_date=date(2024, 5, 1),  # before pricing
        session_factory=fresh_sf,
    )
    with pytest.raises(LookAheadError, match="pricing"):
        await provider.get_ipo_pricing(ipo_id)


@pg_required
@pytest.mark.asyncio
async def test_get_ipo_pricing_returns_on_or_after_pricing_date(fresh_sf) -> None:
    _truncate_data()
    ipo_id = uuid.uuid4()
    _seed_ipo(ipo_id, a1_date=date(2024, 4, 1), pricing_date=date(2024, 6, 6))
    provider = AsOfDataProvider(
        as_of_date=date(2024, 6, 6),  # same day = visible (pricing now public)
        session_factory=fresh_sf,
    )
    pricing = await provider.get_ipo_pricing(ipo_id)
    assert pricing is not None
    assert pricing.final_price == Decimal("5.2800")


# ===========================================================================
# Price fetcher integration
# ===========================================================================


@pytest.mark.asyncio
async def test_get_hk_prices_passes_strict_inequality(fresh_sf) -> None:
    """The provider must query for ``as_of - 1`` to enforce strict-less-than."""
    captured: dict[str, date] = {}

    class _StubFetcher:
        async def get_hk_history_prices(self, tickers, as_of_date, *, start):
            captured["as_of"] = as_of_date
            captured["start"] = start
            return {"data": []}

    provider = AsOfDataProvider(
        as_of_date=date(2024, 6, 13),
        session_factory=fresh_sf,
        price_fetcher=_StubFetcher(),
    )
    await provider.get_hk_prices("TEST.HK", start=date(2024, 5, 13))
    # Strict-less-than: provider passed as_of - 1 = 2024-06-12.
    assert captured["as_of"] == date(2024, 6, 12)
    assert captured["start"] == date(2024, 5, 13)


@pytest.mark.asyncio
async def test_get_hk_prices_raises_when_no_price_fetcher(fresh_sf) -> None:
    """Caller forgot to inject a price fetcher → explicit error, no silent
    empty result."""
    provider = AsOfDataProvider(
        as_of_date=date(2024, 6, 13),
        session_factory=fresh_sf,
        price_fetcher=None,
    )
    with pytest.raises(LookAheadError, match="price_fetcher"):
        await provider.get_hk_prices("TEST.HK", start=date(2024, 5, 13))


@pytest.mark.asyncio
async def test_get_hk_prices_rejects_start_after_as_of_minus_1(fresh_sf) -> None:
    """Caller passed a start date that's only 1 day before as_of → window
    is empty after strict-less-than → fail fast."""

    class _StubFetcher:
        async def get_hk_history_prices(self, tickers, as_of_date, *, start):
            return {"data": []}

    provider = AsOfDataProvider(
        as_of_date=date(2024, 6, 13),
        session_factory=fresh_sf,
        price_fetcher=_StubFetcher(),
    )
    with pytest.raises(LookAheadError, match="empty window"):
        await provider.get_hk_prices("TEST.HK", start=date(2024, 6, 13))


# ===========================================================================
# Generic guard helpers
# ===========================================================================


def test_assert_within_window_passes_for_on_or_before(fresh_sf) -> None:
    provider = AsOfDataProvider(
        as_of_date=date(2024, 6, 13), session_factory=fresh_sf,
    )
    # No-op on past date.
    provider.assert_within_window(date(2024, 5, 1), field_name="filing_date")
    # Same date is OK.
    provider.assert_within_window(date(2024, 6, 13), field_name="filing_date")


def test_assert_within_window_raises_for_future_date(fresh_sf) -> None:
    provider = AsOfDataProvider(
        as_of_date=date(2024, 6, 13), session_factory=fresh_sf,
    )
    with pytest.raises(LookAheadError, match="filing_date"):
        provider.assert_within_window(date(2024, 6, 14), field_name="filing_date")


@pytest.mark.asyncio
async def test_post_ipo_events_always_empty_in_walk_forward(fresh_sf) -> None:
    """Walk-forward MUST NEVER see post-IPO events — they're post-listing
    by definition; the convention is documented + enforced."""
    provider = AsOfDataProvider(
        as_of_date=date(2024, 6, 13), session_factory=fresh_sf,
    )
    events = await provider.get_post_ipo_events(uuid.uuid4())
    assert events == []
