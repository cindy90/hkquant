"""Prospectus QA tool — thin wrapper over ``prospectus.qa.ProspectusQA``.

Per PROJECT_SPEC.md §3.6 (tools/) + §3.5. Agents call ``ask()`` to query
the prospectus RAG and receive citations back. Mandatory enforcement
of citations is delegated to ``ProspectusQA`` itself (raises
``CitationRequiredError``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...prospectus.qa import Answer, ProspectusQA


class ProspectusTool:
    """Inject this into ``AgentContext.prospectus_tool``."""

    def __init__(self, qa: ProspectusQA) -> None:
        self._qa = qa

    async def ask(
        self,
        question: str,
        *,
        top_k: int = 5,
        section_filter: str | None = None,
    ) -> Answer:
        """Forward to ``ProspectusQA.ask``. Raises on missing citations."""
        return await self._qa.ask(question, top_k=top_k, section_filter=section_filter)


__all__ = ("ProspectusTool",)
