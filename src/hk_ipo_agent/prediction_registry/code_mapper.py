"""Company name → stock code auto-mapping per PROJECT_SPEC.md §3.11.

At prospectus filing time an IPO doesn't yet have a stock code — only a
company name. After listing HKEX assigns the code; this mapper resolves
``ipo_id → CodeMapping`` via a three-strategy cascade, the first
successful one wins:

1. **HKEX announcement** — listing announcement explicitly carries the
   code. ``confidence=HIGH``.
2. **iFind search_by_name** — fuzzy match returns the resolved code.
   ``confidence=MEDIUM`` (occasional false positives on similar names).
3. **Sponsor + listing-date window** — search by sponsor name + date
   range as a last resort. ``confidence=LOW`` and **mandatory
   requires_review=True** (CLAUDE.md v1.2 constraint).

A+H pairs resolve both H code (HK exchange) and A code (mainland) per
spec §3.11.
"""

from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..common.enums import CodeMappingConfidence, CodeMappingSource
from ..common.logging import get_logger
from ..data.models import CodeMappingRow

logger = get_logger(__name__)


@dataclass(frozen=True)
class CodeMapping:
    """Resolved mapping. Persisted in ``code_mappings`` table via ``save``."""

    ipo_id: UUID
    hk_stock_code: str | None
    a_share_code: str | None
    us_adr_code: str | None
    confidence: CodeMappingConfidence
    source: CodeMappingSource
    requires_review: bool
    evidence: dict[str, Any]


class _AnnouncementSource(Protocol):
    async def get_listing_documents(self, stock_code_or_name: str) -> list[dict[str, Any]]: ...


class _IFindNameSearch(Protocol):
    async def search_by_name(self, name: str, *, market: str = "HK") -> list[dict[str, Any]]: ...


class _SponsorRepo(Protocol):
    async def find_by_sponsor_and_window(
        self,
        sponsor_id: UUID | None,
        *,
        listing_date_range: tuple[date, date],
    ) -> list[dict[str, Any]]: ...


class CodeMapper:
    """Three-strategy code resolver. Strategies are tried in priority order."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        announcements: _AnnouncementSource,
        ifind: _IFindNameSearch,
        sponsor_repo: _SponsorRepo | None = None,
    ) -> None:
        self._sf = session_factory
        self._anns = announcements
        self._ifind = ifind
        self._sponsors = sponsor_repo

    async def resolve(
        self,
        *,
        ipo_id: UUID,
        company_name_zh: str,
        company_name_en: str | None = None,
        sponsor_id: UUID | None = None,
        expected_listing_date: date | None = None,
    ) -> CodeMapping:
        """Resolve a mapping; returns even on failure (with confidence=LOW)."""
        # Strategy 1 — HKEX announcement (HIGH)
        mapping = await self._try_hkex_announcement(ipo_id, company_name_zh, company_name_en)
        if mapping is not None:
            return mapping
        # Strategy 2 — iFind search (MEDIUM)
        mapping = await self._try_ifind_search(ipo_id, company_name_zh, company_name_en)
        if mapping is not None:
            return mapping
        # Strategy 3 — sponsor + date window (LOW)
        if sponsor_id is not None and expected_listing_date is not None:
            mapping = await self._try_sponsor_window(ipo_id, sponsor_id, expected_listing_date)
            if mapping is not None:
                return mapping
        # All strategies failed → empty mapping with LOW + requires_review.
        return CodeMapping(
            ipo_id=ipo_id, hk_stock_code=None, a_share_code=None, us_adr_code=None,
            confidence=CodeMappingConfidence.LOW,
            source=CodeMappingSource.MANUAL,
            requires_review=True,
            evidence={"reason": "all_strategies_failed",
                      "company_name_zh": company_name_zh,
                      "company_name_en": company_name_en},
        )

    async def save(self, mapping: CodeMapping) -> UUID:
        """Persist (or update) ``code_mappings`` row. Idempotent on ipo_id (UNIQUE)."""
        async with self._sf() as s:
            stmt = select(CodeMappingRow).where(CodeMappingRow.ipo_id == mapping.ipo_id)
            existing = (await s.execute(stmt)).scalar_one_or_none()
            now = datetime.now(UTC)
            if existing is not None:
                existing.hk_stock_code = mapping.hk_stock_code
                existing.a_share_code = mapping.a_share_code
                existing.us_adr_code = mapping.us_adr_code
                existing.confidence = mapping.confidence.value
                existing.confirmation_source = mapping.source.value
                existing.requires_review = mapping.requires_review
                existing.confirmed_at = now if mapping.confidence != CodeMappingConfidence.LOW else None
                await s.commit()
                return UUID(str(existing.id))
            row = CodeMappingRow(
                id=_uuid.uuid4(),
                ipo_id=mapping.ipo_id,
                hk_stock_code=mapping.hk_stock_code,
                a_share_code=mapping.a_share_code,
                us_adr_code=mapping.us_adr_code,
                confidence=mapping.confidence.value,
                confirmation_source=mapping.source.value,
                requires_review=mapping.requires_review,
                confirmed_at=now if mapping.confidence != CodeMappingConfidence.LOW else None,
            )
            s.add(row)
            await s.commit()
        return row.id

    async def is_code_active(self, ipo_id: UUID) -> bool:
        """Sub-check for ``state_detectors.detect_listed_three_way``.

        A code is "active" when we have a HIGH-confidence mapping with a
        non-null hk_stock_code. MEDIUM mappings count too because the
        three-way gate has 2 other independent checks.
        """
        async with self._sf() as s:
            stmt = select(CodeMappingRow).where(CodeMappingRow.ipo_id == ipo_id)
            row = (await s.execute(stmt)).scalar_one_or_none()
        if row is None or row.hk_stock_code is None:
            return False
        return row.confidence in {
            CodeMappingConfidence.HIGH.value,
            CodeMappingConfidence.MEDIUM.value,
        }

    # ------------------------------------------------------------------
    # Strategies
    # ------------------------------------------------------------------

    async def _try_hkex_announcement(
        self, ipo_id: UUID, company_name_zh: str, company_name_en: str | None,
    ) -> CodeMapping | None:
        try:
            docs = await self._anns.get_listing_documents(company_name_zh)
        except Exception as exc:
            logger.warning("hkex_lookup_failed", ipo_id=str(ipo_id), error=str(exc))
            return None
        for doc in docs:
            code = doc.get("stock_code") or doc.get("hk_code")
            if code:
                return CodeMapping(
                    ipo_id=ipo_id, hk_stock_code=str(code),
                    a_share_code=doc.get("a_share_code"),
                    us_adr_code=doc.get("us_adr_code"),
                    confidence=CodeMappingConfidence.HIGH,
                    source=CodeMappingSource.HKEX_ANNOUNCEMENT,
                    requires_review=False,
                    evidence={"hkex_doc_id": doc.get("id"), "title": doc.get("title")},
                )
        return None

    async def _try_ifind_search(
        self, ipo_id: UUID, company_name_zh: str, company_name_en: str | None,
    ) -> CodeMapping | None:
        query = company_name_en or company_name_zh
        try:
            matches = await self._ifind.search_by_name(query)
        except Exception as exc:
            logger.warning("ifind_search_failed", ipo_id=str(ipo_id), error=str(exc))
            return None
        if not matches:
            return None
        # Best match = first result if its name closely matches input.
        best = matches[0]
        code = best.get("ticker") or best.get("thscode")
        if not code:
            return None
        return CodeMapping(
            ipo_id=ipo_id, hk_stock_code=str(code),
            a_share_code=best.get("a_share_code"),
            us_adr_code=best.get("us_adr_code"),
            confidence=CodeMappingConfidence.MEDIUM,
            source=CodeMappingSource.IFIND_MATCH,
            requires_review=False,
            evidence={"ifind_match_name": best.get("name"), "query": query},
        )

    async def _try_sponsor_window(
        self, ipo_id: UUID, sponsor_id: UUID, expected_listing_date: date,
    ) -> CodeMapping | None:
        if self._sponsors is None:
            return None
        window_start = expected_listing_date - timedelta(days=7)
        window_end = expected_listing_date + timedelta(days=7)
        try:
            matches = await self._sponsors.find_by_sponsor_and_window(
                sponsor_id, listing_date_range=(window_start, window_end),
            )
        except Exception as exc:
            logger.warning("sponsor_lookup_failed", ipo_id=str(ipo_id), error=str(exc))
            return None
        if len(matches) != 1:  # Need exactly one match to claim LOW confidence.
            return None
        match = matches[0]
        code = match.get("hk_stock_code")
        if not code:
            return None
        return CodeMapping(
            ipo_id=ipo_id, hk_stock_code=str(code),
            a_share_code=match.get("a_share_code"),
            us_adr_code=None,
            confidence=CodeMappingConfidence.LOW,
            source=CodeMappingSource.HYBRID,
            requires_review=True,  # LOW confidence MUST trigger review
            evidence={
                "sponsor_id": str(sponsor_id),
                "window": [window_start.isoformat(), window_end.isoformat()],
                "match_count": 1,
            },
        )


__all__ = (
    "CodeMapper",
    "CodeMapping",
)
