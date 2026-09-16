from __future__ import annotations

import pytest

from northforge.tools.errors import ToolExecutionError, ToolOutputError
from northforge.tools.invoke import invoke_tool
from northforge.tools.registry import ToolRegistry
from tests.tools.conftest import PROCUREMENT, PROCUREMENT_AND_LEGAL, make_context


async def test_get_document_chunk_success(registry: ToolRegistry) -> None:
    context = make_context(access_groups=PROCUREMENT)

    invocation = await invoke_tool(
        registry,
        "get_document_chunk",
        {"document_id": "doc_acme_msa", "chunk_id": "c04"},
        context,
    )

    chunk = invocation.output["chunk"]
    assert chunk["document_id"] == "doc_acme_msa"
    assert chunk["chunk_id"] == "c04"
    assert "Limitation of Liability" in chunk["text"]


async def test_get_document_chunk_unknown_chunk_is_not_found(registry: ToolRegistry) -> None:
    context = make_context(access_groups=PROCUREMENT)

    with pytest.raises(ToolExecutionError) as exc_info:
        await invoke_tool(
            registry,
            "get_document_chunk",
            {"document_id": "doc_acme_msa", "chunk_id": "c99"},
            context,
        )

    assert exc_info.value.retryable is False


async def test_get_document_chunk_access_filter_hides_restricted_chunk(
    registry: ToolRegistry,
) -> None:
    context = make_context(access_groups=PROCUREMENT)

    with pytest.raises(ToolExecutionError) as exc_info:
        await invoke_tool(
            registry,
            "get_document_chunk",
            {"document_id": "doc_blueharbor_dpa", "chunk_id": "c01"},
            context,
        )

    # Not-found and access-denied must be indistinguishable to the caller.
    assert exc_info.value.code == "TOOL_FAILED"
    assert exc_info.value.retryable is False


async def test_get_document_chunk_reveals_restricted_chunk_with_access(
    registry: ToolRegistry,
) -> None:
    context = make_context(access_groups=PROCUREMENT_AND_LEGAL)

    invocation = await invoke_tool(
        registry,
        "get_document_chunk",
        {"document_id": "doc_blueharbor_dpa", "chunk_id": "c01"},
        context,
    )

    assert invocation.output["chunk"]["document_id"] == "doc_blueharbor_dpa"


async def test_get_document_chunk_malformed_document_fails_output_validation(
    registry: ToolRegistry,
) -> None:
    context = make_context(access_groups=PROCUREMENT)

    with pytest.raises(ToolOutputError):
        await invoke_tool(
            registry,
            "get_document_chunk",
            {"document_id": "doc_malformed", "chunk_id": "c01"},
            context,
        )
