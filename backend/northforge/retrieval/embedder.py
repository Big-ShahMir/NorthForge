"""Embedding interface plus the provider-backed implementation.

Retrieval still ranks with PostgreSQL full-text search only (ADR-024);
nothing in the retrieval path calls ``Embedder.embed`` yet. Phase 4 adds
``ProviderEmbedder`` (NVIDIA ``nemotron-3-embed-1b`` through the model
router, 2048 dimensions) so hybrid ranking can be added later without
touching callers; ADR-027 records the evidence required first.
``NoopEmbedder`` remains as the loud placeholder for wiring that must not
embed.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from northforge.providers.types import (
    EmbeddingInputType,
    EmbeddingRequest,
    EmbeddingResponse,
    ModelRole,
)


@runtime_checkable
class Embedder(Protocol):
    """A text-embedding model: strings in, fixed-length vectors out."""

    @property
    def dimensions(self) -> int:
        """The length of every vector ``embed`` returns."""
        ...

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed ``texts``, one vector per input, in the same order."""
        ...


class NoopEmbedder(Embedder):
    """A placeholder ``Embedder`` that always raises ``NotImplementedError``.

    Exists so constructors can be wired with an ``Embedder`` that fails
    loudly if anything starts depending on embeddings before hybrid
    ranking is deliberately enabled.
    """

    def __init__(self, dimensions: int = 1536) -> None:
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError(
            "NoopEmbedder cannot embed; retrieval ranks with PostgreSQL full-text "
            "search only. Use ProviderEmbedder when hybrid ranking is enabled."
        )


class _EmbedRouter(Protocol):
    async def embed(
        self, request: EmbeddingRequest, role: ModelRole = "embedding"
    ) -> EmbeddingResponse: ...


class ProviderEmbedder(Embedder):
    """``Embedder`` backed by the model router's ``embedding`` role.

    ``embed`` uses ``input_type="passage"`` (document side); ``embed_query``
    uses ``"query"`` because the NVIDIA embedding models are asymmetric and
    score noticeably worse when both sides use the same type.
    """

    def __init__(self, router: _EmbedRouter, *, dimensions: int = 2048) -> None:
        self._router = router
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def _embed(self, texts: list[str], input_type: EmbeddingInputType) -> list[list[float]]:
        if not texts:
            return []
        response = await self._router.embed(EmbeddingRequest(texts=texts, input_type=input_type))
        if response.dimensions != self._dimensions:
            raise ValueError(
                f"embedding model returned {response.dimensions} dimensions, "
                f"expected {self._dimensions}"
            )
        return response.vectors

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await self._embed(texts, "passage")

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self._embed([text], "query")
        return vectors[0]


__all__ = ["Embedder", "NoopEmbedder", "ProviderEmbedder"]
