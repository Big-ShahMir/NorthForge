from __future__ import annotations

import pytest

from northforge.tools.errors import (
    ToolArgumentError,
    ToolBlockedError,
    ToolExecutionError,
    ToolOutputError,
)
from northforge.tools.invoke import invoke_tool
from northforge.tools.registry import ToolRegistry
from tests.tools.conftest import PROCUREMENT, RecordingTracer, make_context


async def test_blocked_when_tool_not_in_allowed_tools(registry: ToolRegistry) -> None:
    context = make_context(allowed_tools=frozenset())

    with pytest.raises(ToolBlockedError) as exc_info:
        await invoke_tool(registry, "search_documents", {"query": "renewal"}, context)

    assert exc_info.value.code == "TOOL_BLOCKED"
    assert exc_info.value.failure_category == "wrong_tool"
    assert exc_info.value.retryable is False


async def test_unknown_tool_name_is_blocked(registry: ToolRegistry) -> None:
    context = make_context()

    with pytest.raises(ToolBlockedError):
        await invoke_tool(registry, "does_not_exist", {}, context)


async def test_extra_argument_rejected(registry: ToolRegistry) -> None:
    context = make_context()

    with pytest.raises(ToolArgumentError) as exc_info:
        await invoke_tool(
            registry,
            "search_documents",
            {"query": "renewal", "not_a_real_field": "x"},
            context,
        )

    assert exc_info.value.code == "MALFORMED_TOOL_ARGUMENTS"
    assert exc_info.value.failure_category == "malformed_tool_arguments"


async def test_wrong_type_argument_rejected(registry: ToolRegistry) -> None:
    context = make_context()

    with pytest.raises(ToolArgumentError):
        await invoke_tool(
            registry,
            "search_documents",
            {"query": "renewal", "limit": "not-a-number"},
            context,
        )


async def test_timeout_becomes_retryable_tool_failed(registry: ToolRegistry) -> None:
    context = make_context()

    with pytest.raises(ToolExecutionError) as exc_info:
        await invoke_tool(
            registry,
            "search_documents",
            {"query": "__timeout__"},
            context,
            timeout_seconds=0.05,
        )

    assert exc_info.value.code == "TOOL_FAILED"
    assert exc_info.value.failure_category == "timeout"
    assert exc_info.value.retryable is True


async def test_error_trigger_raises_retryable_tool_failed(registry: ToolRegistry) -> None:
    context = make_context()

    with pytest.raises(ToolExecutionError) as exc_info:
        await invoke_tool(registry, "search_documents", {"query": "__error__"}, context)

    assert exc_info.value.code == "TOOL_FAILED"
    assert exc_info.value.retryable is True


async def test_malformed_output_becomes_tool_output_invalid(registry: ToolRegistry) -> None:
    context = make_context()

    with pytest.raises(ToolOutputError) as exc_info:
        await invoke_tool(
            registry,
            "get_document_chunk",
            {"document_id": "doc_malformed", "chunk_id": "c01"},
            context,
        )

    assert exc_info.value.code == "TOOL_OUTPUT_INVALID"


async def test_trace_callback_receives_record_on_success(registry: ToolRegistry) -> None:
    tracer = RecordingTracer()
    context = make_context(access_groups=PROCUREMENT, trace=tracer)

    await invoke_tool(registry, "search_documents", {"query": "renewal"}, context)

    assert len(tracer.records) == 1
    record = tracer.records[0]
    assert record.name == "search_documents"
    assert record.outcome == "ok"
    assert record.args["query"] == "renewal"
    assert record.duration_ms >= 0


async def test_trace_callback_receives_record_on_failure(registry: ToolRegistry) -> None:
    tracer = RecordingTracer()
    context = make_context(trace=tracer, allowed_tools=frozenset())

    with pytest.raises(ToolBlockedError):
        await invoke_tool(registry, "search_documents", {"query": "renewal"}, context)

    assert len(tracer.records) == 1
    assert tracer.records[0].outcome == "TOOL_BLOCKED"
