"""Section-aware prospectus chunker.

Strategy:
1. Detect section headers (Chinese + English IPO conventions) inside the
   block stream and tag every block with the most recent section.
2. Pack blocks into ~1500-token chunks, never splitting a single block;
   prefer breaking at section / paragraph boundaries.
3. Tables are emitted as standalone chunks (their markdown form), so the
   retriever can return them whole.
4. Every chunk carries the ``char_offset`` of its first block, enabling
   precise citation back to ``ParsedDocument.full_text``.

The chunker is deterministic — re-running on the same ParsedDocument
yields identical chunk IDs, which matters for idempotent Qdrant upserts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID, uuid5

from .schema import Chunk

if TYPE_CHECKING:
    from .schema import ParsedBlock, ParsedDocument


# R5-3: deterministic namespace for chunk_id generation. Distinct from the
# prospectus-document namespace in ``pipelines/pdf_to_snapshot.py`` so the
# two ID spaces can never overlap.
_NAMESPACE_CHUNK = UUID("6ba7b811-9dad-11d1-80b4-00c04fd430ca")


# ---------------------------------------------------------------------------
# Section detection heuristics
# ---------------------------------------------------------------------------

# Common HK IPO prospectus section markers (Chinese + English)
SECTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^第\s*[一二三四五六七八九十0-9]+\s*[节章部分節]"), "numbered_section_zh"),
    (re.compile(r"^(摘要|概要|SUMMARY)\s*$", re.IGNORECASE), "summary"),
    (re.compile(r"^(风险因素|風險因素|RISK FACTORS?)\s*$", re.IGNORECASE), "risk_factors"),
    (re.compile(r"^(业务|業務|BUSINESS)\s*$", re.IGNORECASE), "business"),
    (
        re.compile(
            r"^(财务资料|財務資料|财务报表|財務報表|FINANCIAL INFORMATION)\s*", re.IGNORECASE
        ),
        "financials",
    ),
    (re.compile(r"^(管理|管理層|MANAGEMENT)\s*$", re.IGNORECASE), "management"),
    (
        re.compile(
            r"^(主要股东|主要股東|股本|股本及購股權|SUBSTANTIAL SHAREHOLDERS?)\s*", re.IGNORECASE
        ),
        "shareholders",
    ),
    (
        re.compile(r"^(基石投资者|基石投資者|CORNERSTONE INVESTORS?)\s*", re.IGNORECASE),
        "cornerstone",
    ),
    (
        re.compile(
            r"^(未来计划|未來計劃|所得款項用途|FUTURE PLANS AND USE OF PROCEEDS)\s*", re.IGNORECASE
        ),
        "use_of_proceeds",
    ),
    (
        re.compile(
            r"^(發售結構|发售结构|STRUCTURE OF THE .*(OFFERING|PLACEMENT))\s*", re.IGNORECASE
        ),
        "use_of_proceeds",
    ),
    (
        re.compile(r"^(中国监管|中國監管|监管概要|監管概要|REGULATORY OVERVIEW)\s*", re.IGNORECASE),
        "regulatory",
    ),
    (re.compile(r"^(附录|附錄|附件|APPENDIX)\s*", re.IGNORECASE), "appendix"),
    (re.compile(r"^(歷史及發展|历史及发展|HISTORY AND DEVELOPMENT)\s*", re.IGNORECASE), "business"),
    (re.compile(r"^(行業概覽|行业概览|INDUSTRY OVERVIEW)\s*", re.IGNORECASE), "business"),
)


def detect_section(text: str) -> str | None:
    """Return a normalized section key if ``text`` looks like a section heading."""
    text = text.strip()
    if not text or len(text) > 100:  # long lines are not headings
        return None
    for pattern, name in SECTION_PATTERNS:
        if pattern.match(text):
            return name
    return None


# ---------------------------------------------------------------------------
# Chunker
# ---------------------------------------------------------------------------


@dataclass
class ChunkConfig:
    """Tunable chunker parameters. Phase 8 calibration may override."""

    target_chars: int = 1500
    max_chars: int = 2500
    min_chars: int = 200
    overlap_chars: int = 100


def _make_chunk_id(prospectus_id: str, char_offset: int, length: int) -> str:
    """R5-3: deterministic UUID5 string, accepted by Qdrant as a point id verbatim.

    Pre-R5-3 returned ``sha256(...).hexdigest()[:32]`` — a 32-char hex string
    that wasn't a valid UUID. ``vector_store`` then coerced it via
    ``int(chunk_id[:16], 16)`` (dropping 192 bits) so the Qdrant point id and
    the ``Citation.chunk_id`` referred to "the same chunk" by two different
    keys. Post-R5-3 the chunk_id IS a UUID5 string and vector_store passes
    it through unchanged — single canonical identifier end-to-end.
    """
    return str(uuid5(_NAMESPACE_CHUNK, f"{prospectus_id}|{char_offset}|{length}"))


def _tag_blocks_with_sections(blocks: list[ParsedBlock]) -> None:
    """Walk the block stream and stamp each block with the current section."""
    current_section: str | None = None
    for block in blocks:
        detected = detect_section(block.text)
        if detected is not None:
            current_section = detected
        block.section = current_section


def chunk_document(
    document: ParsedDocument,
    *,
    config: ChunkConfig | None = None,
) -> list[Chunk]:
    """Section-aware chunking of one parsed document."""
    cfg = config or ChunkConfig()
    _tag_blocks_with_sections(document.blocks)

    chunks: list[Chunk] = []

    # Tables get one chunk each (don't split them).
    for table in document.tables:
        chunks.append(
            Chunk(
                chunk_id=_make_chunk_id(
                    document.prospectus_id, table.char_offset, len(table.markdown)
                ),
                prospectus_id=document.prospectus_id,
                page=table.page,
                section=table.section,
                char_offset=table.char_offset,
                text=table.markdown,
                metadata={
                    "type": "table",
                    "rows": len(table.rows),
                    "caption": table.caption,
                    "bbox": table.bbox,
                },
            )
        )

    # Blocks get packed into chunks of approximately target_chars.
    buffer: list[ParsedBlock] = []
    buffer_len = 0

    def flush() -> None:
        nonlocal buffer, buffer_len
        if not buffer:
            return
        # Join texts with newlines so the chunk preserves block separation.
        text = "\n\n".join(b.text for b in buffer)
        first = buffer[0]
        # Use the first block's section (most chunks span one section).
        section = first.section
        chunks.append(
            Chunk(
                chunk_id=_make_chunk_id(document.prospectus_id, first.char_offset, len(text)),
                prospectus_id=document.prospectus_id,
                page=first.page,
                section=section,
                char_offset=first.char_offset,
                text=text,
                metadata={
                    "type": "text",
                    "block_count": len(buffer),
                    "pages": sorted({b.page for b in buffer}),
                },
            )
        )
        buffer = []
        buffer_len = 0

    for block in document.blocks:
        block_len = len(block.text)

        # If a single block is huge, emit it as its own oversized chunk.
        if block_len >= cfg.max_chars:
            flush()
            chunks.append(
                Chunk(
                    chunk_id=_make_chunk_id(document.prospectus_id, block.char_offset, block_len),
                    prospectus_id=document.prospectus_id,
                    page=block.page,
                    section=block.section,
                    char_offset=block.char_offset,
                    text=block.text,
                    metadata={"type": "text", "oversized": True},
                )
            )
            continue

        # Flush on section boundary if the buffer has accumulated enough.
        if buffer and block.section != buffer[-1].section and buffer_len >= cfg.min_chars:
            flush()

        # Flush when approaching target.
        if buffer_len + block_len > cfg.target_chars and buffer_len >= cfg.min_chars:
            flush()

        buffer.append(block)
        buffer_len += block_len + 2  # +2 for the join separator

    flush()
    return chunks


__all__ = (
    "SECTION_PATTERNS",
    "ChunkConfig",
    "chunk_document",
    "detect_section",
)
