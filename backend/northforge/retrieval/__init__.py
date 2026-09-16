"""Chunking and access-filtered retrieval over the document corpus."""

from __future__ import annotations

from northforge.retrieval.chunking import ChunkSpan, chunk_markdown
from northforge.retrieval.embedder import Embedder, NoopEmbedder
from northforge.retrieval.fixture import FixtureRetriever
from northforge.retrieval.postgres import PostgresRetriever
from northforge.retrieval.retriever import (
    RetrievalOutcome,
    RetrievalQuery,
    RetrievalStatus,
    Retriever,
)

__all__ = [
    "ChunkSpan",
    "Embedder",
    "FixtureRetriever",
    "NoopEmbedder",
    "PostgresRetriever",
    "RetrievalOutcome",
    "RetrievalQuery",
    "RetrievalStatus",
    "Retriever",
    "chunk_markdown",
]
