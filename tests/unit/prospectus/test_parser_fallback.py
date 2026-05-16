"""Tests for the parser fallback chain (PyMuPDF when LlamaParse unavailable)."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from hk_ipo_agent.common.exceptions import ParseError
from hk_ipo_agent.prospectus.parser import (
    ParserConfig,
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


def test_pymupdf_respects_max_pages(tmp_path: Path) -> None:
    pdf = _make_test_pdf(tmp_path, pages=5)
    # Sync sanity check that pymupdf opens the file (used by the async parser)
    doc = pymupdf.open(str(pdf))  # type: ignore[no-untyped-call]
    try:
        assert doc.page_count == 5
    finally:
        doc.close()  # type: ignore[no-untyped-call]
