"""Hybrid retriever per PROJECT_SPEC.md §3.5.

Combines:
1. Dense vector search (via ProspectusVectorStore)
2. Lexical BM25 over the same chunks (process-local index)

Results are fused with Reciprocal Rank Fusion (RRF) so a hit doesn't need
to be top-ranked in both lanes to surface.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from .vector_store import ProspectusVectorStore, SearchHit


@dataclass
class HybridResult:
    """One result from the hybrid retriever, with provenance per lane."""

    hit: SearchHit
    fused_score: float
    vector_rank: int | None
    bm25_rank: int | None


class HybridRetriever:
    """Vector + BM25 hybrid retrieval with RRF fusion."""

    def __init__(self, store: ProspectusVectorStore, *, rrf_k: int = 60) -> None:
        self.store = store
        self.rrf_k = rrf_k
        self._bm25: _BM25Index | None = None

    async def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        section_filter: str | None = None,
        oversample: int = 4,
    ) -> list[HybridResult]:
        """Return the top-k chunks combining vector + BM25 lanes."""
        candidates = top_k * oversample

        vector_hits = await self.store.search(
            query, top_k=candidates, section_filter=section_filter
        )

        if self._bm25 is None:
            self._bm25 = await self._build_bm25_from_collection()
        bm25_hits = self._bm25.search(query, top_k=candidates, section_filter=section_filter)

        # RRF fusion
        scores: dict[str, float] = defaultdict(float)
        vector_rank_by_id: dict[str, int] = {}
        bm25_rank_by_id: dict[str, int] = {}
        hits_by_id: dict[str, SearchHit] = {}
        for rank, h in enumerate(vector_hits, start=1):
            scores[h.chunk_id] += 1.0 / (self.rrf_k + rank)
            vector_rank_by_id[h.chunk_id] = rank
            hits_by_id[h.chunk_id] = h
        for rank, h in enumerate(bm25_hits, start=1):
            scores[h.chunk_id] += 1.0 / (self.rrf_k + rank)
            bm25_rank_by_id[h.chunk_id] = rank
            hits_by_id.setdefault(h.chunk_id, h)

        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
        return [
            HybridResult(
                hit=hits_by_id[chunk_id],
                fused_score=score,
                vector_rank=vector_rank_by_id.get(chunk_id),
                bm25_rank=bm25_rank_by_id.get(chunk_id),
            )
            for chunk_id, score in ranked
        ]

    async def _build_bm25_from_collection(self) -> _BM25Index:
        """Scroll the entire Qdrant collection to build a process-local BM25 index."""
        all_chunks: list[SearchHit] = []
        offset: Any = None
        batch = 500
        while True:
            points, offset = await self.store._client.scroll(
                collection_name=self.store.collection_name,
                limit=batch,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for p in points:
                payload = p.payload or {}
                all_chunks.append(
                    SearchHit(
                        chunk_id=payload.get("chunk_id", str(p.id)),
                        prospectus_id=payload.get("prospectus_id", self.store.prospectus_id),
                        page=int(payload.get("page", 0)),
                        section=payload.get("section"),
                        char_offset=int(payload.get("char_offset", 0)),
                        text=payload.get("text", ""),
                        score=0.0,
                        metadata={},
                    )
                )
            if offset is None:
                break
        return _BM25Index(all_chunks)


# ---------------------------------------------------------------------------
# Minimal BM25 (Okapi)
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[\w一-鿿]+")  # ASCII word chars + CJK


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


class _BM25Index:
    """In-memory Okapi BM25 over a fixed corpus."""

    def __init__(self, chunks: list[SearchHit], *, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.chunks = chunks
        self._tokens = [_tokenize(c.text) for c in chunks]
        self._doc_len = [len(toks) for toks in self._tokens]
        self._avgdl = (sum(self._doc_len) / len(self._doc_len)) if self._doc_len else 0.0
        df: Counter[str] = Counter()
        for toks in self._tokens:
            df.update(set(toks))
        n_docs = len(self._tokens) or 1
        self._idf = {
            tok: math.log((n_docs - cnt + 0.5) / (cnt + 0.5) + 1) for tok, cnt in df.items()
        }

    def search(
        self,
        query: str,
        *,
        top_k: int,
        section_filter: str | None,
    ) -> list[SearchHit]:
        q_toks = _tokenize(query)
        if not q_toks:
            return []
        scores: list[tuple[float, int]] = []
        for i, doc_toks in enumerate(self._tokens):
            chunk = self.chunks[i]
            if section_filter is not None and chunk.section != section_filter:
                continue
            dl = self._doc_len[i] or 1
            tf = Counter(doc_toks)
            score = 0.0
            for q in q_toks:
                if q not in tf:
                    continue
                idf = self._idf.get(q, 0.0)
                denom = tf[q] + self.k1 * (1 - self.b + self.b * dl / (self._avgdl or 1))
                score += idf * (tf[q] * (self.k1 + 1)) / (denom or 1)
            if score > 0:
                scores.append((score, i))
        scores.sort(reverse=True)
        return [self._copy_with_score(self.chunks[i], s) for s, i in scores[:top_k]]

    @staticmethod
    def _copy_with_score(hit: SearchHit, score: float) -> SearchHit:
        return SearchHit(
            chunk_id=hit.chunk_id,
            prospectus_id=hit.prospectus_id,
            page=hit.page,
            section=hit.section,
            char_offset=hit.char_offset,
            text=hit.text,
            score=score,
            metadata=hit.metadata,
        )


__all__ = ("HybridResult", "HybridRetriever")
