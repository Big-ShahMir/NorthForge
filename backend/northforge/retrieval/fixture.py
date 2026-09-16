"""``FixtureRetriever``: the ``Retriever`` protocol over the Phase 2 fixture corpus.

Used by tests (and, until Stage B wires the real database-backed tools,
anywhere a ``Retriever`` is needed without a live PostgreSQL database). It
wraps ``northforge.tools.fixtures.corpus`` and reuses the same deterministic
token-overlap ranking as ``northforge.tools.builtin.search_documents`` (the
scoring function is duplicated, not imported, to keep the fixture-corpus
tool and this retriever independently importable -- neither depends on the
other's private helpers).
"""

from __future__ import annotations

import re

from northforge.retrieval.retriever import RetrievalOutcome, RetrievalQuery
from northforge.schemas.evidence import EvidenceChunk
from northforge.tools.fixtures import corpus

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN_PATTERN.findall(text.lower()))


def _to_evidence_chunk(chunk: corpus.Chunk, score: float | None) -> EvidenceChunk:
    return EvidenceChunk(
        document_id=chunk.document_id,
        chunk_id=chunk.chunk_id,
        document_name=chunk.document_name,
        document_type=chunk.document_type,
        text=chunk.text,
        score=score,
        metadata=chunk.metadata,
    )


class FixtureRetriever:
    """A ``Retriever`` over the small, hand-authored Phase 2 fixture corpus.

    The fixture corpus has no notion of project or dataset version, so
    ``project_id`` is accepted (to satisfy the ``Retriever`` protocol) but
    otherwise ignored.
    """

    async def search(self, query: RetrievalQuery) -> RetrievalOutcome:
        query_tokens = _tokenize(query.query)
        candidates = corpus.visible_chunks(query.access_groups)

        if query.document_types:
            wanted_types = set(query.document_types)
            candidates = [c for c in candidates if c.document_type in wanted_types]

        if query.vendor is not None:
            wanted_vendor = query.vendor.strip().lower()
            candidates = [
                c
                for c in candidates
                if str(c.metadata.get("vendor", "")).strip().lower() == wanted_vendor
            ]

        scored = [(chunk, len(query_tokens & _tokenize(chunk.text))) for chunk in candidates]
        matches = [(chunk, score) for chunk, score in scored if score >= query.min_score]
        matches.sort(key=lambda pair: (-pair[1], pair[0].chunk_id))

        if not matches:
            return RetrievalOutcome(
                status="insufficient_evidence",
                chunks=[],
                reason="No chunks matched the query.",
                total_candidates=0,
            )

        top = matches[: query.limit]
        return RetrievalOutcome(
            status="ok",
            chunks=[_to_evidence_chunk(chunk, float(score)) for chunk, score in top],
            reason=None,
            total_candidates=len(matches),
        )

    async def get_chunk(
        self,
        project_id: str,
        external_id: str,
        chunk_id: str,
        access_groups: frozenset[str],
    ) -> EvidenceChunk | None:
        del project_id  # the fixture corpus is not project-scoped
        chunk = corpus.find_chunk(external_id, chunk_id, access_groups=access_groups)
        if chunk is None:
            return None
        return _to_evidence_chunk(chunk, None)


__all__ = ["FixtureRetriever"]
