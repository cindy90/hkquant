"""Unit tests for prospectus.embeddings (HashEmbeddings + factory)."""

from __future__ import annotations

import pytest

from hk_ipo_agent.prospectus.embeddings import HashEmbeddings, get_embedding_provider


@pytest.mark.asyncio
async def test_hash_embeddings_deterministic() -> None:
    p = HashEmbeddings()
    a = await p.embed_one("hello world")
    b = await p.embed_one("hello world")
    assert a == b
    assert len(a) == HashEmbeddings.dim


@pytest.mark.asyncio
async def test_hash_embeddings_different_texts_different_vectors() -> None:
    p = HashEmbeddings()
    v1 = await p.embed_one("hello")
    v2 = await p.embed_one("world")
    assert v1 != v2


@pytest.mark.asyncio
async def test_hash_embeddings_l2_normalized() -> None:
    p = HashEmbeddings()
    v = await p.embed_one("any text")
    norm = sum(x * x for x in v) ** 0.5
    assert abs(norm - 1.0) < 1e-6


@pytest.mark.asyncio
async def test_hash_embeddings_batch_consistent() -> None:
    p = HashEmbeddings()
    batch = await p.embed(["a", "b", "c"])
    assert len(batch) == 3
    single = await p.embed_one("b")
    assert batch[1] == single


def test_factory_falls_back_to_hash_for_unknown_provider() -> None:
    """Unknown provider should silently fall back to HashEmbeddings."""
    p = get_embedding_provider("nonexistent_provider")
    assert isinstance(p, HashEmbeddings)


def test_factory_returns_hash_when_requested_explicitly() -> None:
    p = get_embedding_provider("hash")
    assert isinstance(p, HashEmbeddings)


def test_factory_falls_back_when_bge_unavailable() -> None:
    """If sentence-transformers isn't installed (default extras), fall back to hash."""
    # In default CI without the embeddings-local extra, this should not raise
    p = get_embedding_provider("bge")
    # Either we get BGE (extras installed) or HashEmbeddings (fallback)
    assert p is not None
