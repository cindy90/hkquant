"""Embedding providers per PROJECT_SPEC.md §1.

Three providers, all behind the same async interface:

1. **HashEmbeddings (default fallback)** — deterministic SHA-256 based
   pseudo-embeddings. Zero deps, instant, suitable for CI / smoke tests
   only (no semantic meaning). Used when no real provider is configured.
2. **BGEEmbeddings (local)** — `sentence-transformers` + BAAI/bge-large-zh-v1.5.
   ~1024-dim. Requires the ``embeddings-local`` extras and a ~2GB model
   download on first use.
3. **VoyageEmbeddings (cloud)** — Voyage-3 API. Requires
   ``embeddings-cloud`` extras + VOYAGE_API_KEY.

Provider selection is driven by ``settings.embedding.provider`` and falls
back to HashEmbeddings if the chosen backend can't load.
"""

from __future__ import annotations

import asyncio
import hashlib
import struct
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from ..common.exceptions import MissingDependencyError
from ..common.logging import get_logger
from ..common.settings import get_settings

log = get_logger(__name__)


class EmbeddingProvider(ABC):
    """All providers must expose ``dim`` and ``embed`` (batch async)."""

    dim: ClassVar[int]
    name: ClassVar[str]

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text."""

    async def embed_one(self, text: str) -> list[float]:
        result = await self.embed([text])
        return result[0]


# ---------------------------------------------------------------------------
# Hash fallback (deterministic, no deps)
# ---------------------------------------------------------------------------


class HashEmbeddings(EmbeddingProvider):
    """Deterministic SHA-256 based pseudo-embeddings (CI / smoke tests only).

    NOT semantically meaningful — re-runs are stable but similar texts do
    NOT cluster in the embedding space. Use only when no real provider is
    available.
    """

    dim: ClassVar[int] = 256
    name: ClassVar[str] = "hash-256"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._hash_vec(t) for t in texts]

    def _hash_vec(self, text: str) -> list[float]:
        # Hash the text, then unpack the digest into float32 values normalized
        # to [-1, 1] for stability.
        h = hashlib.sha256(text.encode("utf-8")).digest()
        # Repeat the digest to fill ``dim`` floats (4 bytes per float)
        needed_bytes = self.dim * 4
        repeated = (h * ((needed_bytes // len(h)) + 1))[:needed_bytes]
        ints = struct.unpack(f"{self.dim}i", repeated)
        # Normalize to roughly [-1, 1]
        vec = [v / (2**31) for v in ints]
        # L2-normalize
        norm = sum(x * x for x in vec) ** 0.5 or 1.0
        return [x / norm for x in vec]


# ---------------------------------------------------------------------------
# BGE (local, sentence-transformers)
# ---------------------------------------------------------------------------


class BGEEmbeddings(EmbeddingProvider):
    """BAAI/bge-large-zh-v1.5 via sentence-transformers (local inference)."""

    dim: ClassVar[int] = 1024
    name: ClassVar[str] = "bge-large-zh-v1.5"

    def __init__(self, model_path: str | None = None) -> None:
        try:
            from sentence_transformers import SentenceTransformer  # noqa: PLC0415
        except ImportError as exc:
            raise MissingDependencyError(
                "sentence-transformers not installed. "
                "`uv sync --extra embeddings-local`",
            ) from exc
        path = model_path or get_settings().embedding.bge_model_path
        log.info("loading_bge_model", path=path)
        self._model = SentenceTransformer(path)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # SentenceTransformer.encode is sync; offload to thread.
        def _do_encode() -> Any:
            return self._model.encode(
                texts,
                normalize_embeddings=True,
                show_progress_bar=False,
            )

        result = await asyncio.to_thread(_do_encode)
        return [list(map(float, vec)) for vec in result]


# ---------------------------------------------------------------------------
# Voyage (cloud)
# ---------------------------------------------------------------------------


class VoyageEmbeddings(EmbeddingProvider):
    """Voyage-3 API embeddings."""

    dim: ClassVar[int] = 1024
    name: ClassVar[str] = "voyage-3"

    def __init__(self, api_key: str | None = None) -> None:
        try:
            import voyageai  # noqa: PLC0415
        except ImportError as exc:
            raise MissingDependencyError(
                "voyageai not installed. `uv sync --extra embeddings-cloud`",
            ) from exc
        settings = get_settings()
        resolved = api_key or (
            settings.embedding.voyage_api_key.get_secret_value()
            if settings.embedding.voyage_api_key
            else None
        )
        if not resolved:
            raise MissingDependencyError("VOYAGE_API_KEY not configured")
        self._client = voyageai.AsyncClient(api_key=resolved)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        response = await self._client.embed(texts, model="voyage-3", input_type="document")
        return [list(map(float, e)) for e in response.embeddings]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_embedding_provider(name: str | None = None) -> EmbeddingProvider:
    """Build the configured provider, falling back to HashEmbeddings on failure.

    Selection precedence:
    1. ``name`` arg if given
    2. ``settings.embedding.provider``
    3. ``"hash"`` (fallback)
    """
    chosen = (name or get_settings().embedding.provider).lower()
    try:
        if chosen in {"bge", "local"}:
            return BGEEmbeddings()
        if chosen == "voyage":
            return VoyageEmbeddings()
        if chosen == "hash":
            return HashEmbeddings()
    except MissingDependencyError as exc:
        log.warning(
            "embedding_provider_unavailable_using_hash",
            requested=chosen,
            reason=str(exc),
        )
        return HashEmbeddings()
    log.warning("unknown_embedding_provider_using_hash", requested=chosen)
    return HashEmbeddings()


__all__ = (
    "BGEEmbeddings",
    "EmbeddingProvider",
    "HashEmbeddings",
    "VoyageEmbeddings",
    "get_embedding_provider",
)
