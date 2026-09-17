"""Chunking and access-filtered retrieval over the document corpus."""

from __future__ import annotations

from northforge.retrieval.chunking import ChunkSpan, chunk_markdown
from northforge.retrieval.embedder import Embedder, NoopEmbedder, ProviderEmbedder
from northforge.retrieval.fixture import FixtureRetriever
from northforge.retrieval.postgres import PostgresRetriever
from northforge.retrieval.reranker import NoopReranker, ProviderReranker, Reranker
from northforge.retrieval.retriever import (
    RetrievalOutcome,
    RetrievalQuery,
    RetrievalStatus,
    Retriever,
)
from northforge.retrieval.rules import (
    FixturePolicyRuleStore,
    PolicyRuleStore,
    PostgresPolicyRuleStore,
)

__all__ = [
    "ChunkSpan",
    "Embedder",
    "FixturePolicyRuleStore",
    "FixtureRetriever",
    "NoopEmbedder",
    "NoopReranker",
    "PolicyRuleStore",
    "PostgresPolicyRuleStore",
    "PostgresRetriever",
    "ProviderEmbedder",
    "ProviderReranker",
    "Reranker",
    "RetrievalOutcome",
    "RetrievalQuery",
    "RetrievalStatus",
    "Retriever",
    "chunk_markdown",
]
