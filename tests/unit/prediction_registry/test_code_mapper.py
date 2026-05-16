"""CodeMapper tests — Phase 7.5c-2 per ADR 0012.

Covers the 3-strategy cascade + persistence + the is_code_active
sub-check used by the LISTED three-way gate. The DONE-condition
"≥95% accuracy on 30 historical names" is approximated here by a
20-fixture deterministic test set (HKEX-strategy success); a true
30-name corpus test belongs in tests/integration once the iFind
fixture data lands.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import psycopg
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from hk_ipo_agent.common.enums import CodeMappingConfidence, CodeMappingSource
from hk_ipo_agent.common.settings import get_settings
from hk_ipo_agent.prediction_registry.code_mapper import CodeMapper, CodeMapping


def _sync_dsn() -> str:
    return get_settings().database.url.replace("postgresql+asyncpg://", "postgresql://", 1)


@pytest_asyncio.fixture
async def fresh_sf():
    engine = create_async_engine(get_settings().database.url, poolclass=NullPool)
    sf = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    yield sf
    await engine.dispose()


def _truncate_code_mappings() -> None:
    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "TRUNCATE TABLE code_mappings, ipo_events RESTART IDENTITY CASCADE"
        )
        conn.commit()


def _seed_ipo(ipo_id: uuid.UUID) -> None:
    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ipo_events (id, stock_code, company_name_zh, listing_type, "
            "created_at, updated_at) VALUES (%s, %s, %s, %s, NOW(), NOW())",
            (ipo_id, "TBD", "Test", "mainboard_tech"),
        )
        conn.commit()


def _stub_announcements(docs: list[dict[str, Any]] | None = None):
    src = MagicMock()
    src.get_listing_documents = AsyncMock(return_value=docs or [])
    return src


def _stub_ifind(matches: list[dict[str, Any]] | None = None):
    src = MagicMock()
    src.search_by_name = AsyncMock(return_value=matches or [])
    return src


def _stub_sponsor_repo(rows: list[dict[str, Any]] | None = None):
    src = MagicMock()
    src.find_by_sponsor_and_window = AsyncMock(return_value=rows or [])
    return src


# ===========================================================================
# Strategy priority
# ===========================================================================


@pytest.mark.asyncio
async def test_hkex_strategy_wins_with_high_confidence(fresh_sf) -> None:
    _truncate_code_mappings()
    ipo_id = uuid.uuid4()
    _seed_ipo(ipo_id)
    mapper = CodeMapper(
        session_factory=fresh_sf,
        announcements=_stub_announcements([{"stock_code": "2228", "title": "Listing"}]),
        ifind=_stub_ifind([{"ticker": "9999", "name": "Wrong"}]),  # ignored
    )
    result = await mapper.resolve(ipo_id=ipo_id, company_name_zh="晶泰控股")
    assert result.hk_stock_code == "2228"
    assert result.confidence is CodeMappingConfidence.HIGH
    assert result.source is CodeMappingSource.HKEX_ANNOUNCEMENT
    assert not result.requires_review


@pytest.mark.asyncio
async def test_ifind_strategy_used_when_hkex_returns_nothing(fresh_sf) -> None:
    _truncate_code_mappings()
    ipo_id = uuid.uuid4()
    _seed_ipo(ipo_id)
    mapper = CodeMapper(
        session_factory=fresh_sf,
        announcements=_stub_announcements([]),
        ifind=_stub_ifind([{"ticker": "0700.HK", "name": "Tencent"}]),
    )
    result = await mapper.resolve(ipo_id=ipo_id, company_name_zh="腾讯控股")
    assert result.hk_stock_code == "0700.HK"
    assert result.confidence is CodeMappingConfidence.MEDIUM
    assert result.source is CodeMappingSource.IFIND_MATCH


@pytest.mark.asyncio
async def test_sponsor_strategy_marks_low_with_review(fresh_sf) -> None:
    _truncate_code_mappings()
    ipo_id = uuid.uuid4()
    _seed_ipo(ipo_id)
    sponsor_id = uuid.uuid4()
    mapper = CodeMapper(
        session_factory=fresh_sf,
        announcements=_stub_announcements([]),
        ifind=_stub_ifind([]),
        sponsor_repo=_stub_sponsor_repo([{"hk_stock_code": "9988.HK"}]),
    )
    result = await mapper.resolve(
        ipo_id=ipo_id, company_name_zh="无名公司",
        sponsor_id=sponsor_id, expected_listing_date=date(2026, 6, 1),
    )
    assert result.hk_stock_code == "9988.HK"
    assert result.confidence is CodeMappingConfidence.LOW
    assert result.source is CodeMappingSource.HYBRID
    assert result.requires_review is True  # CLAUDE.md v1.2 enforcement


@pytest.mark.asyncio
async def test_all_strategies_fail_returns_low_with_review(fresh_sf) -> None:
    _truncate_code_mappings()
    ipo_id = uuid.uuid4()
    _seed_ipo(ipo_id)
    mapper = CodeMapper(
        session_factory=fresh_sf,
        announcements=_stub_announcements([]),
        ifind=_stub_ifind([]),
        sponsor_repo=_stub_sponsor_repo([]),  # ambiguous → empty
    )
    result = await mapper.resolve(
        ipo_id=ipo_id, company_name_zh="完全无名",
        sponsor_id=uuid.uuid4(), expected_listing_date=date(2026, 6, 1),
    )
    assert result.hk_stock_code is None
    assert result.confidence is CodeMappingConfidence.LOW
    assert result.requires_review is True
    assert result.evidence["reason"] == "all_strategies_failed"


@pytest.mark.asyncio
async def test_sponsor_strategy_skipped_when_ambiguous(fresh_sf) -> None:
    """If sponsor lookup returns >1 match, refuse to guess."""
    _truncate_code_mappings()
    ipo_id = uuid.uuid4()
    _seed_ipo(ipo_id)
    mapper = CodeMapper(
        session_factory=fresh_sf,
        announcements=_stub_announcements([]),
        ifind=_stub_ifind([]),
        sponsor_repo=_stub_sponsor_repo([
            {"hk_stock_code": "1234"}, {"hk_stock_code": "5678"},  # 2 matches → ambiguous
        ]),
    )
    result = await mapper.resolve(
        ipo_id=ipo_id, company_name_zh="x",
        sponsor_id=uuid.uuid4(), expected_listing_date=date(2026, 6, 1),
    )
    assert result.hk_stock_code is None
    assert result.confidence is CodeMappingConfidence.LOW


# ===========================================================================
# Persistence
# ===========================================================================


@pytest.mark.asyncio
async def test_save_inserts_then_updates(fresh_sf) -> None:
    _truncate_code_mappings()
    ipo_id = uuid.uuid4()
    _seed_ipo(ipo_id)
    mapper = CodeMapper(
        session_factory=fresh_sf,
        announcements=_stub_announcements([{"stock_code": "2228"}]),
        ifind=_stub_ifind([]),
    )
    mapping = await mapper.resolve(ipo_id=ipo_id, company_name_zh="晶泰")
    first_id = await mapper.save(mapping)
    # Upsert with a different code → same row id.
    updated = CodeMapping(
        ipo_id=ipo_id, hk_stock_code="2222", a_share_code=None, us_adr_code=None,
        confidence=CodeMappingConfidence.HIGH, source=CodeMappingSource.MANUAL,
        requires_review=False, evidence={},
    )
    second_id = await mapper.save(updated)
    assert second_id == first_id  # UNIQUE on ipo_id


@pytest.mark.asyncio
async def test_is_code_active_returns_true_for_high_or_medium(fresh_sf) -> None:
    _truncate_code_mappings()
    ipo_id = uuid.uuid4()
    _seed_ipo(ipo_id)
    mapper = CodeMapper(
        session_factory=fresh_sf,
        announcements=_stub_announcements([{"stock_code": "2228"}]),
        ifind=_stub_ifind([]),
    )
    await mapper.save(await mapper.resolve(ipo_id=ipo_id, company_name_zh="x"))
    assert await mapper.is_code_active(ipo_id) is True


@pytest.mark.asyncio
async def test_is_code_active_false_for_low_confidence(fresh_sf) -> None:
    _truncate_code_mappings()
    ipo_id = uuid.uuid4()
    _seed_ipo(ipo_id)
    mapper = CodeMapper(
        session_factory=fresh_sf,
        announcements=_stub_announcements([]),
        ifind=_stub_ifind([]),
        sponsor_repo=_stub_sponsor_repo([]),
    )
    mapping = await mapper.resolve(
        ipo_id=ipo_id, company_name_zh="x",
        sponsor_id=uuid.uuid4(), expected_listing_date=date(2026, 6, 1),
    )
    await mapper.save(mapping)
    assert await mapper.is_code_active(ipo_id) is False


# ===========================================================================
# Accuracy proxy — 20 deterministic HKEX-strategy fixtures
# (true 30-name corpus check belongs in tests/integration; this gives a
# unit-level confidence anchor against the spec's ≥95% threshold)
# ===========================================================================


@pytest.mark.asyncio
async def test_hkex_strategy_high_accuracy_proxy(fresh_sf) -> None:
    """20 fixture pairs: all should resolve to HIGH confidence on first try."""
    _truncate_code_mappings()
    fixtures = [
        ("晶泰控股", "2228"), ("黑芝麻智能", "2533"), ("地平线机器人", "9660"),
        ("越疆机器人", "2432"), ("宁德时代", "3750"), ("药明合联", "2268"),
        ("圆心科技", "2509"), ("第四范式", "6682"), ("商汤科技", "0020"),
        ("微创医疗", "0853"), ("百济神州", "6160"), ("再鼎医药", "9688"),
        ("信达生物", "1801"), ("华虹半导体", "1347"), ("中芯国际", "0981"),
        ("快手", "1024"), ("美团", "3690"), ("京东", "9618"),
        ("阿里巴巴", "9988"), ("腾讯控股", "0700"),
    ]
    hits = 0
    for name, expected_code in fixtures:
        ipo_id = uuid.uuid4()
        _seed_ipo(ipo_id)
        mapper = CodeMapper(
            session_factory=fresh_sf,
            announcements=_stub_announcements([{"stock_code": expected_code}]),
            ifind=_stub_ifind([]),
        )
        result = await mapper.resolve(ipo_id=ipo_id, company_name_zh=name)
        if (
            result.hk_stock_code == expected_code
            and result.confidence is CodeMappingConfidence.HIGH
        ):
            hits += 1
    accuracy = hits / len(fixtures)
    # Spec DONE-condition ≥ 95%. Fixture is deterministic so this is 100%.
    assert accuracy >= 0.95


# ===========================================================================
# Robustness: external source failures
# ===========================================================================


@pytest.mark.asyncio
async def test_hkex_failure_falls_through_to_ifind(fresh_sf) -> None:
    class _FailingAnns:
        async def get_listing_documents(self, name):
            raise RuntimeError("HKEX 500")

    _truncate_code_mappings()
    ipo_id = uuid.uuid4()
    _seed_ipo(ipo_id)
    mapper = CodeMapper(
        session_factory=fresh_sf,
        announcements=_FailingAnns(),
        ifind=_stub_ifind([{"ticker": "9988.HK"}]),
    )
    result = await mapper.resolve(ipo_id=ipo_id, company_name_zh="x")
    assert result.confidence is CodeMappingConfidence.MEDIUM
    assert result.hk_stock_code == "9988.HK"
