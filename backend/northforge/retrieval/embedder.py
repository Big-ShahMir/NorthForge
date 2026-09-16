"""Embedding interface, fixed now so Phase 4 can add pgvector without changing callers.

Phase 3 ranks retrieval results with PostgreSQL full-text search only (see
ADR in ``DECISIONS.md``); nothing in this codebase calls ``Embedder.embed``
yet. ``NoopEmbedder`` exists purely to give the interface a concrete,
importable implementation that fails loudly if something starts depending
on it before Phase 4 lands a real one.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


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

    Semantic ranking (pgvector-backed) is Phase 4 scope; this class exists
    only so the ``Embedder`` protocol has a concrete implementation to wire
    through constructors today.
    """

    def __init__(self, dimensions: int = 1536) -> None:
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError(
            "Embedding is not implemented in Phase 3; retrieval ranks with "
            "PostgreSQL full-text search only. See Phase 4 (pgvector)."
        )


__all__ = ["Embedder", "NoopEmbedder"]
