"""Qdrant async client wrapper per PROJECT_SPEC.md §3.5.

Per-prospectus collection isolation: each ``prospectus_id`` gets its own
Qdrant collection so re-indexing one prospectus doesn't disturb others
and so retrieval scope is naturally bounded.

Metadata stored alongside each vector:
- prospectus_id, chunk_id (sha256), page, section, char_offset, text
- type (text | table), pages (for multi-page chunks)

Phase 3 lands the create / upsert / search APIs. Phase 3.1 adds
hybrid search (BM25 sparse vectors) once Qdrant cluster is in production
mode (sparse vectors need >=v1.10 + collection config flag).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qm

from ..common.logging import get_logger
from ..common.settings import get_settings
from .embeddings import EmbeddingProvider
from .schema import Chunk

log = get_logger(__name__)


@dataclass
class SearchHit:
    """One retrieval result."""

    chunk_id: str
    prospectus_id: str
    page: int
    section: str | None
    char_offset: int
    text: str
    score: float
    metadata: dict[str, Any]


class ProspectusVectorStore:
    """Qdrant wrapper scoped to one prospectus.

    Lifecycle:
        store = ProspectusVectorStore("PROSP-123", provider=get_embedding_provider())
        await store.ensure_collection()
        await store.upsert_chunks(chunks)
        hits = await store.search("revenue 2024", top_k=5)
    """

    def __init__(
        self,
        prospectus_id: str,
        *,
        provider: EmbeddingProvider,
        client: AsyncQdrantClient | None = None,
    ) -> None:
        self.prospectus_id = prospectus_id
        self.provider = provider
        settings = get_settings()
        self._client = client or AsyncQdrantClient(
            url=settings.qdrant.url,
            api_key=(
                settings.qdrant.api_key.get_secret_value()
                if settings.qdrant.api_key
                else None
            ),
        )
        # Collection naming: lowercase alphanumeric + underscores only
        safe_id = "".join(c if c.isalnum() else "_" for c in prospectus_id.lower())
        self.collection_name = f"prospectus_{safe_id}"

    # ------------------------------------------------------------------ admin

    async def ensure_collection(self) -> None:
        """Create the collection if it doesn't exist."""
        existing = await self._client.collection_exists(self.collection_name)
        if existing:
            return
        await self._client.create_collection(
            collection_name=self.collection_name,
            vectors_config=qm.VectorParams(
                size=self.provider.dim,
                distance=qm.Distance.COSINE,
            ),
        )
        log.info(
            "qdrant_collection_created",
            collection=self.collection_name,
            dim=self.provider.dim,
        )

    async def delete_collection(self) -> None:
        await self._client.delete_collection(self.collection_name)

    async def close(self) -> None:
        await self._client.close()

    # ------------------------------------------------------------------ write

    async def upsert_chunks(self, chunks: list[Chunk]) -> int:
        """Embed and upsert one prospectus's chunks. Idempotent (uses chunk_id as point id)."""
        if not chunks:
            return 0
        await self.ensure_collection()
        texts = [c.text for c in chunks]
        vectors = await self.provider.embed(texts)
        points = [
            qm.PointStruct(
                id=_chunk_id_to_point_id(c.chunk_id),
                vector=vec,
                payload={
                    "chunk_id": c.chunk_id,
                    "prospectus_id": c.prospectus_id,
                    "page": c.page,
                    "section": c.section,
                    "char_offset": c.char_offset,
                    "text": c.text,
                    **c.metadata,
                },
            )
            for c, vec in zip(chunks, vectors, strict=True)
        ]
        await self._client.upsert(collection_name=self.collection_name, points=points)
        log.info("qdrant_upserted", collection=self.collection_name, count=len(points))
        return len(points)

    # ------------------------------------------------------------------ read

    async def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        section_filter: str | None = None,
    ) -> list[SearchHit]:
        """Vector search. Optional section filter narrows to one section."""
        await self.ensure_collection()
        query_vec = await self.provider.embed_one(query)
        qfilter: qm.Filter | None = None
        if section_filter is not None:
            qfilter = qm.Filter(
                must=[qm.FieldCondition(key="section", match=qm.MatchValue(value=section_filter))]
            )
        response = await self._client.query_points(
            collection_name=self.collection_name,
            query=query_vec,
            query_filter=qfilter,
            limit=top_k,
            with_payload=True,
        )
        hits: list[SearchHit] = []
        for r in response.points:
            p = r.payload or {}
            hits.append(
                SearchHit(
                    chunk_id=p.get("chunk_id", str(r.id)),
                    prospectus_id=p.get("prospectus_id", self.prospectus_id),
                    page=int(p.get("page", 0)),
                    section=p.get("section"),
                    char_offset=int(p.get("char_offset", 0)),
                    text=p.get("text", ""),
                    score=float(r.score),
                    metadata={
                        k: v
                        for k, v in p.items()
                        if k
                        not in {"chunk_id", "prospectus_id", "page", "section", "char_offset", "text"}
                    },
                )
            )
        return hits

    async def count(self) -> int:
        info = await self._client.count(
            collection_name=self.collection_name, exact=True
        )
        return int(info.count)


def _chunk_id_to_point_id(chunk_id: str) -> int:
    """Map sha256-prefix hex to a deterministic uint64 point id Qdrant accepts."""
    return int(chunk_id[:16], 16)


__all__ = ("ProspectusVectorStore", "SearchHit")
