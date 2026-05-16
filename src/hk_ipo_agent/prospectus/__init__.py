"""Prospectus pipeline per PROJECT_SPEC.md §3.5: parse -> chunk -> embed -> store -> extract / QA."""

from .chunker import ChunkConfig, chunk_document, detect_section
from .embeddings import (
    BGEEmbeddings,
    EmbeddingProvider,
    HashEmbeddings,
    VoyageEmbeddings,
    get_embedding_provider,
)
from .extractor import ExtractionConfig, ExtractionResult, ProspectusExtractor
from .parser import ParserConfig, parse_prospectus
from .qa import Answer, ProspectusQA
from .retriever import HybridResult, HybridRetriever
from .schema import (
    Chunk,
    ParsedBlock,
    ParsedDocument,
    ParsedTable,
    ParserBackend,
    ProspectusExtraction,
)
from .validators import ValidationIssue, validate
from .vector_store import ProspectusVectorStore, SearchHit

__all__ = (
    "Answer",
    "BGEEmbeddings",
    "Chunk",
    "ChunkConfig",
    "EmbeddingProvider",
    "ExtractionConfig",
    "ExtractionResult",
    "HashEmbeddings",
    "HybridResult",
    "HybridRetriever",
    "ParsedBlock",
    "ParsedDocument",
    "ParsedTable",
    "ParserBackend",
    "ParserConfig",
    "ProspectusExtraction",
    "ProspectusExtractor",
    "ProspectusQA",
    "ProspectusVectorStore",
    "SearchHit",
    "ValidationIssue",
    "VoyageEmbeddings",
    "chunk_document",
    "detect_section",
    "get_embedding_provider",
    "parse_prospectus",
    "validate",
)
