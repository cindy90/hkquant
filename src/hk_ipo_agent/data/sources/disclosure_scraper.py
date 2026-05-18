"""披露易 (HKEX DI) scraper for shareholder + cornerstone disclosure filings.

R7-2: pre-fix this module was a one-line "TODO" docstring. That left
downstream type annotations binding to ``Any`` and silently no-op'ed
when callers tried to fetch filings.

Post-fix:
  * ``DisclosureScraper`` — Protocol describing the expected surface for
    type annotations.
  * ``DisclosureScraperStub`` — concrete class whose every method raises
    ``NotImplementedError``. Imports succeed (so consumers can wire it
    up); first actual call fails loudly so we don't ship a "fetched 0
    filings, that's fine" silent failure.

Full implementation is tracked under PROJECT_SPEC.md §3.4.3 + ADR 0011
Phase 9 (signed URLs / scraper).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class DisclosureScraper(Protocol):
    """披露易 scraper surface — implementations populate Phase 9+.

    The two read paths cover the CLAUDE.md use-cases:
      * ``fetch_filings(ipo_id)`` — pre-listing cornerstone allocations
        for new IPOs (used by cornerstone_signal_agent).
      * ``fetch_substantial_shareholder_changes(stock_code, since)`` —
        post-listing 5%+ holder filings for outcome attribution.
    """

    async def fetch_filings(self, *, ipo_id: str) -> list[dict[str, Any]]: ...

    async def fetch_substantial_shareholder_changes(
        self, *, stock_code: str, since: Any = None
    ) -> list[dict[str, Any]]: ...


class DisclosureScraperStub:
    """R7-2: explicit unimplemented stub. Every method raises ``NotImplementedError``.

    Importable so callers can wire up the type / dependency graph; first
    call surfaces the missing implementation rather than silently returning
    empty results.
    """

    async def fetch_filings(self, *, ipo_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError(
            "disclosure_scraper.fetch_filings: HKEX DI scraping is not implemented "
            "(see PROJECT_SPEC.md §3.4.3 + ADR 0011 Phase 9)."
        )

    async def fetch_substantial_shareholder_changes(
        self, *, stock_code: str, since: Any = None
    ) -> list[dict[str, Any]]:
        raise NotImplementedError(
            "disclosure_scraper.fetch_substantial_shareholder_changes: "
            "HKEX DI scraping is not implemented (see ADR 0011 Phase 9)."
        )


__all__ = ("DisclosureScraper", "DisclosureScraperStub")
