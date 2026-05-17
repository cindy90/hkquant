"""Prospectus Q&A with mandatory citations per PROJECT_SPEC.md §3.5.

The QA layer is what the 7 expert agents (Phase 5) consume. It enforces:

1. **Citations are mandatory** — the LLM is prompted to quote source chunks
   and we attach `Citation(page, chunk_id, text_snippet)` objects to every
   `Answer`. Empty-citation answers raise CitationRequiredError.
2. **Retrieval scope is bounded** — only chunks from the requested prospectus
   are searchable.
3. **Cost is tracked per call** — each Answer carries cost_usd + runtime.

This module is the cross-Phase 3/5 contract surface: agent tools simply
import ``ProspectusQA`` and call ``.ask()``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from ..common.exceptions import CitationRequiredError
from ..common.llm_client import LLMClient
from ..common.logging import LogContext, get_logger
from ..common.schemas import Citation
from .retriever import HybridResult, HybridRetriever

if TYPE_CHECKING:
    from .vector_store import ProspectusVectorStore

log = get_logger(__name__)


@dataclass
class Answer:
    """One QA answer with mandatory citations."""

    question: str
    text: str
    citations: list[Citation]
    cost_usd: float
    runtime_seconds: float
    retrieved_chunks: list[HybridResult] = field(default_factory=list)


class _LLMAnswer(BaseModel):
    """Constrained LLM response — must list at least one citation chunk_id."""

    answer: str = Field(min_length=1)
    cited_chunk_ids: list[str] = Field(min_length=1, description="chunk_ids actually used in the answer")


class ProspectusQA:
    """The Q&A façade agents call via ``ask(...)``."""

    def __init__(
        self,
        store: ProspectusVectorStore,
        llm: LLMClient,
        *,
        model: str = "moonshot-v1-128k",
    ) -> None:
        self.store = store
        self.llm = llm
        self.model = model
        self.retriever = HybridRetriever(store)

    async def ask(
        self,
        question: str,
        *,
        top_k: int = 5,
        section_filter: str | None = None,
    ) -> Answer:
        """Retrieve relevant chunks and produce a cited answer.

        Raises:
            CitationRequiredError: if the LLM returns no citations.
        """
        with LogContext(prospectus_id=self.store.prospectus_id):
            started = time.monotonic()
            cost_before = float(self.llm.cost_log.total_usd())

            retrieved = await self.retriever.search(
                question, top_k=top_k, section_filter=section_filter
            )
            if not retrieved:
                # Nothing to cite — refuse rather than hallucinate.
                raise CitationRequiredError(
                    f"No chunks retrieved for question: {question[:80]!r}",
                    prospectus_id=self.store.prospectus_id,
                )

            prompt = self._build_prompt(question, retrieved)
            llm_answer = await self.llm.acomplete_json(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                response_model=_LLMAnswer,
                agent_role="prospectus_qa",
                ipo_id=self.store.prospectus_id,
            )

            citations = self._build_citations(llm_answer.cited_chunk_ids, retrieved)
            if not citations:
                raise CitationRequiredError(
                    "LLM cited chunk_ids that don't match any retrieved chunk",
                    cited=llm_answer.cited_chunk_ids,
                    available=[r.hit.chunk_id for r in retrieved],
                )

            elapsed = time.monotonic() - started
            cost_after = float(self.llm.cost_log.total_usd())
            log.info(
                "qa_ask_complete",
                question_len=len(question),
                retrieved=len(retrieved),
                citations=len(citations),
                cost_delta_usd=cost_after - cost_before,
                runtime_seconds=round(elapsed, 3),
            )
            return Answer(
                question=question,
                text=llm_answer.answer,
                citations=citations,
                cost_usd=cost_after - cost_before,
                runtime_seconds=elapsed,
                retrieved_chunks=retrieved,
            )

    # ------------------------------------------------------------------ internals

    @staticmethod
    def _build_prompt(question: str, retrieved: list[HybridResult]) -> str:
        evidence_blocks = "\n\n---\n\n".join(
            f"[chunk_id={r.hit.chunk_id} page={r.hit.page} section={r.hit.section or '?'}]\n"
            f"{r.hit.text}"
            for r in retrieved
        )
        return (
            "You are a meticulous HK IPO prospectus analyst. Answer the user "
            "question using ONLY the provided source chunks. You MUST cite "
            "every chunk you used by its chunk_id. If the chunks don't contain "
            "the answer, say so honestly. Do not invent facts.\n\n"
            f"# Source chunks\n\n{evidence_blocks}\n\n"
            f"# Question\n\n{question}\n\n"
            "# Response format\n\n"
            'Return JSON: {"answer": "<your answer in concise Chinese or English>", '
            '"cited_chunk_ids": ["<chunk_id_1>", "<chunk_id_2>", ...]}'
        )

    @staticmethod
    def _build_citations(
        cited_chunk_ids: list[str], retrieved: list[HybridResult]
    ) -> list[Citation]:
        """Resolve LLM-cited chunk_ids back to Citation objects."""
        by_id = {r.hit.chunk_id: r.hit for r in retrieved}
        citations: list[Citation] = []
        for cid in cited_chunk_ids:
            hit = by_id.get(cid)
            if hit is None:
                continue  # LLM hallucinated an id; skip it
            citations.append(
                Citation(
                    page=hit.page,
                    section=hit.section,
                    chunk_id=hit.chunk_id,
                    text_snippet=hit.text[:240],
                )
            )
        return citations


__all__ = ("Answer", "ProspectusQA")
