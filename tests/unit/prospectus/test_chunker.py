"""Unit tests for prospectus.chunker."""

from __future__ import annotations

from hk_ipo_agent.prospectus.chunker import (
    ChunkConfig,
    chunk_document,
    detect_section,
)
from hk_ipo_agent.prospectus.schema import ParsedBlock, ParsedDocument, ParserBackend


def _doc_with(blocks: list[ParsedBlock]) -> ParsedDocument:
    return ParsedDocument(
        prospectus_id="P-TEST",
        backend=ParserBackend.PYMUPDF,
        page_count=max((b.page for b in blocks), default=0),
        full_text="\n".join(b.text for b in blocks),
        blocks=blocks,
    )


def test_detect_section_recognizes_common_zh_en_headings() -> None:
    assert detect_section("风险因素") == "risk_factors"
    assert detect_section("RISK FACTORS") == "risk_factors"
    assert detect_section("业务") == "business"
    assert detect_section("BUSINESS") == "business"
    assert detect_section("基石投资者及锚定投资者") == "cornerstone"
    assert detect_section("第一节 概要") == "numbered_section_zh"


def test_detect_section_ignores_long_lines() -> None:
    """Lines longer than 100 chars are body text, not headings."""
    long_text = "This is a very long sentence that exceeds one hundred characters and " * 3
    assert detect_section(long_text) is None


def test_detect_section_returns_none_for_body_text() -> None:
    assert detect_section("Revenue grew 50% year over year.") is None
    assert detect_section("") is None


def test_chunk_document_packs_blocks_into_target_size() -> None:
    blocks = [
        ParsedBlock(page=1, text="Block " + str(i) * 200, char_offset=i * 1000)
        for i in range(10)
    ]
    doc = _doc_with(blocks)
    chunks = chunk_document(doc, config=ChunkConfig(target_chars=500, min_chars=100))
    assert len(chunks) >= 2
    # Every chunk has the expected metadata structure
    for c in chunks:
        assert c.prospectus_id == "P-TEST"
        assert c.text
        assert c.char_offset >= 0
        assert c.chunk_id


def test_chunk_document_emits_section_at_boundary() -> None:
    blocks = [
        ParsedBlock(page=1, text="业务", char_offset=0),
        ParsedBlock(page=1, text="A" * 300, char_offset=10),
        ParsedBlock(page=2, text="风险因素", char_offset=400),
        ParsedBlock(page=2, text="B" * 300, char_offset=420),
    ]
    doc = _doc_with(blocks)
    chunks = chunk_document(doc, config=ChunkConfig(target_chars=200, min_chars=100))
    sections = {c.section for c in chunks}
    assert "business" in sections
    assert "risk_factors" in sections


def test_chunk_id_is_deterministic() -> None:
    block = ParsedBlock(page=1, text="hello world", char_offset=0)
    doc = _doc_with([block])
    a = chunk_document(doc)
    b = chunk_document(doc)
    assert [c.chunk_id for c in a] == [c.chunk_id for c in b]


def test_oversized_block_emitted_as_own_chunk() -> None:
    """A single block above max_chars should NOT be split."""
    huge = ParsedBlock(page=1, text="X" * 5000, char_offset=0)
    doc = _doc_with([huge])
    chunks = chunk_document(doc, config=ChunkConfig(max_chars=2500))
    assert len(chunks) == 1
    assert chunks[0].metadata.get("oversized") is True


def test_empty_document_yields_no_chunks() -> None:
    doc = _doc_with([])
    assert chunk_document(doc) == []


def test_tables_become_standalone_chunks() -> None:
    from hk_ipo_agent.prospectus.schema import ParsedTable  # noqa: PLC0415

    doc = ParsedDocument(
        prospectus_id="P-TEST",
        backend=ParserBackend.PYMUPDF,
        page_count=1,
        full_text="",
        blocks=[],
        tables=[
            ParsedTable(
                page=2,
                rows=[["Header"], ["Cell"]],
                markdown="| Header |\n| Cell |",
                char_offset=0,
                caption="Table 1",
            )
        ],
    )
    chunks = chunk_document(doc)
    assert len(chunks) == 1
    assert chunks[0].metadata["type"] == "table"
    assert chunks[0].metadata["rows"] == 2
    assert chunks[0].metadata["caption"] == "Table 1"
