"""The retrieval query/outcome shapes and the ``Retriever`` protocol.

Both ``PostgresRetriever`` (live database, full-text search) and
``FixtureRetriever`` (in-memory, for unit tests without a database)
implement this protocol, so callers -- the built-in tools, and later the
document-search API route -- never know which one they are holding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

from northforge.schemas.evidence import EvidenceChunk

RetrievalStatus = Literal["ok", "insufficient_evidence", "conflicting_evidence"]


@dataclass(frozen=True)
class RetrievalQuery:
    """A search request scoped to a project and the caller's access groups."""

    project_id: str
    query: str
    access_groups: frozenset[str]
    document_types: list[str] = field(default_factory=list)
    vendor: str | None = None
    limit: int = 8
    # ``ts_rank_cd``'s raw (unnormalized) scale is much smaller than a naive
    # 0..1 assumption suggests -- an exact, all-terms-matched short legal
    # clause commonly scores 0.005-0.03. Since the SQL WHERE clause already
    # requires `search_vector @@ websearch_to_tsquery(...)` (so every
    # returned row already matched every required term under
    # ``websearch_to_tsquery``'s AND semantics), this threshold exists to
    # reject genuinely degenerate near-zero scores, not to second-guess a
    # match the query already made.
    min_score: float = 0.001

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("RetrievalQuery.query must not be blank.")
        if not 1 <= self.limit <= 20:
            raise ValueError("RetrievalQuery.limit must be between 1 and 20.")
        if self.min_score < 0:
            raise ValueError("RetrievalQuery.min_score must not be negative.")


@dataclass(frozen=True)
class RetrievalOutcome:
    """The result of a search: an outcome status plus the evidence found.

    ``chunks`` is populated even for ``conflicting_evidence`` so a reviewer
    can see both sides of the conflict; it is empty for
    ``insufficient_evidence``.
    """

    status: RetrievalStatus
    chunks: list[EvidenceChunk]
    reason: str | None
    total_candidates: int


@runtime_checkable
class Retriever(Protocol):
    """Access-filtered, ranked retrieval over a project's document corpus."""

    async def search(self, query: RetrievalQuery) -> RetrievalOutcome: ...

    async def get_chunk(
        self,
        project_id: str,
        external_id: str,
        chunk_id: str,
        access_groups: frozenset[str],
    ) -> EvidenceChunk | None: ...


__all__ = ["RetrievalOutcome", "RetrievalQuery", "RetrievalStatus", "Retriever"]
