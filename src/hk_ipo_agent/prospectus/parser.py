"""PDF parser per PROJECT_SPEC.md §3.5 + ADR 0004.

Two backends:
1. **LlamaParse (primary)** — best at HK prospectus tables and structured
   layout. Requires LLAMA_CLOUD_API_KEY. Lazy-imported.
2. **PyMuPDF (fallback)** — always available (pip dependency). Catches the
   case where LlamaParse is unconfigured / quota-exhausted / down.

Both backends produce a ``ParsedDocument`` with the same shape so
downstream code (chunker / extractor) is backend-agnostic.

Block char_offset is the cumulative character position in ``full_text``,
which lets the citation system pinpoint quoted text without re-parsing.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..common.exceptions import MissingDependencyError, ParseError
from ..common.logging import get_logger
from ..common.settings import get_settings
from .schema import ParsedBlock, ParsedDocument, ParsedTable, ParserBackend

if TYPE_CHECKING:
    pass

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class ParserConfig:
    """Tunable parser parameters."""

    max_pages: int = 800
    prefer_llamaparse: bool = True
    min_block_chars: int = 5  # drop blocks shorter than this (page numbers etc.)


# ---------------------------------------------------------------------------
# Top-level entry
# ---------------------------------------------------------------------------


async def parse_prospectus(
    pdf_path: Path,
    *,
    prospectus_id: str,
    config: ParserConfig | None = None,
) -> ParsedDocument:
    """Parse one prospectus PDF, trying LlamaParse first then PyMuPDF.

    Raises:
        ParseError: if both backends fail.
    """
    if not pdf_path.exists():
        raise ParseError(f"PDF not found: {pdf_path}")
    cfg = config or ParserConfig()
    settings = get_settings()
    if settings.prospectus.parser_max_pages:
        cfg.max_pages = settings.prospectus.parser_max_pages

    if cfg.prefer_llamaparse:
        try:
            return await _parse_with_llamaparse(pdf_path, prospectus_id, cfg)
        except (MissingDependencyError, ParseError) as exc:
            log.warning(
                "llamaparse_unavailable_falling_back",
                pdf=str(pdf_path),
                reason=str(exc),
            )

    return await _parse_with_pymupdf(pdf_path, prospectus_id, cfg)


# ---------------------------------------------------------------------------
# LlamaParse backend (primary)
# ---------------------------------------------------------------------------


async def _parse_with_llamaparse(
    pdf_path: Path,
    prospectus_id: str,
    cfg: ParserConfig,
) -> ParsedDocument:
    """LlamaParse primary path. Requires LLAMA_CLOUD_API_KEY."""
    settings = get_settings()
    api_key = (
        settings.prospectus.llama_cloud_api_key.get_secret_value()
        if settings.prospectus.llama_cloud_api_key
        else None
    )
    if not api_key:
        raise MissingDependencyError(
            "LLAMA_CLOUD_API_KEY not configured; PyMuPDF fallback will be used.",
            install_hint="Set LLAMA_CLOUD_API_KEY in .env or env var",
        )

    try:
        from llama_parse import LlamaParse  # noqa: PLC0415
    except ImportError as exc:
        raise MissingDependencyError(
            "llama-parse package not installed. `uv sync --extra parse`",
        ) from exc

    log.info("llamaparse_starting", pdf=str(pdf_path), prospectus_id=prospectus_id)
    parser = LlamaParse(api_key=api_key, result_type="markdown", verbose=False)

    # LlamaParse SDK is sync; offload to thread.
    documents = await asyncio.to_thread(parser.load_data, str(pdf_path))

    full_text_parts: list[str] = []
    blocks: list[ParsedBlock] = []
    tables: list[ParsedTable] = []
    char_offset = 0
    page = 1

    for doc in documents:
        # LlamaParse returns one Document per page (or sometimes per section).
        text = getattr(doc, "text", "") or ""
        if not text.strip():
            continue
        # Heuristically split markdown into blocks (paragraphs separated by
        # blank lines) and tables (lines starting with |).
        for chunk in _split_markdown_blocks(text):
            if chunk["type"] == "table":
                table = ParsedTable(
                    page=page,
                    rows=chunk["rows"],
                    markdown=chunk["text"],
                    char_offset=char_offset,
                )
                tables.append(table)
            else:
                blocks.append(
                    ParsedBlock(
                        page=page,
                        text=chunk["text"],
                        char_offset=char_offset,
                        block_type=chunk["block_type"],
                    )
                )
            full_text_parts.append(chunk["text"])
            char_offset += len(chunk["text"]) + 2  # account for join
        page += 1

    full_text = "\n\n".join(full_text_parts)
    return ParsedDocument(
        prospectus_id=prospectus_id,
        backend=ParserBackend.LLAMAPARSE,
        page_count=page - 1,
        full_text=full_text,
        blocks=blocks,
        tables=tables,
        metadata={"source_path": str(pdf_path)},
    )


_TABLE_LINE_RE = re.compile(r"^\s*\|.+\|\s*$")
_HEADING_RE = re.compile(r"^#{1,6}\s+")


def _split_markdown_blocks(md: str) -> list[dict[str, Any]]:
    """Split a markdown blob into blocks + tables."""
    out: list[dict[str, Any]] = []
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if _TABLE_LINE_RE.match(line):
            # Consume the consecutive table lines
            start = i
            while i < len(lines) and (_TABLE_LINE_RE.match(lines[i]) or lines[i].strip() == ""):
                i += 1
            table_text = "\n".join(lines[start:i]).strip()
            rows = [
                [cell.strip() for cell in row.strip("|").split("|")]
                for row in table_text.splitlines()
                if row.strip().startswith("|")
            ]
            out.append({"type": "table", "text": table_text, "rows": rows})
            continue
        if line.strip() == "":
            i += 1
            continue
        # Collect a paragraph (or heading) until blank line / table
        start = i
        while i < len(lines) and lines[i].strip() != "" and not _TABLE_LINE_RE.match(lines[i]):
            i += 1
        para = "\n".join(lines[start:i]).strip()
        block_type = "heading" if _HEADING_RE.match(para) else "paragraph"
        out.append({"type": "text", "text": para, "block_type": block_type})
    return out


# ---------------------------------------------------------------------------
# PyMuPDF backend (fallback)
# ---------------------------------------------------------------------------


async def _parse_with_pymupdf(
    pdf_path: Path,
    prospectus_id: str,
    cfg: ParserConfig,
) -> ParsedDocument:
    """PyMuPDF fallback. Always available (pip dependency)."""
    try:
        import pymupdf  # noqa: PLC0415
    except ImportError as exc:
        raise MissingDependencyError(
            "pymupdf package not installed. It should be in pyproject.toml core deps.",
        ) from exc

    log.info("pymupdf_starting", pdf=str(pdf_path), prospectus_id=prospectus_id)
    pymupdf_any: Any = pymupdf

    def _do_parse() -> ParsedDocument:
        doc: Any = pymupdf_any.open(str(pdf_path))
        try:
            page_count = min(doc.page_count, cfg.max_pages)
            full_text_parts: list[str] = []
            blocks: list[ParsedBlock] = []
            char_offset = 0

            for page_idx in range(page_count):
                page = doc.load_page(page_idx)
                # get_text("blocks") returns (x0, y0, x1, y1, text, block_no, block_type)
                raw_blocks = page.get_text("blocks")
                # Order top-to-bottom, left-to-right (PyMuPDF gives them mostly
                # in reading order but sort defensively)
                raw_blocks.sort(key=lambda b: (round(b[1], 1), round(b[0], 1)))
                for block in raw_blocks:
                    text = (block[4] or "").strip()
                    if len(text) < cfg.min_block_chars:
                        continue
                    bbox = (
                        float(block[0]),
                        float(block[1]),
                        float(block[2]),
                        float(block[3]),
                    )
                    blocks.append(
                        ParsedBlock(
                            page=page_idx + 1,
                            text=text,
                            char_offset=char_offset,
                            block_type="paragraph",
                            bbox=bbox,
                        )
                    )
                    full_text_parts.append(text)
                    char_offset += len(text) + 2

            full_text = "\n\n".join(full_text_parts)
            return ParsedDocument(
                prospectus_id=prospectus_id,
                backend=ParserBackend.PYMUPDF,
                page_count=page_count,
                full_text=full_text,
                blocks=blocks,
                metadata={"source_path": str(pdf_path)},
            )
        finally:
            doc.close()

    return await asyncio.to_thread(_do_parse)


__all__ = ("ParserConfig", "parse_prospectus")
