"""HKEXnews + 披露易 scraper per PROJECT_SPEC.md §3.4.

Skeleton implementation: real HKEX scraping requires careful robots.txt
compliance + structured parsing of announcement PDFs. Phase 2 lands the
interface + a rate-limited HTTPx client; full parsing logic and
DOM-extraction lives in Phase 7.5 (event_detector dependency).

This module MUST NOT be used to bypass robots.txt — see PROJECT_SPEC.md §15.2
data source authorization rules.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ...common.exceptions import DataSourceError, DataSourceUnavailableError
from ...common.logging import get_logger
from ...common.settings import load_data_sources_config

log = get_logger(__name__)

# HKEX endpoints (per PROJECT_SPEC.md §15.2 — public + rate-limited)
HKEXNEWS_BASE = "https://www1.hkexnews.hk"
HKEX_RSS_FEED = "https://www.hkexnews.hk/listedco/listconews/sehk/index.htm"


class HKEXScraper:
    """Async HTTPx-based scraper for HKEXnews announcements + 披露易.

    Polite by construction: respects ``HKEX_RATE_LIMIT_PER_SEC`` from config
    and emits a project-identifying User-Agent.
    """

    def __init__(
        self,
        *,
        user_agent: str | None = None,
        rate_limit_per_sec: float | None = None,
        timeout_seconds: int = 30,
    ) -> None:
        cfg = load_data_sources_config().get("hkex", {})
        self.user_agent = user_agent or cfg.get("user_agent", "hk-ipo-agent/0.0 (research)")
        self.rate_limit_per_sec = float(
            rate_limit_per_sec or cfg.get("rate_limit_per_sec", 2)
        )
        self._interval = 1.0 / self.rate_limit_per_sec
        self._timeout_seconds = timeout_seconds
        self._client: httpx.AsyncClient | None = None
        self._last_request_at = 0.0
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> HKEXScraper:
        self._client = httpx.AsyncClient(
            headers={"User-Agent": self.user_agent},
            timeout=self._timeout_seconds,
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ public

    async def download_prospectus(
        self, stock_code: str, *, dest_dir: Path
    ) -> Path:
        """Download a prospectus PDF for ``stock_code`` to ``dest_dir``.

        Phase 2 stub: discovers the listing announcement page, then downloads
        the most recent prospectus PDF linked there. Full DOM parsing is
        Phase 3's job (see prospectus.parser).
        """
        listing_docs = await self.get_listing_documents(stock_code)
        if not listing_docs:
            raise DataSourceError(
                f"No listing documents found for {stock_code} on HKEXnews",
                stock_code=stock_code,
            )
        # Use the first PDF link as a placeholder; Phase 3 will pick by
        # version (PHIP / AP1 / etc.)
        pdf_url = listing_docs[0]["url"]
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / f"{stock_code}_{listing_docs[0]['filename']}"
        await self._stream_to_file(pdf_url, target)
        log.info("downloaded_prospectus", stock_code=stock_code, path=str(target))
        return target

    async def get_listing_documents(self, stock_code: str) -> list[dict[str, Any]]:
        """Return list of {title, url, filename, published_at} dicts.

        TODO Phase 3: implement DOM parsing of the HKEXnews search results page.
        """
        # Phase 2 stub — return empty list until Phase 3 wires up the parser.
        log.warning(
            "hkex_scraper_stub",
            stock_code=stock_code,
            note="Phase 2 placeholder; real parsing in Phase 3",
        )
        return []

    async def get_disclosure_filings(self, stock_code: str) -> list[dict[str, Any]]:
        """Return shareholder disclosure filings from 披露易.

        TODO Phase 7.5: implement when cornerstone_tracker needs lockup data.
        """
        log.warning(
            "hkex_disclosure_stub",
            stock_code=stock_code,
            note="Phase 2 placeholder; needed in Phase 7.5",
        )
        return []

    # ------------------------------------------------------------------ internals

    async def _throttle(self) -> None:
        async with self._lock:
            now = asyncio.get_running_loop().time()
            wait = max(0.0, self._interval - (now - self._last_request_at))
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_at = asyncio.get_running_loop().time()

    async def _get(self, url: str) -> httpx.Response:
        if self._client is None:
            raise DataSourceUnavailableError(
                "HKEXScraper must be used as an async context manager."
            )
        await self._throttle()
        retry = AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            retry=retry_if_exception_type(httpx.TransportError),
            reraise=True,
        )
        async for attempt in retry:
            with attempt:
                response = await self._client.get(url)
                response.raise_for_status()
                return response
        raise DataSourceError("HKEX GET retry loop exited without result")  # pragma: no cover

    async def _stream_to_file(self, url: str, target: Path) -> None:
        if self._client is None:
            raise DataSourceUnavailableError(
                "HKEXScraper must be used as an async context manager."
            )
        await self._throttle()
        async with self._client.stream("GET", url) as response:
            response.raise_for_status()
            with target.open("wb") as fh:
                async for chunk in response.aiter_bytes():
                    fh.write(chunk)


__all__ = ("HKEXScraper",)
