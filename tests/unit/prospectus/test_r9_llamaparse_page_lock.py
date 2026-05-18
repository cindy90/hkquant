"""R9-6 — additional LlamaParse page-number lockdowns.

Complements the R1-2 regression test
(``test_llamaparse_page_numbers_skip_empty_pages_correctly``) with edge
cases that pin the page-tracking contract end-to-end:

  * Tables (``ParsedTable``) get the correct page, same as blocks.
  * A single-page document → all output is page 1 (no off-by-zero).
  * Trailing empty pages don't shift the final block's page number.
  * Pages preserve their 1-indexed numbering (no zero-indexed leak).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from hk_ipo_agent.prospectus.parser import ParserConfig


@pytest.mark.asyncio
async def test_llamaparse_table_gets_correct_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R9-6 — table chunks on page 3 are tagged page=3, not page=1 (or N+1)."""
    contents = [
        "# Page 1\n\nintro text",
        "# Page 2\n\nsection body",
        # Page 3 has a markdown table.
        "# Page 3\n\n| col1 | col2 |\n|------|------|\n| a | b |\n| c | d |",
    ]
    fake_docs = [MagicMock(text=c) for c in contents]
    fake_parser = MagicMock()
    fake_parser.load_data.return_value = fake_docs

    fake_mod = MagicMock()
    fake_mod.LlamaParse = MagicMock(return_value=fake_parser)
    monkeypatch.setitem(sys.modules, "llama_parse", fake_mod)

    from hk_ipo_agent.prospectus import parser as parser_module

    fake_settings = MagicMock()
    fake_settings.prospectus.llama_cloud_api_key.get_secret_value.return_value = "fake-key"
    monkeypatch.setattr(parser_module, "get_settings", lambda: fake_settings)

    pdf = tmp_path / "fake.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    result = await parser_module._parse_with_llamaparse(
        pdf, "P-R9-6", ParserConfig(prefer_llamaparse=True)
    )
    # The table from page 3 must be tagged page=3.
    assert result.tables, "R9-6: expected at least one table extracted"
    for tbl in result.tables:
        assert tbl.page == 3, f"R9-6: table on page 3 got tagged page={tbl.page}; expected 3"


@pytest.mark.asyncio
async def test_llamaparse_single_page_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R9-6 — 1-page PDF → all blocks on page 1 (no off-by-zero)."""
    fake_docs = [MagicMock(text="# Single page\n\nOnly content here.")]
    fake_parser = MagicMock()
    fake_parser.load_data.return_value = fake_docs

    fake_mod = MagicMock()
    fake_mod.LlamaParse = MagicMock(return_value=fake_parser)
    monkeypatch.setitem(sys.modules, "llama_parse", fake_mod)

    from hk_ipo_agent.prospectus import parser as parser_module

    fake_settings = MagicMock()
    fake_settings.prospectus.llama_cloud_api_key.get_secret_value.return_value = "fake-key"
    monkeypatch.setattr(parser_module, "get_settings", lambda: fake_settings)

    pdf = tmp_path / "fake.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    result = await parser_module._parse_with_llamaparse(
        pdf, "P-R9-6", ParserConfig(prefer_llamaparse=True)
    )
    assert result.blocks, "R9-6: single-page doc must produce ≥1 block"
    for blk in result.blocks:
        assert blk.page == 1, f"R9-6: single-page block got page={blk.page}; expected 1 (1-indexed)"


@pytest.mark.asyncio
async def test_llamaparse_trailing_empty_pages_do_not_shift_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R9-6 — empty pages at the END don't cause off-by-one on prior blocks."""
    contents = [
        "# Page 1\n\nfirst content",
        "# Page 2\n\nsecond content",
        "",  # empty page 3
        "",  # empty page 4
    ]
    fake_docs = [MagicMock(text=c) for c in contents]
    fake_parser = MagicMock()
    fake_parser.load_data.return_value = fake_docs

    fake_mod = MagicMock()
    fake_mod.LlamaParse = MagicMock(return_value=fake_parser)
    monkeypatch.setitem(sys.modules, "llama_parse", fake_mod)

    from hk_ipo_agent.prospectus import parser as parser_module

    fake_settings = MagicMock()
    fake_settings.prospectus.llama_cloud_api_key.get_secret_value.return_value = "fake-key"
    monkeypatch.setattr(parser_module, "get_settings", lambda: fake_settings)

    pdf = tmp_path / "fake.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    result = await parser_module._parse_with_llamaparse(
        pdf, "P-R9-6", ParserConfig(prefer_llamaparse=True)
    )
    pages = sorted({b.page for b in result.blocks})
    # Only pages 1 and 2 should appear. NOT page 3 / 4 (empty) and NOT
    # any off-by-one trail that shifts page 2 → 3.
    assert pages == [1, 2], (
        f"R9-6: trailing empty pages should not appear nor shift others; got pages={pages}"
    )


@pytest.mark.asyncio
async def test_llamaparse_pages_are_one_indexed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R9-6 — no block has page=0. CHECKPOINT_DAYS and citation pages
    are 1-indexed throughout the spec; a 0-page would surface as a bug.
    """
    fake_docs = [MagicMock(text=f"# Page {i + 1}\n\nbody {i}") for i in range(3)]
    fake_parser = MagicMock()
    fake_parser.load_data.return_value = fake_docs

    fake_mod = MagicMock()
    fake_mod.LlamaParse = MagicMock(return_value=fake_parser)
    monkeypatch.setitem(sys.modules, "llama_parse", fake_mod)

    from hk_ipo_agent.prospectus import parser as parser_module

    fake_settings = MagicMock()
    fake_settings.prospectus.llama_cloud_api_key.get_secret_value.return_value = "fake-key"
    monkeypatch.setattr(parser_module, "get_settings", lambda: fake_settings)

    pdf = tmp_path / "fake.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    result = await parser_module._parse_with_llamaparse(
        pdf, "P-R9-6", ParserConfig(prefer_llamaparse=True)
    )
    for blk in result.blocks:
        assert blk.page >= 1, (
            f"R9-6: block carries non-1-indexed page={blk.page}; spec is 1-indexed"
        )
