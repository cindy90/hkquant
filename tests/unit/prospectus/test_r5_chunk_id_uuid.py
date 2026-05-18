"""R5-3 — chunk_id is a deterministic UUID5 string, passed directly to Qdrant.

Pre-R5-3:
  * ``_make_chunk_id`` returned ``hashlib.sha256(...).hexdigest()[:32]`` —
    a 32-char hex string, not a valid UUID format.
  * ``vector_store._chunk_id_to_point_id`` coerced this to a uint64 via
    ``int(chunk_id[:16], 16)`` — dropping 192 bits of the 256-bit hash.
    With ~1.3 K cornerstone profiles and tens of thousands of chunks per
    prospectus the truncation moved collision probability from
    cryptographic (2^-128) to merely "probably fine" (~2^-32 birthday).
  * The Citation.chunk_id (which is what makes its way back to UI / audit
    log) was the sha256 truncation, while Qdrant's point id was the int
    truncation — two different IDs for "the same chunk".

Post-R5-3:
  * ``_make_chunk_id`` returns ``str(uuid5(_NAMESPACE_CHUNK, ...))`` —
    a 36-char hyphenated UUID, deterministic for the same inputs,
    accepted by Qdrant as a point id verbatim.
  * vector_store passes ``c.chunk_id`` straight into PointStruct(id=...);
    no helper conversion exists or is needed.

This file pins both halves: the chunker contract AND the vector_store
upsert path. The vector_store half mocks AsyncQdrantClient so we don't
need a live Qdrant for the perf-shape contract.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from hk_ipo_agent.prospectus.chunker import ChunkConfig, _make_chunk_id, chunk_document
from hk_ipo_agent.prospectus.schema import (
    Chunk,
    ParsedBlock,
    ParsedDocument,
    ParserBackend,
)
from hk_ipo_agent.prospectus.vector_store import ProspectusVectorStore


def _doc_with(blocks: list[ParsedBlock]) -> ParsedDocument:
    return ParsedDocument(
        prospectus_id="P-TEST",
        backend=ParserBackend.PYMUPDF,
        page_count=1,
        full_text="\n".join(b.text for b in blocks),
        blocks=blocks,
    )


# ------------------------------------------------------------------ _make_chunk_id


def test_make_chunk_id_returns_valid_uuid_string() -> None:
    """R5-3 — chunk_id is a parseable UUID (length 36 with hyphens)."""
    cid = _make_chunk_id("P-TEST", 0, 100)
    parsed = UUID(cid)
    assert str(parsed) == cid
    assert len(cid) == 36
    assert cid.count("-") == 4


def test_make_chunk_id_is_deterministic() -> None:
    """R5-3 — same inputs → byte-identical UUID. Re-upserts stay idempotent."""
    a = _make_chunk_id("P-TEST", 100, 1500)
    b = _make_chunk_id("P-TEST", 100, 1500)
    assert a == b


def test_make_chunk_id_distinct_for_different_inputs() -> None:
    """R5-3 — minor input changes produce distinct UUIDs (no truncation collision)."""
    base = _make_chunk_id("P-TEST", 100, 1500)
    diff_offset = _make_chunk_id("P-TEST", 101, 1500)
    diff_len = _make_chunk_id("P-TEST", 100, 1501)
    diff_prosp = _make_chunk_id("P-OTHER", 100, 1500)
    assert len({base, diff_offset, diff_len, diff_prosp}) == 4


def test_chunk_document_emits_uuid_chunk_ids() -> None:
    """R5-3 — chunker integration: every Chunk.chunk_id parses as a UUID."""
    blocks = [
        ParsedBlock(page=1, text="Block " + str(i) * 200, char_offset=i * 1000) for i in range(5)
    ]
    doc = _doc_with(blocks)
    chunks = chunk_document(doc, config=ChunkConfig(target_chars=500, min_chars=100))
    for c in chunks:
        UUID(c.chunk_id)  # raises if invalid


# ------------------------------------------------------------------ vector_store


@pytest.mark.asyncio
async def test_upsert_passes_chunk_id_directly_to_qdrant() -> None:
    """R5-3 — PointStruct.id == chunk_id (no helper conversion).

    Pre-fix this was ``int(chunk_id[:16], 16)``; the post-fix vector_store
    has no such helper. We assert by mocking AsyncQdrantClient and reading
    back the points kwarg.
    """
    fake_client = AsyncMock()
    fake_client.collection_exists = AsyncMock(return_value=True)
    fake_client.upsert = AsyncMock(return_value=None)

    class _FakeProvider:
        dim = 4

        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.0, 0.0, 0.0, 0.0] for _ in texts]

        async def embed_one(self, text: str) -> list[float]:
            return [0.0, 0.0, 0.0, 0.0]

    store = ProspectusVectorStore("P-TEST", provider=_FakeProvider(), client=fake_client)
    cid = _make_chunk_id("P-TEST", 0, 5)
    chunk = Chunk(
        chunk_id=cid,
        prospectus_id="P-TEST",
        page=1,
        section="business",
        char_offset=0,
        text="hello",
    )
    await store.upsert_chunks([chunk])

    fake_client.upsert.assert_awaited_once()
    points = fake_client.upsert.await_args.kwargs["points"]
    assert len(points) == 1
    # The critical assertion: PointStruct.id IS the chunk_id string, not a uint64.
    assert points[0].id == cid
    assert isinstance(points[0].id, str)
    # And the payload preserves chunk_id for back-reference.
    assert points[0].payload["chunk_id"] == cid


def test_no_chunk_id_to_point_id_helper_exists() -> None:
    """R5-3 — the lossy int-truncation helper has been deleted entirely.

    Importing it should fail with AttributeError; this stops anyone from
    accidentally reintroducing the conversion path.
    """
    import hk_ipo_agent.prospectus.vector_store as vs_mod

    assert not hasattr(vs_mod, "_chunk_id_to_point_id")
