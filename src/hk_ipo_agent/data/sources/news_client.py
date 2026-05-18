"""News aggregation client.

R7-2: pre-fix this was a one-line "TODO" docstring. Now exposes a
Protocol + an explicitly unimplemented stub that raises NotImplementedError
on first use.

Full implementation tracked under PROJECT_SPEC.md §3.4.4 (news ingestion
for sentiment_agent's pre-listing theme tracking).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class NewsClient(Protocol):
    """News aggregator surface — implementations populate Phase 9+.

    ``search(query, since)`` returns the most recent news articles matching
    the query, filtered to those published on or after ``since``. Used by
    sentiment_agent to score theme heat.
    """

    async def search(
        self, *, query: str, since: datetime | None = None
    ) -> list[dict[str, Any]]: ...


class NewsClientStub:
    """R7-2: explicit unimplemented stub. Every method raises ``NotImplementedError``."""

    async def search(self, *, query: str, since: datetime | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError(
            "news_client.search: news aggregation is not implemented (see PROJECT_SPEC.md §3.4.4)."
        )


__all__ = ("NewsClient", "NewsClientStub")
