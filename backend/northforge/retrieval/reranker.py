"""Reranking interface and the provider-backed implementation.

Phase 4 adds the ``Reranker`` protocol and ``ProviderReranker`` (NVIDIA
reranking NIM through the model router) but does not wire either into
``PostgresRetriever``: the retrieval quality gate passes with lexical
ranking alone, and ADR-027 records the evidence that must appear before
reranking joins the retrieval path. ``NoopReranker`` returns candidates
unchanged so callers can hold a ``Reranker`` today.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from northforge.providers.types import ModelRole, RerankRequest, RerankResponse
from northforge.schemas.evidence import EvidenceChunk


@runtime_checkable
class Reranker(Protocol):
    """Reorder retrieved chunks by relevance to ``query``; never adds or invents chunks."""

    async def rerank(
        self, query: str, chunks: list[EvidenceChunk], top_n: int | None = None
    ) -> list[EvidenceChunk]: ...


class _RerankRouter(Protocol):
    async def rerank(
        self, request: RerankRequest, role: ModelRole = "reranker"
    ) -> RerankResponse: ...


class NoopReranker:
    """Keeps the retriever's order; applies ``top_n`` only."""

    async def rerank(
        self, query: str, chunks: list[EvidenceChunk], top_n: int | None = None
    ) -> list[EvidenceChunk]:
        return list(chunks[:top_n]) if top_n is not None else list(chunks)


class ProviderReranker:
    """``Reranker`` backed by the model router's ``reranker`` role."""

    def __init__(self, router: _RerankRouter) -> None:
        self._router = router

    async def rerank(
        self, query: str, chunks: list[EvidenceChunk], top_n: int | None = None
    ) -> list[EvidenceChunk]:
        if not chunks:
            return []
        response = await self._router.rerank(
            RerankRequest(query=query, passages=[chunk.text for chunk in chunks], top_n=top_n)
        )
        return [chunks[result.index] for result in response.rankings]


__all__ = ["NoopReranker", "ProviderReranker", "Reranker"]
