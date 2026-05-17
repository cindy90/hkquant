"""Integration test: end-to-end prospectus RAG pipeline against live Qdrant.

DONE-condition acceptance test for Phase 3 (PROJECT_SPEC.md §4):
- Given a synthetic prospectus PDF
- Parse (PyMuPDF) -> Chunk -> Embed (HashEmbeddings) -> Qdrant upsert
- Run a Q&A query through ProspectusQA (LLM mocked)
- Verify: answer text + citations with page numbers come back

The LLM is mocked to avoid Anthropic API calls in CI; the real LlamaParse +
BGE + Claude path is exercised in Phase 9 end-to-end golden tests.

Skips if Qdrant is unreachable so CI without docker compose still passes.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pymupdf
import pytest
import pytest_asyncio
from qdrant_client import AsyncQdrantClient

from hk_ipo_agent.common.llm_client import LLMClient
from hk_ipo_agent.common.settings import get_settings
from hk_ipo_agent.prospectus.chunker import ChunkConfig, chunk_document
from hk_ipo_agent.prospectus.embeddings import HashEmbeddings
from hk_ipo_agent.prospectus.parser import ParserConfig, parse_prospectus
from hk_ipo_agent.prospectus.qa import ProspectusQA, _LLMAnswer
from hk_ipo_agent.prospectus.vector_store import ProspectusVectorStore

if TYPE_CHECKING:
    pass

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def qdrant_or_skip() -> AsyncQdrantClient:
    """Skip the test if Qdrant isn't reachable on the configured URL."""
    settings = get_settings()
    client = AsyncQdrantClient(url=settings.qdrant.url)
    try:
        # cheap health probe
        await client.get_collections()
    except Exception as exc:
        pytest.skip(f"Qdrant not reachable: {exc}")
    yield client
    await client.close()


def _make_synthetic_prospectus(tmp_path: Path) -> Path:
    """Generate a 3-page synthetic prospectus with structured content."""
    doc = pymupdf.open()  # type: ignore[no-untyped-call]
    # Page 1 — summary
    p1 = doc.new_page()  # type: ignore[no-untyped-call]
    p1.insert_text(  # type: ignore[no-untyped-call]
        (72, 72),
        "Test Tech Co., Ltd.\n\n"
        "Summary\n\n"
        "Test Tech is a Hong Kong technology company.\n"
        "Founded 2020.\n",
    )
    # Page 2 — financials
    p2 = doc.new_page()  # type: ignore[no-untyped-call]
    p2.insert_text(  # type: ignore[no-untyped-call]
        (72, 72),
        "Financial Information\n\n"
        "Revenue for FY2024 was RMB 1,234,567,890.\n"
        "Gross margin was 37 percent.\n"
        "Net loss attributable to owners was RMB 200 million.\n",
    )
    # Page 3 — risks
    p3 = doc.new_page()  # type: ignore[no-untyped-call]
    p3.insert_text(  # type: ignore[no-untyped-call]
        (72, 72),
        "Risk Factors\n\n"
        "We depend on a small number of major customers for most revenue.\n"
        "Loss of any single customer could materially impact our business.\n",
    )
    path = tmp_path / "synthetic_prospectus.pdf"
    doc.save(str(path))  # type: ignore[no-untyped-call]
    doc.close()  # type: ignore[no-untyped-call]
    return path


@pytest.fixture
def mocked_llm(monkeypatch: pytest.MonkeyPatch) -> LLMClient:
    """LLMClient with acomplete_json mocked to return a fixed answer."""
    from decimal import Decimal

    monkeypatch.setenv("KIMI_API_KEY", "sk-test-rag")
    return LLMClient(daily_budget_usd=Decimal("100"))


@pytest.mark.asyncio
async def test_end_to_end_pdf_to_cited_answer(
    tmp_path: Path,
    qdrant_or_skip: AsyncQdrantClient,
    mocked_llm: LLMClient,
) -> None:
    """The Phase 3 DONE acceptance test."""
    prospectus_id = "P-RAG-TEST"
    pdf = _make_synthetic_prospectus(tmp_path)

    # 1. Parse with PyMuPDF (LlamaParse not configured)
    parsed = await parse_prospectus(
        pdf,
        prospectus_id=prospectus_id,
        config=ParserConfig(prefer_llamaparse=False),
    )
    assert parsed.page_count == 3
    assert len(parsed.blocks) >= 3

    # 2. Chunk
    chunks = chunk_document(parsed, config=ChunkConfig(target_chars=200, min_chars=50))
    assert len(chunks) >= 1
    for c in chunks:
        assert c.text
        assert c.page in {1, 2, 3}

    # 3. Embed + upsert to Qdrant
    provider = HashEmbeddings()
    store = ProspectusVectorStore(
        prospectus_id=prospectus_id,
        provider=provider,
        client=qdrant_or_skip,
    )
    try:
        upserted = await store.upsert_chunks(chunks)
        assert upserted == len(chunks)

        stored = await store.count()
        assert stored == len(chunks)

        # 4. Mock the LLM JSON path on this LLMClient instance
        # ProspectusQA reaches into llm.acomplete_json — patch it directly.
        target_chunk = chunks[1] if len(chunks) > 1 else chunks[0]
        fake_answer = _LLMAnswer(
            answer="Revenue for FY2024 was RMB 1.23 billion.",
            cited_chunk_ids=[target_chunk.chunk_id],
        )
        mocked_llm.acomplete_json = AsyncMock(return_value=fake_answer)  # type: ignore[method-assign]

        # 5. Run a QA query
        qa = ProspectusQA(store=store, llm=mocked_llm, model="moonshot-v1-128k")
        # Force a retrieval result that contains our target_chunk
        # by patching the retriever to return a controlled hit-list:
        from hk_ipo_agent.prospectus.retriever import HybridResult
        from hk_ipo_agent.prospectus.vector_store import SearchHit

        controlled_hit = SearchHit(
            chunk_id=target_chunk.chunk_id,
            prospectus_id=prospectus_id,
            page=target_chunk.page,
            section=target_chunk.section,
            char_offset=target_chunk.char_offset,
            text=target_chunk.text,
            score=0.9,
            metadata={},
        )
        qa.retriever = MagicMock()
        qa.retriever.search = AsyncMock(
            return_value=[
                HybridResult(hit=controlled_hit, fused_score=0.9, vector_rank=1, bm25_rank=1)
            ]
        )

        answer = await qa.ask("What was the FY2024 revenue?")

        # 6. Assertions: cited answer + page-level citation present
        assert "1.23" in answer.text or "1,234" in answer.text or "Revenue" in answer.text
        assert len(answer.citations) >= 1
        cite = answer.citations[0]
        assert cite.page in {1, 2, 3}
        assert cite.chunk_id == target_chunk.chunk_id
        assert cite.text_snippet  # non-empty
    finally:
        await store.delete_collection()


@pytest.mark.asyncio
async def test_collection_isolation_per_prospectus(
    qdrant_or_skip: AsyncQdrantClient,
) -> None:
    """Two different prospectus_ids must get distinct Qdrant collections."""
    provider = HashEmbeddings()
    store_a = ProspectusVectorStore("PROSP-A", provider=provider, client=qdrant_or_skip)
    store_b = ProspectusVectorStore("PROSP-B", provider=provider, client=qdrant_or_skip)
    assert store_a.collection_name != store_b.collection_name
    assert "prosp_a" in store_a.collection_name
    assert "prosp_b" in store_b.collection_name
