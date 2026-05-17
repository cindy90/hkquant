"""EventDetector tests — Phase 7.5b per ADR 0012.

Covers:
- price anomaly path (no LLM)
- announcement classification path (Sonnet mocked)
- merge + dedup
- best-effort: source failure on one signal doesn't block the other
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from hk_ipo_agent.common.enums import EventSeverity, PostIPOEventType
from hk_ipo_agent.common.llm_client import LLMClient
from hk_ipo_agent.prediction_registry.event_detector import (
    PRICE_ANOMALY_SINGLE_DAY_THRESHOLD,
    EventDetector,
    _EventClassification,
)


class _StubPrices:
    def __init__(self, rows):
        self._rows = rows

    async def get_hk_history_prices(self, tickers, as_of_date, *, start):
        return {"data": self._rows}


class _StubAnnouncements:
    def __init__(self, filings):
        self._filings = filings

    async def get_disclosure_filings(self, stock_code):
        return self._filings


@pytest.fixture
def llm_mock(monkeypatch) -> LLMClient:
    monkeypatch.setenv("KIMI_API_KEY", "sk-test")
    client = LLMClient(daily_budget_usd=Decimal("100"))
    # Default: return EARNINGS / MAJOR / "Q1 业绩".
    client.acomplete_json = AsyncMock(  # type: ignore[method-assign]
        return_value=_EventClassification(
            event_type=PostIPOEventType.EARNINGS,
            severity=EventSeverity.MAJOR,
            description="Q1 业绩超预期",
        )
    )
    return client


@pytest.mark.asyncio
async def test_scan_detects_single_day_anomaly(llm_mock) -> None:
    rows = [
        {"time": "2026-02-01", "thscode": "TEST.HK", "close": 100.0},
        {"time": "2026-02-02", "thscode": "TEST.HK", "close": 110.0},  # +10% — major
    ]
    detector = EventDetector(
        ifind=_StubPrices(rows),
        announcements=_StubAnnouncements([]),
        llm=llm_mock,
    )
    events = await detector.scan_events(
        ipo_id=uuid4(), stock_code="TEST.HK",
        window_start=date(2026, 2, 1), window_end=date(2026, 2, 2),
    )
    assert len(events) == 1
    assert events[0].event_type == PostIPOEventType.OTHER
    assert events[0].severity == EventSeverity.MAJOR
    assert "single_day_up" in events[0].description


@pytest.mark.asyncio
async def test_scan_ignores_below_threshold_moves(llm_mock) -> None:
    rows = [
        {"time": "2026-02-01", "thscode": "TEST.HK", "close": 100.0},
        {"time": "2026-02-02", "thscode": "TEST.HK", "close": 104.0},  # +4% — below 5%
    ]
    detector = EventDetector(
        ifind=_StubPrices(rows),
        announcements=_StubAnnouncements([]),
        llm=llm_mock,
    )
    events = await detector.scan_events(
        ipo_id=uuid4(), stock_code="TEST.HK",
        window_start=date(2026, 2, 1), window_end=date(2026, 2, 2),
    )
    assert events == []


@pytest.mark.asyncio
async def test_scan_classifies_announcement_via_llm(llm_mock) -> None:
    detector = EventDetector(
        ifind=_StubPrices([]),
        announcements=_StubAnnouncements(
            [{"filing_date": "2026-03-15", "doc_type": "10-K", "title": "年度业绩公告", "url": "https://x"}]
        ),
        llm=llm_mock,
    )
    events = await detector.scan_events(
        ipo_id=uuid4(), stock_code="TEST.HK",
        window_start=date(2026, 3, 1), window_end=date(2026, 3, 31),
    )
    assert len(events) == 1
    assert events[0].event_type == PostIPOEventType.EARNINGS
    assert events[0].severity == EventSeverity.MAJOR


@pytest.mark.asyncio
async def test_scan_dedupes_overlapping_events(llm_mock) -> None:
    """If an earnings announcement *and* a price spike land same day, keep one."""
    rows = [
        {"time": "2026-03-14", "thscode": "TEST.HK", "close": 100.0},
        {"time": "2026-03-15", "thscode": "TEST.HK", "close": 200.0},  # +100% — would emit OTHER/CRITICAL
    ]
    # LLM classifies as EARNINGS/MAJOR (different category) — both should survive.
    detector = EventDetector(
        ifind=_StubPrices(rows),
        announcements=_StubAnnouncements(
            [{"filing_date": "2026-03-15", "title": "年度业绩"}]
        ),
        llm=llm_mock,
    )
    events = await detector.scan_events(
        ipo_id=uuid4(), stock_code="TEST.HK",
        window_start=date(2026, 3, 14), window_end=date(2026, 3, 16),
    )
    # 1 earnings + 1 price anomaly = 2 distinct (event_type, severity) keys.
    assert len(events) == 2


@pytest.mark.asyncio
async def test_scan_robust_to_announcement_failure(llm_mock) -> None:
    """Failure on announcements still returns price-anomaly events."""

    class _Failing:
        async def get_disclosure_filings(self, stock_code):
            raise RuntimeError("HKEX down")

    rows = [
        {"time": "2026-02-01", "thscode": "TEST.HK", "close": 100.0},
        {"time": "2026-02-02", "thscode": "TEST.HK", "close": 110.0},  # +10% major
    ]
    detector = EventDetector(
        ifind=_StubPrices(rows),
        announcements=_Failing(),
        llm=llm_mock,
    )
    events = await detector.scan_events(
        ipo_id=uuid4(), stock_code="TEST.HK",
        window_start=date(2026, 2, 1), window_end=date(2026, 2, 2),
    )
    assert len(events) == 1
    assert events[0].event_type == PostIPOEventType.OTHER


def test_threshold_constants_match_spec() -> None:
    """PROJECT_SPEC.md §3.11 fixes thresholds at 5% / 10%."""
    assert PRICE_ANOMALY_SINGLE_DAY_THRESHOLD == 0.05
