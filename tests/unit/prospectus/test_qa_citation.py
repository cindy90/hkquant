"""Tests for the citation-required contract of ProspectusQA.

These mock the entire retrieval + LLM stack so we can pin behavior:
- citations are mandatory
- LLM-cited chunk_ids must match retrieved chunks
- empty retrieval raises CitationRequiredError
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from hk_ipo_agent.common.exceptions import CitationRequiredError
from hk_ipo_agent.common.llm_client import LLMClient
from hk_ipo_agent.prospectus.qa import ProspectusQA, _LLMAnswer
from hk_ipo_agent.prospectus.retriever import HybridResult
from hk_ipo_agent.prospectus.vector_store import SearchHit


def _make_hit(chunk_id: str, *, page: int = 100, text: str = "sample text") -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id,
        prospectus_id="P-TEST",
        page=page,
        section="financials",
        char_offset=0,
        text=text,
        score=0.9,
        metadata={},
    )


def _make_hybrid(hit: SearchHit) -> HybridResult:
    return HybridResult(hit=hit, fused_score=0.9, vector_rank=1, bm25_rank=1)


def _stub_qa(retrieval_hits: list[HybridResult], llm_answer: _LLMAnswer) -> ProspectusQA:
    store = MagicMock()
    store.prospectus_id = "P-TEST"
    llm = MagicMock(spec=LLMClient)
    llm.cost_log = MagicMock()
    llm.cost_log.total_usd = lambda: 0.0
    llm.acomplete_json = AsyncMock(return_value=llm_answer)
    qa = ProspectusQA(store=store, llm=llm)
    # Patch the retriever instance to return our fixed hits
    qa.retriever = MagicMock()
    qa.retriever.search = AsyncMock(return_value=retrieval_hits)
    return qa


@pytest.mark.asyncio
async def test_ask_with_valid_citations_returns_answer() -> None:
    hits = [_make_hybrid(_make_hit("chunk_a", page=42, text="Revenue grew 50%."))]
    answer = _LLMAnswer(answer="Revenue grew 50% YoY.", cited_chunk_ids=["chunk_a"])
    qa = _stub_qa(hits, answer)

    result = await qa.ask("How much did revenue grow?")
    assert result.text == "Revenue grew 50% YoY."
    assert len(result.citations) == 1
    assert result.citations[0].page == 42
    assert result.citations[0].chunk_id == "chunk_a"
    assert "Revenue grew 50%" in (result.citations[0].text_snippet or "")


@pytest.mark.asyncio
async def test_ask_raises_when_no_chunks_retrieved() -> None:
    qa = _stub_qa([], _LLMAnswer(answer="anything", cited_chunk_ids=["x"]))
    with pytest.raises(CitationRequiredError, match="No chunks retrieved"):
        await qa.ask("a question")


@pytest.mark.asyncio
async def test_ask_raises_when_llm_hallucinates_chunk_ids() -> None:
    """LLM cites chunk_ids that aren't in retrieval -> raises."""
    hits = [_make_hybrid(_make_hit("real_chunk"))]
    answer = _LLMAnswer(answer="fake answer", cited_chunk_ids=["hallucinated_id"])
    qa = _stub_qa(hits, answer)

    with pytest.raises(CitationRequiredError, match="cited chunk_ids that don't match"):
        await qa.ask("question")


@pytest.mark.asyncio
async def test_ask_filters_hallucinated_ids_but_keeps_valid_ones() -> None:
    """Mixed cite list — should keep the valid ones, drop the hallucinated."""
    hits = [
        _make_hybrid(_make_hit("chunk_a", page=10)),
        _make_hybrid(_make_hit("chunk_b", page=20)),
    ]
    answer = _LLMAnswer(
        answer="combined answer",
        cited_chunk_ids=["chunk_a", "hallucinated", "chunk_b"],
    )
    qa = _stub_qa(hits, answer)

    result = await qa.ask("question")
    cited_ids = [c.chunk_id for c in result.citations]
    assert cited_ids == ["chunk_a", "chunk_b"]


@pytest.mark.asyncio
async def test_pydantic_rejects_empty_citation_list() -> None:
    """The _LLMAnswer model enforces min_length=1 on cited_chunk_ids."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _LLMAnswer(answer="some answer", cited_chunk_ids=[])


@pytest.mark.asyncio
async def test_ask_tracks_runtime_and_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    hits = [_make_hybrid(_make_hit("chunk_a"))]
    answer = _LLMAnswer(answer="x", cited_chunk_ids=["chunk_a"])
    qa = _stub_qa(hits, answer)

    # Make cost_log report increasing total
    state: dict[str, Any] = {"calls": 0}

    def fake_total() -> float:
        state["calls"] += 1
        return float(state["calls"]) * 0.01

    qa.llm.cost_log.total_usd = fake_total  # type: ignore[attr-defined]

    result = await qa.ask("q")
    assert result.cost_usd > 0
    assert result.runtime_seconds >= 0
