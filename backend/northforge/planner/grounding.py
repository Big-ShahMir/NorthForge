"""Project facts and bounded tool probing shown to the planner model.

``collect_grounding`` runs two grouped-count SQL queries so the model is
told what document types, vendors, and policy areas actually exist in the
project (access-filtered, exactly like retrieval), instead of guessing.
``probe_tools`` lets the model make a few real, access-filtered tool calls
through the caller's own ``ToolContext`` before it commits to a proposal;
every call and its outcome is recorded, and a probe failure never stops
planning -- only proposing does.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from northforge.db.models import Document, PolicyRuleRow
from northforge.planner import prompts
from northforge.planner.schema import (
    MAX_TOOL_PROBE_CALLS,
    MAX_TOOL_PROBE_ROUNDS,
    MAX_TOOL_RESULT_CHARS,
    GroundingSummary,
    ToolProbeRecord,
)
from northforge.providers.errors import ProviderError
from northforge.providers.router import ModelRouter, Route
from northforge.providers.types import (
    GenerationRequest,
    Message,
    ModelInvocationRecord,
    ModelResponse,
    ToolCallRequest,
    Usage,
    tool_definition_from_spec,
)
from northforge.tools.context import ToolContext
from northforge.tools.errors import ToolError
from northforge.tools.invoke import invoke_tool
from northforge.tools.registry import ToolRegistry

_MAX_GROUPS = 30
_TRUNCATION_MARKER = "... [truncated]"


def _truncate(text: str) -> str:
    if len(text) <= MAX_TOOL_RESULT_CHARS:
        return text
    keep = max(0, MAX_TOOL_RESULT_CHARS - len(_TRUNCATION_MARKER))
    return text[:keep] + _TRUNCATION_MARKER


async def _grouped_counts(session: AsyncSession, stmt: Any) -> dict[str, int]:
    rows = (await session.execute(stmt)).all()
    ordered = sorted(
        ((str(value), int(count)) for value, count in rows if value is not None),
        key=lambda pair: pair[1],
        reverse=True,
    )
    return dict(ordered[:_MAX_GROUPS])


async def collect_grounding(
    session: AsyncSession, project_id: uuid.UUID, access_groups: frozenset[str]
) -> GroundingSummary:
    """Access-filtered counts of document types, vendors, and policy areas.

    Returns an empty summary without querying when ``access_groups`` is
    empty -- a caller with no access group can see nothing.
    """
    if not access_groups:
        return GroundingSummary()

    groups = list(access_groups)

    document_types_stmt = (
        select(Document.document_type, func.count())
        .where(Document.project_id == project_id, Document.access_group.in_(groups))
        .group_by(Document.document_type)
    )
    vendors_stmt = (
        select(Document.vendor, func.count())
        .where(
            Document.project_id == project_id,
            Document.access_group.in_(groups),
            Document.vendor.is_not(None),
        )
        .group_by(Document.vendor)
    )
    policy_areas_stmt = (
        select(PolicyRuleRow.policy_area, func.count())
        .select_from(PolicyRuleRow)
        .join(Document, Document.id == PolicyRuleRow.policy_document_id)
        .where(PolicyRuleRow.project_id == project_id, Document.access_group.in_(groups))
        .group_by(PolicyRuleRow.policy_area)
    )

    return GroundingSummary(
        document_types=await _grouped_counts(session, document_types_stmt),
        vendors=await _grouped_counts(session, vendors_stmt),
        policy_areas=await _grouped_counts(session, policy_areas_stmt),
    )


@dataclass
class ProbeOutcome:
    """Everything a tool-probe round produced, ready for the prompt and the trace."""

    tool_results: list[str] = field(default_factory=list)
    records: list[ToolProbeRecord] = field(default_factory=list)
    invocations: list[ModelInvocationRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _record_from_tool_response(response: ModelResponse[None]) -> ModelInvocationRecord:
    return ModelInvocationRecord(
        operation="tool_call",
        provider=response.provider,
        model=response.model,
        outcome="ok",
        latency_ms=response.latency_ms,
        role="planner",
        attempts=response.attempts,
        cache_hit=response.cache_hit,
        fallback_used=response.fallback_used,
        usage=response.usage,
        request_id=response.request_id,
    )


def _record_from_tool_error(exc: ProviderError, route: Route) -> ModelInvocationRecord:
    return ModelInvocationRecord(
        operation="tool_call",
        provider=exc.provider or route.provider,
        model=exc.model or route.primary,
        outcome=exc.code,
        latency_ms=0.0,
        role="planner",
        attempts=int(getattr(exc, "attempts", 1)),
        cache_hit=False,
        fallback_used=False,
        usage=Usage(),
        request_id=exc.request_id,
    )


def _summarize_search_documents(output: dict[str, Any]) -> str:
    chunks = output.get("chunks", [])
    outcome = output.get("outcome", {}) or {}
    lines = [f"status={outcome.get('status', 'unknown')}, {len(chunks)} chunk(s)"]
    for chunk in chunks[:3]:
        metadata = chunk.get("metadata", {}) or {}
        lines.append(
            f"- {chunk.get('document_name', '?')} / {chunk.get('document_type', '?')} / "
            f"{metadata.get('vendor', 'unknown')}"
        )
    return _truncate("\n".join(lines))


def _summarize_lookup_policy_rules(output: dict[str, Any]) -> str:
    rules = output.get("rules", [])
    areas = sorted({rule.get("policy_area", "") for rule in rules if rule.get("policy_area")})
    lines = [f"{len(rules)} rule(s); policy_area(s): {', '.join(areas) or '(none)'}"]
    for rule in rules[:5]:
        lines.append(f"- {rule.get('rule_id', '?')} ({rule.get('severity', '?')})")
    return _truncate("\n".join(lines))


def _summarize_get_document_chunk(output: dict[str, Any]) -> str:
    chunk = output.get("chunk", {}) or {}
    lines = [
        f"document_id={chunk.get('document_id', '?')} chunk_id={chunk.get('chunk_id', '?')}",
        str(chunk.get("text", ""))[:300],
    ]
    return _truncate("\n".join(lines))


_SUMMARIZERS = {
    "search_documents": _summarize_search_documents,
    "lookup_policy_rules": _summarize_lookup_policy_rules,
    "get_document_chunk": _summarize_get_document_chunk,
}


async def _run_probe_call(
    tool_registry: ToolRegistry,
    tool_context: ToolContext,
    call: ToolCallRequest,
    warnings: list[str],
) -> tuple[str, str, float]:
    """Run one probed tool call. Never raises: unknown tools and ``ToolError`` are recorded."""
    if call.name not in tool_registry.names():
        warnings.append(f"tool probe requested unknown tool: {call.name}")
        return f"unknown tool: {call.name}", "unknown_tool", 0.0

    try:
        invocation = await invoke_tool(
            tool_registry, call.name, call.arguments, tool_context, timeout_seconds=15
        )
    except ToolError as exc:
        warnings.append(f"tool probe '{call.name}' failed: {exc.code}")
        return f"tool '{call.name}' failed: {exc.code}", exc.code, 0.0

    summarizer = _SUMMARIZERS.get(call.name)
    summary = summarizer(invocation.output) if summarizer else _truncate(str(invocation.output))
    return summary, "ok", invocation.duration_ms


async def probe_tools(
    *,
    model_router: ModelRouter,
    tool_registry: ToolRegistry,
    tool_context: ToolContext,
    system_prompt: str,
    user_prompt: str,
) -> ProbeOutcome:
    """Let the model make a few bounded, access-filtered tool calls before proposing.

    Never raises: a ``ProviderError`` from ``tool_call`` ends probing (with a
    warning) and returns whatever was gathered so far; proposing itself is
    not optional, only this probe round is.
    """
    tool_defs = [tool_definition_from_spec(spec) for spec in tool_registry.specs()]
    messages: list[Message] = [
        Message(role="system", content=system_prompt),
        Message(role="user", content=f"{user_prompt}\n\n{prompts.PROBE_INSTRUCTION}"),
    ]

    tool_results: list[str] = []
    records: list[ToolProbeRecord] = []
    invocations: list[ModelInvocationRecord] = []
    warnings: list[str] = []
    total_calls = 0
    capped = False

    for _round in range(MAX_TOOL_PROBE_ROUNDS):
        request = GenerationRequest(
            messages=messages,
            tools=tool_defs,
            tool_choice="auto",
            temperature=0,
            max_tokens=1024,
            metadata={"stage": "probe"},
        )
        try:
            response = await model_router.tool_call("planner", request, tool_defs)
        except ProviderError as exc:
            warnings.append(f"tool probe skipped: {exc.code}")
            invocations.append(_record_from_tool_error(exc, model_router.route("planner")))
            return ProbeOutcome(
                tool_results=tool_results,
                records=records,
                invocations=invocations,
                warnings=warnings,
            )

        invocations.append(_record_from_tool_response(response))
        if not response.tool_calls:
            break

        round_messages: list[Message] = [
            Message(
                role="assistant", content=response.content or "", tool_calls=response.tool_calls
            )
        ]
        made_a_call = False
        for call in response.tool_calls:
            if total_calls >= MAX_TOOL_PROBE_CALLS:
                if not capped:
                    warnings.append(
                        f"tool probe call cap ({MAX_TOOL_PROBE_CALLS}) reached; "
                        "ignoring remaining calls"
                    )
                    capped = True
                continue
            total_calls += 1
            made_a_call = True
            summary, call_outcome, duration_ms = await _run_probe_call(
                tool_registry, tool_context, call, warnings
            )
            records.append(
                ToolProbeRecord(
                    name=call.name,
                    args=call.arguments,
                    outcome=call_outcome,
                    duration_ms=duration_ms,
                )
            )
            tool_results.append(prompts.tool_result_block(call.name, summary))
            round_messages.append(
                Message(role="tool", content=summary, tool_call_id=call.id, name=call.name)
            )

        messages = [*messages, *round_messages]
        if not made_a_call or total_calls >= MAX_TOOL_PROBE_CALLS:
            break

    return ProbeOutcome(
        tool_results=tool_results, records=records, invocations=invocations, warnings=warnings
    )


__all__ = ["ProbeOutcome", "collect_grounding", "probe_tools"]
