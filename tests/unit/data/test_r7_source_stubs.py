"""R7-2 — 3 data-source modules expose Protocols that raise NotImplementedError.

Pre-R7-2 ``disclosure_scraper.py`` / ``news_client.py`` / ``web_search.py``
were each one-line docstrings ("TODO: implement per PROJECT_SPEC.md").
That meant:
  * No type to import → downstream code that wanted "a disclosure scraper"
    typed against ``Any``.
  * ``from hk_ipo_agent.data.sources import disclosure_scraper`` succeeded
    silently, masking that no implementation existed.
  * If a future caller did ``disclosure_scraper.fetch(...)`` they'd get
    AttributeError rather than a clear "not implemented" surface.

Post-R7-2 each module exposes a Protocol class + a concrete unimplemented
stub that raises NotImplementedError. The ``sources/__init__.py`` re-exports
them so the module surface is explicit.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------- Protocols exist


def test_disclosure_scraper_protocol_exists() -> None:
    """R7-2 — DisclosureScraper Protocol + stub are importable from the module."""
    from hk_ipo_agent.data.sources.disclosure_scraper import (
        DisclosureScraper,
        DisclosureScraperStub,
    )

    assert DisclosureScraper is not None
    assert DisclosureScraperStub is not None


def test_news_client_protocol_exists() -> None:
    """R7-2 — NewsClient Protocol + stub are importable."""
    from hk_ipo_agent.data.sources.news_client import NewsClient, NewsClientStub

    assert NewsClient is not None
    assert NewsClientStub is not None


def test_web_search_protocol_exists() -> None:
    """R7-2 — WebSearch Protocol + stub are importable."""
    from hk_ipo_agent.data.sources.web_search import WebSearch, WebSearchStub

    assert WebSearch is not None
    assert WebSearchStub is not None


# ---------------------------------------------------------------------- __init__ exports


def test_sources_package_reexports_stubs() -> None:
    """R7-2 — ``from hk_ipo_agent.data.sources import ...`` works for all 3 stubs."""
    from hk_ipo_agent.data import sources

    for name in (
        "DisclosureScraper",
        "DisclosureScraperStub",
        "NewsClient",
        "NewsClientStub",
        "WebSearch",
        "WebSearchStub",
    ):
        assert hasattr(sources, name), f"data.sources missing {name}"


# ---------------------------------------------------------------------- stubs raise loudly


@pytest.mark.asyncio
async def test_disclosure_scraper_stub_raises_not_implemented() -> None:
    """R7-2 — any method call on the stub raises ``NotImplementedError``.

    Important: NOT a silent ``return None``. Callers must see the failure
    so we don't ship a degraded service that pretends to work.
    """
    from hk_ipo_agent.data.sources.disclosure_scraper import DisclosureScraperStub

    stub = DisclosureScraperStub()
    with pytest.raises(NotImplementedError, match="disclosure_scraper"):
        await stub.fetch_filings(ipo_id="TEST")


@pytest.mark.asyncio
async def test_news_client_stub_raises_not_implemented() -> None:
    from hk_ipo_agent.data.sources.news_client import NewsClientStub

    stub = NewsClientStub()
    with pytest.raises(NotImplementedError, match="news_client"):
        await stub.search(query="test", since=None)


@pytest.mark.asyncio
async def test_web_search_stub_raises_not_implemented() -> None:
    from hk_ipo_agent.data.sources.web_search import WebSearchStub

    stub = WebSearchStub()
    with pytest.raises(NotImplementedError, match="web_search"):
        await stub.search(query="test")
