"""Web search tool — stub for Phase 5; production wiring deferred to Phase 9.

Per PROJECT_SPEC.md §3.6. Phase 5 agents that want web search (e.g.
``sentiment_agent`` for media coverage) should call ``WebTool.search``
which returns a list of (title, url, snippet, retrieved_at) dicts.

For now this is a deterministic stub returning empty results — when the
real web_search MCP / Serper API is wired, only this file needs changes
and all agents pick it up.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


class WebTool:
    """Inject this into ``AgentContext.misc['web_tool']`` (optional)."""

    async def search(self, query: str, *, top_k: int = 5) -> list[dict[str, Any]]:
        """Return up to ``top_k`` web result dicts.

        Phase 5 stub: returns empty list. Callers must check ``is_stub``
        and degrade gracefully if no web results are available.
        """
        _ = (query, top_k)
        return []

    @property
    def is_stub(self) -> bool:
        """Callers can branch on this until real web search is wired."""
        return True

    @staticmethod
    def now() -> datetime:
        """UTC retrieval timestamp helper for callers stamping records."""
        return datetime.now(UTC)


__all__ = ("WebTool",)
