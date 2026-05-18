"""Whitelisted web search wrapper.

R7-2: pre-fix this was a one-line "TODO" docstring. Now exposes a
Protocol + an explicitly unimplemented stub that raises NotImplementedError
on first use.

Full implementation tracked under PROJECT_SPEC.md §3.4.5 (whitelisted
search for prospectus citation cross-reference + theme research).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class WebSearch(Protocol):
    """Whitelisted-domain web search surface.

    The whitelist (financial press / regulatory bodies / official IR sites)
    is enforced at implementation time so agents can't leak prompts to
    arbitrary endpoints.
    """

    async def search(self, *, query: str, top_k: int = 5) -> list[dict[str, Any]]: ...


class WebSearchStub:
    """R7-2: explicit unimplemented stub. Every method raises ``NotImplementedError``."""

    async def search(self, *, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        raise NotImplementedError(
            "web_search.search: whitelisted web search is not implemented "
            "(see PROJECT_SPEC.md §3.4.5)."
        )


__all__ = ("WebSearch", "WebSearchStub")
