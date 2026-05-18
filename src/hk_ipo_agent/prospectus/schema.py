"""Prospectus pipeline schemas.

Re-exports the canonical Pydantic models defined in ``common.schemas`` and
adds the parser-layer types (``ParsedBlock``, ``ParsedTable``,
``ParsedDocument``, ``ParserBackend``, ``Chunk``) that don't belong in the
cross-phase contract surface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ..common.schemas import (
    Ch18CQualification,
    Citation,
    CustomerConcentration,
    FinancialSnapshot,
    ProspectusExtraction,
    RiskFactor,
    ShareholderEntry,
)

__all__ = (
    "Ch18CQualification",
    "Chunk",
    "Citation",
    "CustomerConcentration",
    "FinancialSnapshot",
    "ParsedBlock",
    "ParsedDocument",
    "ParsedTable",
    "ParserBackend",
    "ProspectusExtraction",
    "RiskFactor",
    "ShareholderEntry",
)


class ParserBackend(StrEnum):
    """Which PDF parser produced a ParsedDocument."""

    LLAMAPARSE = "llamaparse"
    PYMUPDF = "pymupdf"
    PYMUPDF_PLUS_CAMELOT = "pymupdf+camelot"


@dataclass
class ParsedBlock:
    """One block of text from a PDF page.

    Pages can have multiple blocks (paragraphs / headers / list items).
    ``char_offset`` is the index of this block's first character within
    ``ParsedDocument.full_text`` — used to resolve citations back to the
    exact span.
    """

    page: int
    text: str
    char_offset: int
    block_type: str = "paragraph"  # paragraph / heading / list / footnote / caption
    bbox: tuple[float, float, float, float] | None = None  # PyMuPDF (x0, y0, x1, y1)
    section: str | None = None  # filled in by chunker


@dataclass
class ParsedTable:
    """One extracted table.

    For LlamaParse / Camelot, ``rows`` is a list of cell-row arrays; we keep
    ``markdown`` for direct embedding so the chunker / extractor can use it
    as plain text when needed.
    """

    page: int
    rows: list[list[str]]
    markdown: str
    char_offset: int
    bbox: tuple[float, float, float, float] | None = None
    section: str | None = None
    caption: str | None = None


@dataclass
class ParsedDocument:
    """The raw output of a parser backend, before chunking + extraction."""

    prospectus_id: str
    backend: ParserBackend
    page_count: int
    full_text: str
    blocks: list[ParsedBlock] = field(default_factory=list)
    tables: list[ParsedTable] = field(default_factory=list)
    figures: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Chunk:
    """A chunk emitted by the chunker. The unit of embedding and retrieval.

    ``chunk_id`` is a deterministic UUID5 string (R5-3: ``uuid5`` of
    prospectus_id + char_offset + length) so re-runs yield identical IDs
    and Qdrant point upserts stay idempotent.
    """

    chunk_id: str
    prospectus_id: str
    page: int
    section: str | None
    char_offset: int
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def char_length(self) -> int:
        return len(self.text)
