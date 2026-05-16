"""Per-state auto-detection — PROJECT_SPEC.md §3.11.1.

Each detector inspects external data sources and returns either a
``TransitionSignal`` (suggesting a transition with evidence) or None.
The state machine is the only writer; detectors are read-only.

LISTED is the high-stakes transition — CLAUDE.md "LISTED 状态必须经过
三重验证（HKEX 公告 + iFind 行情 + 股票代码激活）" — so it's split out
as ``ThreeWayValidation`` with all 3 sub-checks visible to operators.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Protocol
from uuid import UUID

from ...common.enums import IPOLifecycleStateType, TransitionTrigger
from ...common.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class TransitionSignal:
    """A detector's recommendation. Caller decides whether to apply it."""

    target_state: IPOLifecycleStateType
    triggered_by: TransitionTrigger
    evidence: dict[str, Any]


@dataclass(frozen=True)
class ThreeWayValidation:
    """Result of the LISTED gate. ``passed`` iff all 3 sub-checks succeed."""

    hkex_listing_announcement: bool
    ifind_first_day_quote: bool
    stock_code_active: bool
    evidence: dict[str, Any]

    @property
    def passed(self) -> bool:
        return (
            self.hkex_listing_announcement
            and self.ifind_first_day_quote
            and self.stock_code_active
        )


class _AnnouncementSource(Protocol):
    async def get_listing_documents(self, stock_code: str) -> list[dict[str, Any]]: ...

    async def get_disclosure_filings(self, stock_code: str) -> list[dict[str, Any]]: ...


class _PriceFetcher(Protocol):
    async def get_hk_history_prices(
        self,
        tickers: str | list[str],
        as_of_date: date,
        *,
        start: date,
    ) -> Any: ...


class _CodeResolver(Protocol):
    async def is_code_active(self, ipo_id: UUID) -> bool: ...


class StateDetectors:
    """All 4 transition detectors bundled. Each is independently testable."""

    def __init__(
        self,
        *,
        announcements: _AnnouncementSource,
        ifind: _PriceFetcher,
        code_resolver: _CodeResolver,
    ) -> None:
        self._anns = announcements
        self._ifind = ifind
        self._codes = code_resolver

    async def detect_pricing(
        self, ipo_id: UUID, *, stock_code: str
    ) -> TransitionSignal | None:
        """PRICING: HKEX publishes a price-range filing (PHIP / AP)."""
        try:
            filings = await self._anns.get_disclosure_filings(stock_code)
        except Exception as exc:
            logger.warning("detect_pricing_failed", ipo_id=str(ipo_id), error=str(exc))
            return None
        for f in filings:
            title = (f.get("title") or "").lower()
            if any(k in title for k in ("招股价", "price range", "phip", "ap")):
                return TransitionSignal(
                    target_state=IPOLifecycleStateType.PRICING,
                    triggered_by=TransitionTrigger.AUTO_DETECTOR,
                    evidence={
                        "source": "hkex_filing",
                        "title": f.get("title"),
                        "filing_date": f.get("filing_date"),
                    },
                )
        return None

    async def detect_listed_three_way(
        self,
        ipo_id: UUID,
        *,
        stock_code: str,
        expected_listing_date: date | None = None,
    ) -> ThreeWayValidation:
        """The 3-way gate for LISTED transition.

        Returns a ``ThreeWayValidation`` regardless — caller checks
        ``.passed`` to decide whether to call ``transition_to(LISTED)``.
        """
        # Sub-check 1: HKEX listing announcement
        try:
            listings = await self._anns.get_listing_documents(stock_code)
        except Exception as exc:
            logger.warning("listed_announcement_check_failed", ipo_id=str(ipo_id), error=str(exc))
            listings = []
        hkex_ok = len(listings) > 0

        # Sub-check 2: iFind first-day quote available
        target_date = expected_listing_date or datetime.now(UTC).date()
        try:
            payload = await self._ifind.get_hk_history_prices(
                stock_code, target_date, start=target_date,
            )
            from ..benchmarks import _close_series  # noqa: PLC0415

            series = _close_series(payload)
            ifind_ok = any(stock_code in vals for vals in series.values())
        except Exception as exc:
            logger.warning("listed_ifind_check_failed", ipo_id=str(ipo_id), error=str(exc))
            ifind_ok = False

        # Sub-check 3: stock code resolver confirms code is live
        try:
            code_ok = await self._codes.is_code_active(ipo_id)
        except Exception as exc:
            logger.warning("listed_code_check_failed", ipo_id=str(ipo_id), error=str(exc))
            code_ok = False

        return ThreeWayValidation(
            hkex_listing_announcement=hkex_ok,
            ifind_first_day_quote=ifind_ok,
            stock_code_active=code_ok,
            evidence={
                "hkex_listings_count": len(listings),
                "ifind_quote_date": target_date.isoformat(),
                "stock_code": stock_code,
            },
        )

    async def detect_withdrawn(
        self, ipo_id: UUID, *, stock_code: str
    ) -> TransitionSignal | None:
        """WITHDRAWN: HKEX publishes a withdrawal-of-application notice."""
        try:
            filings = await self._anns.get_disclosure_filings(stock_code)
        except Exception as exc:
            logger.warning("detect_withdrawn_failed", ipo_id=str(ipo_id), error=str(exc))
            return None
        for f in filings:
            title = (f.get("title") or "").lower()
            if any(k in title for k in ("撤回", "withdrawn", "withdraw application")):
                return TransitionSignal(
                    target_state=IPOLifecycleStateType.WITHDRAWN,
                    triggered_by=TransitionTrigger.AUTO_DETECTOR,
                    evidence={
                        "source": "hkex_filing",
                        "title": f.get("title"),
                        "filing_date": f.get("filing_date"),
                    },
                )
        return None

    async def detect_hearing_failed(
        self, ipo_id: UUID, *, stock_code: str
    ) -> TransitionSignal | None:
        """HEARING_FAILED: HKEX hearing committee rejects the application."""
        try:
            filings = await self._anns.get_disclosure_filings(stock_code)
        except Exception as exc:
            logger.warning("detect_hearing_failed_failed", ipo_id=str(ipo_id), error=str(exc))
            return None
        for f in filings:
            title = (f.get("title") or "").lower()
            if any(k in title for k in ("聆讯失败", "hearing failed", "未获通过", "rejected")):
                return TransitionSignal(
                    target_state=IPOLifecycleStateType.HEARING_FAILED,
                    triggered_by=TransitionTrigger.AUTO_DETECTOR,
                    evidence={
                        "source": "hkex_filing",
                        "title": f.get("title"),
                        "filing_date": f.get("filing_date"),
                    },
                )
        return None


__all__ = (
    "StateDetectors",
    "ThreeWayValidation",
    "TransitionSignal",
)
