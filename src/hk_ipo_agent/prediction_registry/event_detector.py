"""Post-listing event detector per PROJECT_SPEC.md §3.11.

Two complementary signals over a [window_start, window_end] window:

1. **Price anomalies** — single-day return > ±5% or rolling 5-day
   return > ±10%; severity tier by magnitude. Source: iFind history.
2. **HKEX announcements** — daily disclosure filings. Sonnet classifies
   each filing title into ``PostIPOEventType`` + ``EventSeverity`` +
   short rationale via ``acomplete_json`` (Pydantic-validated).

Persistence is the caller's responsibility (``outcome_tracker.py``
upserts into ``post_ipo_events`` with idempotency on
(ipo_id, event_date, event_type)).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, Field

from ..common.enums import EventSeverity, PostIPOEventType
from ..common.llm_client import LLMClient
from ..common.logging import get_logger
from ..common.schemas import PostIPOEvent
from .benchmarks import _close_series

logger = get_logger(__name__)

PRICE_ANOMALY_SINGLE_DAY_THRESHOLD = 0.05  # |daily| > 5%
PRICE_ANOMALY_5D_THRESHOLD = 0.10  # |5-day rolling| > 10%
EVENT_CLASSIFIER_MODEL = "claude-sonnet-4-6"


class _AnnouncementSource(Protocol):
    """Subset of ``HKEXScraper`` we use (test seam)."""

    async def get_disclosure_filings(self, stock_code: str) -> list[dict[str, Any]]: ...


class _PriceFetcher(Protocol):
    async def get_hk_history_prices(
        self,
        tickers: str | list[str],
        as_of_date: date,
        *,
        start: date,
    ) -> Any: ...


class _EventClassification(BaseModel):
    """LLM-structured classification of a single filing."""

    event_type: PostIPOEventType
    severity: EventSeverity
    description: str = Field(..., max_length=500)


class EventDetector:
    """Detects post-listing events over a price + announcement window."""

    def __init__(
        self,
        *,
        ifind: _PriceFetcher,
        announcements: _AnnouncementSource,
        llm: LLMClient,
        classifier_model: str = EVENT_CLASSIFIER_MODEL,
    ) -> None:
        self._ifind = ifind
        self._anns = announcements
        self._llm = llm
        self._model = classifier_model

    async def scan_events(
        self,
        *,
        ipo_id: UUID,
        stock_code: str,
        window_start: date,
        window_end: date,
    ) -> list[PostIPOEvent]:
        """Returns the union of price-anomaly + classified-announcement events.

        Best-effort: a failure on one signal source does NOT block the
        other; the missing signal is logged at WARNING.
        """
        price_events = await self._scan_price_anomalies(stock_code, window_start, window_end)
        ann_events = await self._scan_announcements(stock_code, window_start, window_end, ipo_id=ipo_id)
        # De-dupe on (event_date, event_type, severity).
        seen: set[tuple[date, str, str]] = set()
        merged: list[PostIPOEvent] = []
        for ev in [*ann_events, *price_events]:
            key = (ev.event_date, ev.event_type.value, ev.severity.value)
            if key in seen:
                continue
            seen.add(key)
            merged.append(ev)
        merged.sort(key=lambda e: e.event_date)
        return merged

    # ------------------------------------------------------------------
    # Price anomaly path
    # ------------------------------------------------------------------

    async def _scan_price_anomalies(
        self,
        stock_code: str,
        window_start: date,
        window_end: date,
    ) -> list[PostIPOEvent]:
        try:
            payload = await self._ifind.get_hk_history_prices(
                stock_code, window_end, start=window_start
            )
        except Exception as exc:
            logger.warning(
                "event_price_fetch_failed",
                stock_code=stock_code, error=str(exc),
            )
            return []
        series = _close_series(payload)
        if not series:
            return []

        # Sort by date; pull this stock's closes only.
        timeline: list[tuple[date, float]] = sorted(
            (d, vals[stock_code])
            for d, vals in series.items()
            if stock_code in vals
        )
        if len(timeline) < 2:
            return []

        events: list[PostIPOEvent] = []
        prev_close = timeline[0][1]
        rolling: list[float] = [timeline[0][1]]
        for d, close in timeline[1:]:
            day_ret = (close - prev_close) / prev_close if prev_close else 0.0
            rolling.append(close)
            if len(rolling) > 5:
                rolling.pop(0)
            five_day_ret = (
                (rolling[-1] - rolling[0]) / rolling[0]
                if len(rolling) >= 5 and rolling[0] else None
            )

            if abs(day_ret) > PRICE_ANOMALY_SINGLE_DAY_THRESHOLD:
                events.append(
                    self._build_price_event(
                        event_date=d, day_ret=day_ret,
                        five_day_ret=five_day_ret, kind="single_day",
                    )
                )
            elif five_day_ret is not None and abs(five_day_ret) > PRICE_ANOMALY_5D_THRESHOLD:
                events.append(
                    self._build_price_event(
                        event_date=d, day_ret=day_ret,
                        five_day_ret=five_day_ret, kind="five_day",
                    )
                )
            prev_close = close
        return events

    @staticmethod
    def _build_price_event(
        *,
        event_date: date,
        day_ret: float,
        five_day_ret: float | None,
        kind: str,
    ) -> PostIPOEvent:
        magnitude = abs(day_ret if kind == "single_day" else (five_day_ret or 0.0))
        if magnitude > 0.15:
            severity = EventSeverity.CRITICAL
        elif magnitude > 0.08:
            severity = EventSeverity.MAJOR
        else:
            severity = EventSeverity.MINOR
        direction = "up" if (day_ret if kind == "single_day" else (five_day_ret or 0.0)) > 0 else "down"
        return PostIPOEvent(
            event_date=event_date,
            event_type=PostIPOEventType.OTHER,
            severity=severity,
            description=f"price_anomaly_{kind}_{direction}: 1d={day_ret:.2%}, 5d={(five_day_ret or 0.0):.2%}",
            price_impact_1d=day_ret,
            price_impact_5d=five_day_ret,
        )

    # ------------------------------------------------------------------
    # Announcement classification path
    # ------------------------------------------------------------------

    async def _scan_announcements(
        self,
        stock_code: str,
        window_start: date,
        window_end: date,
        *,
        ipo_id: UUID,
    ) -> list[PostIPOEvent]:
        try:
            filings = await self._anns.get_disclosure_filings(stock_code)
        except Exception as exc:
            logger.warning(
                "event_announcements_fetch_failed",
                stock_code=stock_code, ipo_id=str(ipo_id), error=str(exc),
            )
            return []
        # Window filter
        in_window = [f for f in filings if _in_window(f.get("filing_date"), window_start, window_end)]
        if not in_window:
            return []

        events: list[PostIPOEvent] = []
        for filing in in_window:
            classified = await self._classify_filing(filing, ipo_id=ipo_id)
            if classified is None:
                continue
            filing_d = _parse_date(filing.get("filing_date"))
            if filing_d is None:
                continue
            events.append(
                PostIPOEvent(
                    event_date=filing_d,
                    event_type=classified.event_type,
                    severity=classified.severity,
                    description=classified.description,
                    source_url=filing.get("url"),
                )
            )
        return events

    async def _classify_filing(
        self,
        filing: dict[str, Any],
        *,
        ipo_id: UUID,
    ) -> _EventClassification | None:
        title = filing.get("title") or filing.get("doc_title") or ""
        if not title:
            return None
        try:
            return await self._llm.acomplete_json(
                model=self._model,
                system=_CLASSIFIER_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": _format_classification_prompt(filing)}],
                response_model=_EventClassification,
                agent_role="event_detector",
                ipo_id=str(ipo_id),
                temperature=0.0,
                max_tokens=512,
            )
        except Exception as exc:
            logger.warning(
                "event_classification_failed",
                ipo_id=str(ipo_id), title=title[:80], error=str(exc),
            )
            return None


def _in_window(d: Any, start: date, end: date) -> bool:
    parsed = _parse_date(d)
    return parsed is not None and start <= parsed <= end


def _parse_date(d: Any) -> date | None:
    if isinstance(d, date):
        return d
    if isinstance(d, str):
        try:
            return date.fromisoformat(d[:10])
        except ValueError:
            return None
    return None


_CLASSIFIER_SYSTEM_PROMPT = (
    "You classify HKEX disclosure filings into post-IPO event types for an "
    "investment archive. Return a JSON object matching the schema with one of: "
    "earnings, profit_warning, major_contract, regulatory, management_change, "
    "cornerstone_disclosure, placement, share_buyback, other. Severity tiers: "
    "critical (top management exit, regulator intervention, profit warning), "
    "major (earnings, major contract win/loss, cornerstone reduction), "
    "minor (routine governance, small placements). Description must be in "
    "Chinese, ≤80 字 with the most important fact only."
)


def _format_classification_prompt(filing: dict[str, Any]) -> str:
    lines = ["请分类以下港交所披露文件："]
    for k in ("filing_date", "doc_type", "title", "summary"):
        v = filing.get(k)
        if v:
            lines.append(f"- {k}: {v}")
    lines.append("输出 JSON：{event_type, severity, description}")
    return "\n".join(lines)


def iter_window_dates(start: date, end: date) -> Iterable[date]:
    """Yield calendar dates over [start, end]. Public for tests."""
    from datetime import timedelta as _td  # noqa: PLC0415

    d = start
    while d <= end:
        yield d
        d += _td(days=1)


__all__ = (
    "EVENT_CLASSIFIER_MODEL",
    "PRICE_ANOMALY_5D_THRESHOLD",
    "PRICE_ANOMALY_SINGLE_DAY_THRESHOLD",
    "EventDetector",
)
