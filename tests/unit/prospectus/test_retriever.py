"""Tests for `hk_ipo_agent.prospectus.retriever` — BM25 + hybrid RRF fusion."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from hk_ipo_agent.prospectus.retriever import (
    HybridRetriever,
    _BM25Index,
    _tokenize,
)
from hk_ipo_agent.prospectus.vector_store import SearchHit

# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------


def test_tokenize_ascii_words() -> None:
    tokens = _tokenize("Revenue grew 50% YoY")
    assert "revenue" in tokens
    assert "grew" in tokens
    assert "50" in tokens
    assert "yoy" in tokens


def test_tokenize_cjk_characters() -> None:
    # The regex [\w一-鿿]+ treats CJK + digits as a single token run.
    tokens = _tokenize("公司收入增长50%")
    assert "公司收入增长50" in tokens


def test_tokenize_mixed_cjk_and_ascii() -> None:
    # Space splits tokens; CJK/ASCII within same run stay together.
    tokens = _tokenize("AI收入达到RMB 123万元")
    assert "ai收入达到rmb" in tokens
    assert "123万元" in tokens


def test_tokenize_empty_string() -> None:
    assert _tokenize("") == []


def test_tokenize_only_punctuation() -> None:
    assert _tokenize("!@#$%^&*()") == []


# ---------------------------------------------------------------------------
# BM25 Index
# ---------------------------------------------------------------------------


def _make_hits(texts: list[str]) -> list[SearchHit]:
    """Helper to create SearchHit objects from text list."""
    return [
        SearchHit(
            chunk_id=f"chunk-{i}",
            prospectus_id="P-TEST",
            page=i + 1,
            section="financials" if i % 2 == 0 else "business",
            char_offset=i * 100,
            text=text,
            score=0.0,
            metadata={},
        )
        for i, text in enumerate(texts)
    ]


def test_bm25_search_returns_relevant_results() -> None:
    chunks = _make_hits([
        "Revenue for FY2024 was RMB 1.2 billion, growing 35% year over year.",
        "The company operates a B2B SaaS platform for enterprise customers.",
        "Revenue concentration: top 5 customers account for 61% of revenue.",
        "Risk: dependency on a small number of key customers.",
    ])
    index = _BM25Index(chunks)
    results = index.search("revenue customers", top_k=3, section_filter=None)
    assert len(results) >= 2
    # "Revenue concentration" chunk and "Revenue for FY2024" should rank high
    result_ids = [r.chunk_id for r in results]
    assert "chunk-0" in result_ids  # has "revenue"
    assert "chunk-2" in result_ids  # has both "revenue" and "customers"


def test_bm25_search_with_section_filter() -> None:
    chunks = _make_hits([
        "Revenue grew 50%.",  # section=financials (i=0)
        "Business model is SaaS.",  # section=business (i=1)
        "Revenue from services.",  # section=financials (i=2)
    ])
    index = _BM25Index(chunks)
    results = index.search("revenue", top_k=10, section_filter="financials")
    # Only chunks with section="financials" should appear
    assert len(results) == 2
    for r in results:
        assert r.section == "financials"


def test_bm25_search_empty_query_returns_empty() -> None:
    chunks = _make_hits(["Some text here."])
    index = _BM25Index(chunks)
    results = index.search("", top_k=5, section_filter=None)
    assert results == []


def test_bm25_search_no_match_returns_empty() -> None:
    chunks = _make_hits(["The company manufactures widgets."])
    index = _BM25Index(chunks)
    results = index.search("quantum computing blockchain", top_k=5, section_filter=None)
    assert results == []


def test_bm25_search_respects_top_k() -> None:
    chunks = _make_hits([
        "Revenue from product A.",
        "Revenue from product B.",
        "Revenue from product C.",
        "Revenue from product D.",
    ])
    index = _BM25Index(chunks)
    results = index.search("revenue product", top_k=2, section_filter=None)
    assert len(results) == 2


def test_bm25_empty_corpus() -> None:
    index = _BM25Index([])
    results = index.search("anything", top_k=5, section_filter=None)
    assert results == []


def test_bm25_idf_weights_rare_terms_higher() -> None:
    chunks = _make_hits([
        "apple banana cherry",  # chunk-0
        "apple banana dragonfruit",  # chunk-1
        "apple elderberry fig",  # chunk-2
    ])
    index = _BM25Index(chunks)
    # "cherry" only in doc 0; "apple" in all docs → IDF of "cherry" > IDF of "apple"
    assert index._idf["cherry"] > index._idf["apple"]


def test_bm25_score_returned_in_hit() -> None:
    chunks = _make_hits(["keyword here", "no match"])
    index = _BM25Index(chunks)
    results = index.search("keyword", top_k=5, section_filter=None)
    assert len(results) == 1
    assert results[0].score > 0
    assert results[0].chunk_id == "chunk-0"


# ---------------------------------------------------------------------------
# HybridRetriever (RRF fusion)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hybrid_retriever_fuses_both_lanes() -> None:
    """Hits from both vector + BM25 get fused scores."""
    # Create mock store
    store = MagicMock()
    store.prospectus_id = "P-TEST"
    store.collection_name = "prospectus_p_test"

    # Vector lane returns chunk-A and chunk-B
    chunk_a = SearchHit("chunk-A", "P-TEST", 1, "financials", 0, "Revenue grew 50%", 0.95, {})
    chunk_b = SearchHit("chunk-B", "P-TEST", 2, "business", 100, "SaaS platform", 0.85, {})
    store.search = AsyncMock(return_value=[chunk_a, chunk_b])

    retriever = HybridRetriever(store, rrf_k=60)

    # Pre-build BM25 with chunks that include chunk-A and chunk-C
    chunk_c = SearchHit("chunk-C", "P-TEST", 3, "risks", 200, "Revenue risk factor", 0.0, {})
    retriever._bm25 = _BM25Index([chunk_a, chunk_b, chunk_c])

    results = await retriever.search("Revenue", top_k=3)

    assert len(results) >= 1
    # chunk-A appears in both lanes → should have the highest fused score
    assert results[0].hit.chunk_id == "chunk-A"
    assert results[0].vector_rank is not None
    assert results[0].bm25_rank is not None
    assert results[0].fused_score > 0


@pytest.mark.asyncio
async def test_hybrid_retriever_vector_only_hit_still_returned() -> None:
    """A hit that only appears in the vector lane still gets a fused score."""
    store = MagicMock()
    store.prospectus_id = "P-TEST"
    store.collection_name = "prospectus_p_test"

    # Vector returns chunk-unique (text has no overlap with BM25 query terms)
    chunk_unique = SearchHit("chunk-U", "P-TEST", 1, "other", 0, "XYZ patent filing", 0.9, {})
    store.search = AsyncMock(return_value=[chunk_unique])

    retriever = HybridRetriever(store, rrf_k=60)
    # BM25 corpus doesn't contain "patent" so query "patent" won't find anything in BM25
    retriever._bm25 = _BM25Index([
        SearchHit("chunk-Z", "P-TEST", 2, "other", 100, "something else entirely", 0.0, {})
    ])

    results = await retriever.search("patent filing", top_k=3)
    assert len(results) >= 1
    # chunk-U should be in results from vector lane
    ids = [r.hit.chunk_id for r in results]
    assert "chunk-U" in ids
    # Its bm25_rank should be None (not found in BM25)
    u_result = next(r for r in results if r.hit.chunk_id == "chunk-U")
    assert u_result.vector_rank == 1
    assert u_result.bm25_rank is None


@pytest.mark.asyncio
async def test_hybrid_retriever_respects_top_k() -> None:
    store = MagicMock()
    store.prospectus_id = "P-TEST"
    store.collection_name = "prospectus_p_test"

    hits = [
        SearchHit(f"chunk-{i}", "P-TEST", i, "financials", i * 100, f"revenue item {i}", 0.9 - i * 0.1, {})
        for i in range(10)
    ]
    store.search = AsyncMock(return_value=hits)
    retriever = HybridRetriever(store, rrf_k=60)
    retriever._bm25 = _BM25Index(hits)

    results = await retriever.search("revenue item", top_k=3)
    assert len(results) == 3


@pytest.mark.asyncio
async def test_hybrid_retriever_rrf_k_affects_score() -> None:
    """Smaller rrf_k gives more weight to top ranks."""
    store = MagicMock()
    store.prospectus_id = "P-TEST"
    store.collection_name = "prospectus_p_test"

    chunk = SearchHit("chunk-1", "P-TEST", 1, "financials", 0, "revenue growth", 0.9, {})
    store.search = AsyncMock(return_value=[chunk])

    # Small rrf_k (rank 1 score = 1/(10+1) = 0.0909)
    retriever_small_k = HybridRetriever(store, rrf_k=10)
    retriever_small_k._bm25 = _BM25Index([chunk])
    results_small = await retriever_small_k.search("revenue", top_k=5)

    # Large rrf_k (rank 1 score = 1/(100+1) = 0.0099)
    retriever_large_k = HybridRetriever(store, rrf_k=100)
    retriever_large_k._bm25 = _BM25Index([chunk])
    results_large = await retriever_large_k.search("revenue", top_k=5)

    # Smaller k → higher absolute fused score
    assert results_small[0].fused_score > results_large[0].fused_score


@pytest.mark.asyncio
async def test_hybrid_retriever_section_filter_passed_to_both_lanes() -> None:
    """Section filter is forwarded to both vector store and BM25."""
    store = MagicMock()
    store.prospectus_id = "P-TEST"
    store.collection_name = "prospectus_p_test"

    chunk_fin = SearchHit("chunk-fin", "P-TEST", 1, "financials", 0, "revenue data", 0.9, {})
    chunk_biz = SearchHit("chunk-biz", "P-TEST", 2, "business", 100, "revenue model", 0.8, {})
    store.search = AsyncMock(return_value=[chunk_fin])  # vector already filtered

    retriever = HybridRetriever(store, rrf_k=60)
    retriever._bm25 = _BM25Index([chunk_fin, chunk_biz])

    await retriever.search("revenue", top_k=5, section_filter="financials")

    # Verify store.search was called with section_filter
    store.search.assert_awaited_once()
    call_kwargs = store.search.call_args[1]
    assert call_kwargs["section_filter"] == "financials"
