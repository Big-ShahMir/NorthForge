"""Tool invocation pipeline: the only path allowed to run a tool.

Order, per step: the tool must be registered, then present in the caller's
``context.allowed_tools`` (else ``ToolBlockedError``); raw arguments are
validated against the tool's input model with ``extra="forbid"`` (else
``ToolArgumentError``); the implementation runs under
``asyncio.wait_for(timeout_seconds)`` (a timeout becomes a retryable
``ToolExecutionError``); the return value is validated against the tool's
output model (else ``ToolOutputError``). Failure classification happens
here, once, never inside a tool implementation. Every outcome -- success or
failure -- is reported to ``context.trace`` as a ``ToolCallRecord`` that
never carries raw exception text.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from northforge.tools.context import ToolCallRecord, ToolContext
from northforge.tools.errors import (
    ToolArgumentError,
    ToolBlockedError,
    ToolError,
    ToolExecutionError,
    ToolOutputError,
)
from northforge.tools.registry import ToolRegistry


@dataclass(frozen=True)
class ToolInvocation:
    """The validated result of a successful tool call."""

    name: str
    args: dict[str, Any]
    output: dict[str, Any]
    duration_ms: float
    attempt: int = 1


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


async def invoke_tool(
    registry: ToolRegistry,
    name: str,
    raw_args: dict[str, Any],
    context: ToolContext,
    *,
    timeout_seconds: float = 30,
) -> ToolInvocation:
    started = time.perf_counter()

    def _trace(outcome: str, args: dict[str, Any]) -> None:
        if context.trace is not None:
            context.trace(
                ToolCallRecord(
                    name=name,
                    args=args,
                    outcome=outcome,
                    duration_ms=_elapsed_ms(started),
                )
            )

    if name not in registry.names():
        _trace(ToolBlockedError.code, {})
        raise ToolBlockedError(f"tool is not registered: {name}")

    if name not in context.allowed_tools:
        _trace(ToolBlockedError.code, {})
        raise ToolBlockedError(f"tool is not permitted for this step: {name}")

    spec, implementation = registry.get(name)

    try:
        validated_args = spec.input_model.model_validate(raw_args)
    except ValidationError as exc:
        _trace(ToolArgumentError.code, {})
        raise ToolArgumentError(f"invalid arguments for tool {name}") from exc

    args_dump = validated_args.model_dump(mode="json")

    try:
        raw_output = await asyncio.wait_for(
            implementation(validated_args, context), timeout=timeout_seconds
        )
    except TimeoutError as exc:
        _trace(ToolExecutionError.code, args_dump)
        raise ToolExecutionError(
            f"tool timed out: {name}", retryable=True, failure_category="timeout"
        ) from exc
    except ToolError as exc:
        _trace(exc.code, args_dump)
        raise
    except Exception as exc:
        _trace(ToolExecutionError.code, args_dump)
        raise ToolExecutionError(
            f"tool raised an unexpected error: {name}", retryable=False
        ) from exc

    try:
        validated_output = spec.output_model.model_validate(raw_output)
    except ValidationError as exc:
        _trace(ToolOutputError.code, args_dump)
        raise ToolOutputError(f"tool output failed validation: {name}") from exc

    output_dump = validated_output.model_dump(mode="json")
    _trace("ok", args_dump)

    return ToolInvocation(
        name=name,
        args=args_dump,
        output=output_dump,
        duration_ms=_elapsed_ms(started),
        attempt=1,
    )
