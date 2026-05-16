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

import hashlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .schema import Chunk

if TYPE_CHECKING:
    from .schema import ParsedBlock, ParsedDocument


# ---------------------------------------------------------------------------
# Section detection heuristics
# ---------------------------------------------------------------------------

# Common HK IPO prospectus section markers (Chinese + English)
SECTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^第\s*[一二三四五六七八九十0-9]+\s*[节章部分]"), "numbered_section_zh"),
    (re.compile(r"^(摘要|SUMMARY)\s*$", re.IGNORECASE), "summary"),
    (re.compile(r"^(风险因素|RISK FACTORS?)\s*$", re.IGNORECASE), "risk_factors"),
    (re.compile(r"^(业务|BUSINESS)\s*$", re.IGNORECASE), "business"),
    (re.compile(r"^(财务资料|财务报表|FINANCIAL INFORMATION)\s*", re.IGNORECASE), "financials"),
    (re.compile(r"^(管理|MANAGEMENT)\s*$", re.IGNORECASE), "management"),
    (re.compile(r"^(主要股东|股本|SUBSTANTIAL SHAREHOLDERS?)\s*", re.IGNORECASE), "shareholders"),
    (re.compile(r"^(基石投资者|CORNERSTONE INVESTORS?)\s*", re.IGNORECASE), "cornerstone"),
    (re.compile(r"^(发售|未来计划|FUTURE PLANS AND USE OF PROCEEDS)\s*", re.IGNORECASE), "use_of_proceeds"),
    (re.compile(r"^(中国监管|监管概要|REGULATORY OVERVIEW)\s*", re.IGNORECASE), "regulatory"),
    (re.compile(r"^(附录|附件|APPENDIX)\s*", re.IGNORECASE), "appendix"),
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
    raw = f"{prospectus_id}|{char_offset}|{length}".encode()
    return hashlib.sha256(raw).hexdigest()[:32]


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
                chunk_id=_make_chunk_id(
                    document.prospectus_id, first.char_offset, len(text)
                ),
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
                    chunk_id=_make_chunk_id(
                        document.prospectus_id, block.char_offset, block_len
                    ),
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
        if (
            buffer
            and block.section != buffer[-1].section
            and buffer_len >= cfg.min_chars
        ):
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
