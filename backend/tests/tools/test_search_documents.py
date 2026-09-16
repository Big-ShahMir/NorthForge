from __future__ import annotations

from northforge.tools.invoke import invoke_tool
from northforge.tools.registry import ToolRegistry
from tests.tools.conftest import PROCUREMENT, PROCUREMENT_AND_LEGAL, make_context


async def test_search_documents_success_ranks_by_token_overlap(registry: ToolRegistry) -> None:
    context = make_context(access_groups=PROCUREMENT)

    invocation = await invoke_tool(
        registry,
        "search_documents",
        {"query": "sixty days prior notice non-renewal"},
        context,
    )

    chunks = invocation.output["chunks"]
    assert len(chunks) > 0
    # Scores are non-increasing.
    scores = [chunk["score"] for chunk in chunks]
    assert scores == sorted(scores, reverse=True)
    # Acme's non-renewal-notice clause shares every query token ("sixty" is
    # unique to it in the corpus), so it must rank first.
    assert (chunks[0]["document_id"], chunks[0]["chunk_id"]) == ("doc_acme_msa", "c03")


async def test_search_documents_respects_limit(registry: ToolRegistry) -> None:
    context = make_context(access_groups=PROCUREMENT_AND_LEGAL)

    invocation = await invoke_tool(
        registry,
        "search_documents",
        {"query": "agreement services data", "limit": 2},
        context,
    )

    assert len(invocation.output["chunks"]) <= 2


async def test_search_documents_filters_by_document_type(registry: ToolRegistry) -> None:
    context = make_context(access_groups=PROCUREMENT)

    invocation = await invoke_tool(
        registry,
        "search_documents",
        {"query": "renewal notice period vendor policy", "document_types": ["policy"]},
        context,
    )

    for chunk in invocation.output["chunks"]:
        assert chunk["document_type"] == "policy"


async def test_search_documents_filters_by_vendor(registry: ToolRegistry) -> None:
    context = make_context(access_groups=PROCUREMENT)

    invocation = await invoke_tool(
        registry,
        "search_documents",
        {"query": "renewal termination liability agreement", "vendor": "Acme Cloud Services"},
        context,
    )

    assert len(invocation.output["chunks"]) > 0
    for chunk in invocation.output["chunks"]:
        assert chunk["document_id"] == "doc_acme_msa"


async def test_search_documents_access_filter_hides_restricted_chunks(
    registry: ToolRegistry,
) -> None:
    # Blue Harbor's DPA is legal_restricted; without that group its chunks
    # must never appear, even for a query that matches them uniquely.
    context = make_context(access_groups=PROCUREMENT)

    invocation = await invoke_tool(
        registry,
        "search_documents",
        {"query": "subprocessor data protection incident processor"},
        context,
    )

    document_ids = {chunk["document_id"] for chunk in invocation.output["chunks"]}
    assert "doc_blueharbor_dpa" not in document_ids
    assert "doc_legal_policy" not in document_ids


async def test_search_documents_reveals_restricted_chunks_with_access(
    registry: ToolRegistry,
) -> None:
    context = make_context(access_groups=PROCUREMENT_AND_LEGAL)

    invocation = await invoke_tool(
        registry,
        "search_documents",
        {"query": "subprocessor data protection incident processor"},
        context,
    )

    document_ids = {chunk["document_id"] for chunk in invocation.output["chunks"]}
    assert "doc_blueharbor_dpa" in document_ids


async def test_search_documents_is_deterministic(registry: ToolRegistry) -> None:
    context = make_context(access_groups=PROCUREMENT_AND_LEGAL)
    args = {"query": "agreement liability termination vendor notice"}

    first = await invoke_tool(registry, "search_documents", args, context)
    second = await invoke_tool(registry, "search_documents", args, context)

    first_order = [(c["document_id"], c["chunk_id"]) for c in first.output["chunks"]]
    second_order = [(c["document_id"], c["chunk_id"]) for c in second.output["chunks"]]
    assert first_order == second_order
