"""``PostgresRetriever``: access-filtered full-text search over ``document_chunks``.

The query is written with ``sqlalchemy.text()`` and bound parameters only --
every value that comes from a caller (project id, query text, access
groups, filters) is passed as a bind parameter, never interpolated into the
SQL string. The only thing that varies the *shape* of the SQL between calls
is whether the optional ``document_types``/``vendor`` filters are present,
and which of two fixed match expressions is used (see below); neither is
ever built from caller-supplied values.

Ranking is a two-stage attempt, both stages using ``ts_rank_cd`` against the
same ``search_vector``:

1. ``websearch_to_tsquery`` ("and" mode): Postgres's web-search-style
   parser, which ANDs together every significant word by default (quoting a
   phrase or writing ``or`` between words changes that). This is tried
   first because it is precise -- every returned chunk contains every
   significant query word.
2. A same-terms "or" fallback, used only when the "and" stage returns zero
   rows: the "and" tsquery is re-parsed and its ``&`` operators replaced
   with ``|`` (``to_tsquery('english', replace(websearch_to_tsquery(...)
   ::text, ' & ', ' | '))``), so a chunk matching *any* significant query
   word is a candidate. A natural-language query routinely includes a word
   or two that the right clause simply does not use verbatim (the clause
   says "decline renewal", the query says "opt out of renewal"); without
   this fallback such queries would return zero candidates even though the
   right chunk is a near-paraphrase, which is what ``retrieval_eval.json``'s
   quality gate (``tests/retrieval/test_retrieval_quality.py``) measures
   directly. The fallback is scored against a much higher floor
   (``_OR_FALLBACK_MIN_SCORE``, well above the ordinary ``min_score``)
   because an "or" match is far weaker evidence than an "and" match --
   empirically, a genuine near-paraphrase match scores at least 0.7 this
   way against the synthetic corpus, while an honest miss (no related
   clause exists at all) tops out around 0.4, so 0.5 separates them
   cleanly without needing per-query tuning.

Constructor accepts either a session factory (``async_sessionmaker``, or any
zero-argument callable returning a fresh ``AsyncSession``) or an existing
``AsyncSession``. Tests using the shared ``db_session`` fixture -- a single
connection wrapped in a savepoint that is rolled back at teardown -- must
pass that session directly, because opening a second connection through a
factory would not see uncommitted rows written on the test's connection.
Application code passes the app's session factory instead, so each search
gets its own short-lived session.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import date
from typing import Any, Literal

from sqlalchemy import String, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from northforge.retrieval.retriever import RetrievalOutcome, RetrievalQuery
from northforge.schemas.evidence import EvidenceChunk

SessionFactory = async_sessionmaker[AsyncSession] | Callable[[], AsyncSession]
SessionOrFactory = AsyncSession | SessionFactory

MatchMode = Literal["and", "or"]

# See the module docstring: the "and" stage requires every significant query
# word (Postgres's ``websearch_to_tsquery`` default); the "or" stage is only
# tried when that returns nothing, and requires a much higher score because
# it accepts a single matching word.
_MATCH_EXPRESSIONS: dict[MatchMode, str] = {
    "and": "websearch_to_tsquery('english', :query_text)",
    "or": (
        "to_tsquery('english', "
        "replace(websearch_to_tsquery('english', :query_text)::text, ' & ', ' | '))"
    ),
}

_OR_FALLBACK_MIN_SCORE = 0.5

_SEARCH_SQL_TEMPLATE = """
SELECT
    documents.external_id AS external_id,
    documents.name AS document_name,
    documents.document_type AS document_type,
    documents.vendor AS vendor,
    documents.effective_date AS effective_date,
    documents.access_group AS access_group,
    documents.dataset_version AS dataset_version,
    documents.metadata_json AS document_metadata,
    document_chunks.chunk_id AS chunk_id,
    document_chunks.sequence AS sequence,
    document_chunks.heading AS heading,
    document_chunks.text AS text,
    document_chunks.start_offset AS start_offset,
    document_chunks.end_offset AS end_offset,
    document_chunks.content_hash AS content_hash,
    document_chunks.metadata_json AS chunk_metadata,
    ts_rank_cd(document_chunks.search_vector, {match_expression}) AS rank
FROM document_chunks
JOIN documents ON documents.id = document_chunks.document_id
WHERE documents.project_id = CAST(:project_id AS uuid)
  AND documents.access_group = ANY(:access_groups)
  AND document_chunks.search_vector @@ {match_expression}
  {document_type_filter}
  {vendor_filter}
ORDER BY rank DESC, documents.effective_date DESC NULLS LAST, document_chunks.sequence
LIMIT :fetch_limit
"""

_GET_CHUNK_SQL = text(
    """
    SELECT
        documents.external_id AS external_id,
        documents.name AS document_name,
        documents.document_type AS document_type,
        documents.vendor AS vendor,
        documents.effective_date AS effective_date,
        documents.access_group AS access_group,
        documents.dataset_version AS dataset_version,
        documents.metadata_json AS document_metadata,
        document_chunks.chunk_id AS chunk_id,
        document_chunks.heading AS heading,
        document_chunks.text AS text,
        document_chunks.start_offset AS start_offset,
        document_chunks.end_offset AS end_offset,
        document_chunks.content_hash AS content_hash,
        document_chunks.metadata_json AS chunk_metadata
    FROM document_chunks
    JOIN documents ON documents.id = document_chunks.document_id
    WHERE documents.project_id = CAST(:project_id AS uuid)
      AND documents.external_id = :external_id
      AND document_chunks.chunk_id = :chunk_id
      AND documents.access_group = ANY(:access_groups)
    """
).bindparams(bindparam("access_groups", type_=ARRAY(String())))


def _row_to_evidence_chunk(row: RowMapping) -> EvidenceChunk:
    metadata: dict[str, Any] = dict(row["document_metadata"] or {})
    metadata.update(row["chunk_metadata"] or {})
    effective_date = row["effective_date"]
    metadata.update(
        {
            "vendor": row["vendor"],
            "document_type": row["document_type"],
            "effective_date": effective_date.isoformat() if effective_date else None,
            "access_group": row["access_group"],
            "heading": row["heading"],
            "dataset_version": row["dataset_version"],
            "start_offset": row["start_offset"],
            "end_offset": row["end_offset"],
            "content_hash": row["content_hash"],
        }
    )
    return EvidenceChunk(
        document_id=row["external_id"],
        chunk_id=row["chunk_id"],
        document_name=row["document_name"],
        document_type=row["document_type"],
        text=row["text"],
        score=float(row["rank"]) if "rank" in row.keys() else None,
        metadata=metadata,
    )


def _effective_date_sort_key(row: RowMapping) -> tuple[int, date]:
    effective_date = row["effective_date"]
    if effective_date is None:
        return (0, date.min)
    return (1, effective_date)


def _dedupe_by_content_hash(rows: list[RowMapping]) -> list[RowMapping]:
    """Keep one row per ``content_hash``: the one with the newest ``effective_date``."""
    best: dict[str, RowMapping] = {}
    order: list[str] = []
    for row in rows:
        content_hash = row["content_hash"]
        if content_hash not in best:
            order.append(content_hash)
            best[content_hash] = row
        elif _effective_date_sort_key(row) > _effective_date_sort_key(best[content_hash]):
            best[content_hash] = row
    return [best[content_hash] for content_hash in order]


def _detect_conflict(rows: list[RowMapping]) -> bool:
    """Whether ``rows`` contain two policy documents disagreeing on a policy area.

    A conflict is two chunks from ``document_type == "policy"`` documents
    whose document metadata share ``policy_area``, whose ``effective_date``
    values differ, and where the newer document's ``supersedes`` metadata
    does not name the older document's ``external_id``.
    """
    policy_rows = [row for row in rows if row["document_type"] == "policy"]
    for i, row_a in enumerate(policy_rows):
        meta_a = row_a["document_metadata"] or {}
        area_a = meta_a.get("policy_area")
        if not area_a:
            continue
        for row_b in policy_rows[i + 1 :]:
            meta_b = row_b["document_metadata"] or {}
            if meta_b.get("policy_area") != area_a:
                continue
            date_a, date_b = row_a["effective_date"], row_b["effective_date"]
            if date_a is None or date_b is None or date_a == date_b:
                continue
            newer, older = (row_a, row_b) if date_a > date_b else (row_b, row_a)
            newer_meta = newer["document_metadata"] or {}
            supersedes = newer_meta.get("supersedes") or []
            if older["external_id"] not in supersedes:
                return True
    return False


class PostgresRetriever:
    """A ``Retriever`` backed by PostgreSQL full-text search."""

    def __init__(self, session_or_factory: SessionOrFactory) -> None:
        self._session_or_factory = session_or_factory

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[AsyncSession]:
        if isinstance(self._session_or_factory, AsyncSession):
            yield self._session_or_factory
            return
        session = self._session_or_factory()
        try:
            yield session
        finally:
            await session.close()

    def _build_search_statement(self, query: RetrievalQuery, mode: MatchMode) -> Any:
        document_type_filter = (
            "AND documents.document_type = ANY(:document_types)" if query.document_types else ""
        )
        vendor_filter = "AND documents.vendor = :vendor" if query.vendor is not None else ""
        sql = _SEARCH_SQL_TEMPLATE.format(
            match_expression=_MATCH_EXPRESSIONS[mode],
            document_type_filter=document_type_filter,
            vendor_filter=vendor_filter,
        )
        bindparams = [bindparam("access_groups", type_=ARRAY(String()))]
        if query.document_types:
            bindparams.append(bindparam("document_types", type_=ARRAY(String())))
        return text(sql).bindparams(*bindparams)

    async def _execute_search(self, query: RetrievalQuery, mode: MatchMode) -> list[RowMapping]:
        statement = self._build_search_statement(query, mode)
        params: dict[str, Any] = {
            "project_id": query.project_id,
            "query_text": query.query,
            "access_groups": sorted(query.access_groups),
            "fetch_limit": query.limit * 3,
        }
        if query.document_types:
            params["document_types"] = query.document_types
        if query.vendor is not None:
            params["vendor"] = query.vendor

        async with self._session() as session:
            result = await session.execute(statement, params)
            return list(result.mappings().all())

    async def search(self, query: RetrievalQuery) -> RetrievalOutcome:
        rows = await self._execute_search(query, "and")
        min_score = query.min_score

        if not rows:
            # No chunk contains every significant query word. Fall back to
            # an "any significant word" match, at a much higher score floor
            # -- see the module docstring for why this separates genuine
            # near-paraphrase matches from honest misses.
            rows = await self._execute_search(query, "or")
            min_score = max(min_score, _OR_FALLBACK_MIN_SCORE)

        total_candidates = len(rows)
        if not rows or float(rows[0]["rank"]) < min_score:
            return RetrievalOutcome(
                status="insufficient_evidence",
                chunks=[],
                reason="No sufficiently relevant evidence was found for this query.",
                total_candidates=total_candidates,
            )

        deduped = _dedupe_by_content_hash(rows)
        capped = deduped[: query.limit]

        # Defensive re-check: every row's access_group must be one the
        # caller holds. The SQL WHERE clause already guarantees this; this
        # assertion exists so a future change to the query cannot silently
        # leak a restricted chunk.
        for row in capped:
            if row["access_group"] not in query.access_groups:
                raise RuntimeError(
                    f"Retriever returned a chunk outside the caller's access groups: "
                    f"{row['access_group']!r} not in {sorted(query.access_groups)!r}"
                )

        chunks = [_row_to_evidence_chunk(row) for row in capped]
        if _detect_conflict(capped):
            return RetrievalOutcome(
                status="conflicting_evidence",
                chunks=chunks,
                reason="Retrieved policy documents disagree and neither supersedes the other.",
                total_candidates=total_candidates,
            )
        return RetrievalOutcome(
            status="ok", chunks=chunks, reason=None, total_candidates=total_candidates
        )

    async def get_chunk(
        self,
        project_id: str,
        external_id: str,
        chunk_id: str,
        access_groups: frozenset[str],
    ) -> EvidenceChunk | None:
        params = {
            "project_id": project_id,
            "external_id": external_id,
            "chunk_id": chunk_id,
            "access_groups": sorted(access_groups),
        }
        async with self._session() as session:
            result = await session.execute(_GET_CHUNK_SQL, params)
            row = result.mappings().first()
        if row is None:
            return None
        if row["access_group"] not in access_groups:
            raise RuntimeError(
                f"Retriever returned a chunk outside the caller's access groups: "
                f"{row['access_group']!r} not in {sorted(access_groups)!r}"
            )
        return _row_to_evidence_chunk(row)


__all__ = ["PostgresRetriever"]
