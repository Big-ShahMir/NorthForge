from __future__ import annotations

from northforge.retrieval.fixture import FixtureRetriever
from northforge.retrieval.retriever import RetrievalQuery

PROCUREMENT = frozenset({"procurement"})
PROCUREMENT_AND_LEGAL = frozenset({"procurement", "legal_restricted"})


async def test_search_returns_relevant_chunks_for_a_procurement_query() -> None:
    retriever = FixtureRetriever()
    query = RetrievalQuery(
        project_id="proj_test", query="renewal notice period", access_groups=PROCUREMENT
    )

    outcome = await retriever.search(query)

    assert outcome.status == "ok"
    assert outcome.chunks
    assert all(c.metadata["access_group"] == "procurement" for c in outcome.chunks)


async def test_search_excludes_restricted_chunks_without_the_access_group() -> None:
    retriever = FixtureRetriever()
    query = RetrievalQuery(
        project_id="proj_test",
        query="data processing agreement liability",
        access_groups=PROCUREMENT,
    )

    outcome = await retriever.search(query)

    assert all(c.metadata["access_group"] != "legal_restricted" for c in outcome.chunks)


async def test_search_includes_restricted_chunks_with_the_access_group() -> None:
    retriever = FixtureRetriever()
    query = RetrievalQuery(
        project_id="proj_test",
        query="data processing agreement subprocessor",
        access_groups=PROCUREMENT_AND_LEGAL,
    )

    outcome = await retriever.search(query)

    assert outcome.status == "ok"
    assert any(c.metadata["access_group"] == "legal_restricted" for c in outcome.chunks)


async def test_search_filters_by_document_type() -> None:
    retriever = FixtureRetriever()
    query = RetrievalQuery(
        project_id="proj_test",
        query="renewal notice termination liability",
        access_groups=PROCUREMENT,
        document_types=["policy"],
    )

    outcome = await retriever.search(query)

    assert outcome.chunks
    assert all(c.document_type == "policy" for c in outcome.chunks)


async def test_search_filters_by_vendor() -> None:
    retriever = FixtureRetriever()
    query = RetrievalQuery(
        project_id="proj_test",
        query="renewal notice termination liability",
        access_groups=PROCUREMENT,
        vendor="Acme Cloud Services",
    )

    outcome = await retriever.search(query)

    assert outcome.chunks
    assert all(c.metadata.get("vendor") == "Acme Cloud Services" for c in outcome.chunks)


async def test_search_respects_limit() -> None:
    retriever = FixtureRetriever()
    query = RetrievalQuery(
        project_id="proj_test",
        query="agreement notice renewal liability termination data",
        access_groups=PROCUREMENT_AND_LEGAL,
        limit=2,
    )

    outcome = await retriever.search(query)

    assert len(outcome.chunks) <= 2


async def test_search_abstains_on_nonsense_query() -> None:
    retriever = FixtureRetriever()
    query = RetrievalQuery(
        project_id="proj_test",
        query="xyzzy quux frobnicate wibblewobble",
        access_groups=PROCUREMENT_AND_LEGAL,
    )

    outcome = await retriever.search(query)

    assert outcome.status == "insufficient_evidence"
    assert outcome.chunks == []


async def test_get_chunk_returns_the_chunk_when_visible() -> None:
    retriever = FixtureRetriever()

    chunk = await retriever.get_chunk("proj_test", "doc_acme_msa", "c02", PROCUREMENT)

    assert chunk is not None
    assert chunk.chunk_id == "c02"
    assert chunk.document_id == "doc_acme_msa"


async def test_get_chunk_denies_restricted_chunk_without_access_group() -> None:
    retriever = FixtureRetriever()

    chunk = await retriever.get_chunk("proj_test", "doc_blueharbor_dpa", "c01", PROCUREMENT)

    assert chunk is None


async def test_get_chunk_allows_restricted_chunk_with_access_group() -> None:
    retriever = FixtureRetriever()

    chunk = await retriever.get_chunk(
        "proj_test", "doc_blueharbor_dpa", "c01", PROCUREMENT_AND_LEGAL
    )

    assert chunk is not None
    assert chunk.chunk_id == "c01"


async def test_get_chunk_returns_none_for_unknown_chunk() -> None:
    retriever = FixtureRetriever()

    chunk = await retriever.get_chunk("proj_test", "doc_acme_msa", "c99", PROCUREMENT)

    assert chunk is None
