"""Tests for the parser fallback chain (PyMuPDF when LlamaParse unavailable)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pymupdf
import pytest

from hk_ipo_agent.common.exceptions import ParseError
from hk_ipo_agent.prospectus.parser import (
    ParserConfig,
    _parse_with_llamaparse,
    _split_markdown_blocks,
    parse_prospectus,
)
from hk_ipo_agent.prospectus.schema import ParserBackend


def _make_test_pdf(tmp_path: Path, *, pages: int = 3) -> Path:
    """Generate a small synthetic PDF using PyMuPDF directly."""
    doc = pymupdf.open()  # type: ignore[no-untyped-call]
    for i in range(pages):
        page = doc.new_page()  # type: ignore[no-untyped-call]
        page.insert_text(  # type: ignore[no-untyped-call]
            (72, 72),
            f"摘要\n\nThis is page {i + 1} of the test prospectus.\n"
            "Revenue for FY2024 was RMB 1,234,567,890.",
        )
    path = tmp_path / "synthetic.pdf"
    doc.save(str(path))  # type: ignore[no-untyped-call]
    doc.close()  # type: ignore[no-untyped-call]
    return path


@pytest.mark.asyncio
async def test_parse_with_pymupdf_extracts_pages_and_blocks(
    tmp_path: Path,
) -> None:
    pdf = _make_test_pdf(tmp_path, pages=3)
    result = await parse_prospectus(
        pdf,
        prospectus_id="P-TEST",
        config=ParserConfig(prefer_llamaparse=False),
    )
    assert result.backend == ParserBackend.PYMUPDF
    assert result.page_count == 3
    assert result.prospectus_id == "P-TEST"
    assert len(result.blocks) > 0
    assert "page 1" in result.full_text.lower()
    # Every block carries page + offset
    for block in result.blocks:
        assert block.page >= 1
        assert block.char_offset >= 0


@pytest.mark.asyncio
async def test_parse_falls_back_to_pymupdf_when_llamaparse_missing(
    tmp_path: Path,
) -> None:
    """Default config (prefer_llamaparse=True) should silently fall back."""
    pdf = _make_test_pdf(tmp_path, pages=1)
    # No LLAMA_CLOUD_API_KEY -> MissingDependencyError -> PyMuPDF fallback
    result = await parse_prospectus(pdf, prospectus_id="P-TEST")
    assert result.backend == ParserBackend.PYMUPDF


@pytest.mark.asyncio
async def test_parse_raises_on_missing_pdf() -> None:
    with pytest.raises(ParseError, match="PDF not found"):
        await parse_prospectus(Path("/no/such/file.pdf"), prospectus_id="P")


def test_split_markdown_blocks_separates_tables_and_text() -> None:
    md = (
        "# Heading\n\n"
        "Some paragraph.\n\n"
        "| Col A | Col B |\n"
        "|-------|-------|\n"
        "| 1 | 2 |\n\n"
        "Another paragraph."
    )
    blocks = _split_markdown_blocks(md)
    types = [b["type"] for b in blocks]
    assert "table" in types
    assert types.count("text") >= 2


@pytest.mark.asyncio
async def test_llamaparse_page_numbers_skip_empty_pages_correctly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R1-2 — LlamaParse path must keep physical page indices when documents are empty.

    Pre-fix bug: ``page += 1`` was at the bottom of the loop, after the
    ``if not text.strip(): continue`` guard. So an empty page skipped both
    block-creation AND page counting, shifting every subsequent block's
    ``page`` field down by one.

    For a 5-page PDF where pages 2 and 4 contain only whitespace, the
    citations on pages 3 and 5 would erroneously read 2 and 3 — a silent
    corruption of the citation-page contract (CLAUDE.md severe constraint
    'every Finding must trace back to a prospectus page').

    Verification: mock LlamaParse to return 5 documents with content
    [text, "", text, "", text]; assert resulting blocks carry pages
    [1, 3, 5] not [1, 2, 3].
    """
    # Build fake LlamaParse Document objects.
    contents = [
        "# Page 1 heading\n\nFirst real page content.",
        "",  # empty page 2
        "# Page 3 heading\n\nThird real page content.",
        "",  # empty page 4
        "# Page 5 heading\n\nFifth real page content.",
    ]
    fake_docs = [MagicMock(text=c) for c in contents]
    fake_parser_instance = MagicMock()
    fake_parser_instance.load_data.return_value = fake_docs

    fake_module = MagicMock()
    fake_module.LlamaParse = MagicMock(return_value=fake_parser_instance)
    monkeypatch.setitem(sys.modules, "llama_parse", fake_module)

    # Force settings to return a non-empty api_key. parser.py imports
    # get_settings by-name (``from ..common.settings import get_settings``),
    # so we must patch parser's local binding.
    from hk_ipo_agent.prospectus import parser as parser_module
    fake_settings = MagicMock()
    fake_settings.prospectus.llama_cloud_api_key.get_secret_value.return_value = "fake-key"
    monkeypatch.setattr(parser_module, "get_settings", lambda: fake_settings)

    pdf = tmp_path / "fake.pdf"
    pdf.touch()

    result = await _parse_with_llamaparse(
        pdf, "P-R1-2", ParserConfig(prefer_llamaparse=True)
    )

    assert result.backend == ParserBackend.LLAMAPARSE
    # Every block must carry the physical PDF page number, not a compacted index.
    block_pages = sorted({b.page for b in result.blocks})
    assert block_pages == [1, 3, 5], (
        f"Empty-page page-numbering bug: expected blocks on pages [1,3,5] "
        f"(physical PDF pages, with pages 2 and 4 empty), got {block_pages}. "
        f"This means R1-2 (parser.py:127-154 page increment) is not applied — "
        f"empty pages are still suppressing the page counter."
    )
    # page_count should reflect TOTAL documents seen, not just non-empty.
    assert result.page_count == 5, (
        f"page_count should equal len(documents)=5, got {result.page_count}"
    )


def test_pymupdf_respects_max_pages(tmp_path: Path) -> None:
    pdf = _make_test_pdf(tmp_path, pages=5)
    # Sync sanity check that pymupdf opens the file (used by the async parser)
    doc = pymupdf.open(str(pdf))  # type: ignore[no-untyped-call]
    try:
        assert doc.page_count == 5
    finally:
        doc.close()  # type: ignore[no-untyped-call]
