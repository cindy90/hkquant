"""External data source clients.

R7-2: explicit re-exports of the Protocol + Stub pairs for the three
unimplemented sources, so downstream type annotations resolve cleanly
and callers see a clear NotImplementedError on first use rather than
``AttributeError`` from poking at empty modules.
"""

from __future__ import annotations

from .disclosure_scraper import DisclosureScraper, DisclosureScraperStub
from .news_client import NewsClient, NewsClientStub
from .web_search import WebSearch, WebSearchStub

__all__ = (
    "DisclosureScraper",
    "DisclosureScraperStub",
    "NewsClient",
    "NewsClientStub",
    "WebSearch",
    "WebSearchStub",
)
