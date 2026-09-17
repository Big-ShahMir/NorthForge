"""Provider-agnostic structured-output parsing and repair.

Every provider that supports structured generation shares this module
instead of re-implementing JSON extraction or the one-shot repair loop:
``NvidiaProvider.generate_structured`` and ``MockProvider.generate_structured``
both build a ``call`` closure that sends one HTTP (or in-memory) request and
delegate the parse-then-maybe-repair sequence to ``generate_with_repair``.

Nothing here ever raises a provider error directly except
``ProviderMalformedOutputError``, which is the terminal failure after a
repair attempt has also failed to parse.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable

from pydantic import BaseModel, ValidationError

from northforge.providers.errors import ProviderMalformedOutputError
from northforge.providers.types import GenerationRequest, Message, ModelResponse

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
_DETAIL_MAX_CHARS = 500


class StructuredParseError(Exception):
    """Raised when model output could not be parsed against a schema.

    Kept internal to the parse/repair loop: ``generate_with_repair`` catches
    this and either retries once (repair request) or converts it to
    ``ProviderMalformedOutputError``. It is never raised across a provider
    boundary.
    """

    def __init__(self, *, raw_text: str, detail: str) -> None:
        super().__init__(detail)
        self.raw_text = raw_text
        self.detail = detail


def strip_reasoning(text: str) -> str:
    """Remove ``<think>...</think>`` reasoning blocks (multiline)."""
    return _THINK_RE.sub("", text)


def _strip_fences(text: str) -> str:
    """Return the content of the first fenced code block, if any, else ``text``."""
    match = _FENCE_RE.search(text)
    if match:
        return match.group(1)
    return text


def extract_json(text: str) -> str:
    """Extract the first balanced top-level JSON object or array from ``text``.

    Strips ``<think>`` blocks and code fences first, then scans for a ``{``
    or ``[`` from which the standard library's JSON tokenizer can decode a
    complete value -- this handles nested braces and escaped quotes inside
    JSON strings correctly, because it is the real JSON parser rather than a
    hand-rolled bracket counter.
    """
    stripped = _strip_fences(strip_reasoning(text))
    decoder = json.JSONDecoder()
    for index, char in enumerate(stripped):
        if char not in "{[":
            continue
        try:
            _, end = decoder.raw_decode(stripped, index)
        except json.JSONDecodeError:
            continue
        return stripped[index:end]
    raise StructuredParseError(raw_text=text, detail="no JSON object or array found in output")


def parse_structured[T: BaseModel](text: str, schema: type[T]) -> T:
    """Extract JSON from ``text`` and validate it against ``schema``."""
    json_text = extract_json(text)
    try:
        return schema.model_validate_json(json_text)
    except (ValidationError, ValueError) as exc:
        raise StructuredParseError(raw_text=text, detail=str(exc)[:_DETAIL_MAX_CHARS]) from exc


def schema_instruction(schema: type[BaseModel]) -> str:
    """Instruction text for ``prompt_only`` structured-output mode."""
    schema_json = json.dumps(schema.model_json_schema())
    return (
        "Reply with only a single JSON value matching this JSON Schema, with no prose, "
        f"no explanation, and no code fences: {schema_json}"
    )


def with_schema_instruction[T: BaseModel](
    request: GenerationRequest, schema: type[T]
) -> GenerationRequest:
    """Return a copy of ``request`` with the schema instruction added to the system message."""
    instruction = schema_instruction(schema)
    messages = list(request.messages)
    if messages and messages[0].role == "system":
        combined = f"{messages[0].content}\n\n{instruction}".strip()
        messages[0] = messages[0].model_copy(update={"content": combined})
    else:
        messages.insert(0, Message(role="system", content=instruction))
    return request.model_copy(update={"messages": messages})


def build_repair_request[T: BaseModel](
    request: GenerationRequest, raw_text: str, detail: str, schema: type[T]
) -> GenerationRequest:
    """Build the repair follow-up: original messages, the bad reply, and a correction ask."""
    del schema  # kept for signature symmetry with the rest of the parse/repair API
    messages = [
        *request.messages,
        Message(role="assistant", content=raw_text),
        Message(
            role="user",
            content=(
                f"Your previous reply did not match the required schema. Errors: {detail}. "
                "Reply with only the corrected JSON."
            ),
        ),
    ]
    return request.model_copy(update={"messages": messages, "temperature": 0.0})


def _with_parsed[T: BaseModel](
    response: ModelResponse[None],
    *,
    parsed: T,
    content: str,
    extra_warnings: list[str],
) -> ModelResponse[T]:
    return ModelResponse[T](
        model=response.model,
        provider=response.provider,
        latency_ms=response.latency_ms,
        request_id=response.request_id,
        usage=response.usage,
        warnings=[*response.warnings, *extra_warnings],
        cache_hit=response.cache_hit,
        attempts=response.attempts,
        fallback_used=response.fallback_used,
        content=content,
        parsed=parsed,
        tool_calls=response.tool_calls,
        finish_reason=response.finish_reason,
    )


async def generate_with_repair[T: BaseModel](
    call: Callable[[GenerationRequest], Awaitable[ModelResponse[None]]],
    request: GenerationRequest,
    schema: type[T],
) -> ModelResponse[T]:
    """Call ``call``, parse against ``schema``, and repair once on failure.

    On a second parse failure, raises ``ProviderMalformedOutputError`` with
    ``raw_text`` set from the repair attempt's output; the raw text is never
    included in the exception message.
    """
    response = await call(request)
    raw_text = response.content or ""
    try:
        parsed = parse_structured(raw_text, schema)
    except StructuredParseError as exc:
        repair_request = build_repair_request(request, exc.raw_text, exc.detail, schema)
        repair_response = await call(repair_request)
        repair_raw_text = repair_response.content or ""
        try:
            repaired = parse_structured(repair_raw_text, schema)
        except StructuredParseError as repair_exc:
            raise ProviderMalformedOutputError(raw_text=repair_exc.raw_text) from repair_exc
        return _with_parsed(
            repair_response,
            parsed=repaired,
            content=repair_raw_text,
            extra_warnings=["structured output repaired once"],
        )
    return _with_parsed(response, parsed=parsed, content=raw_text, extra_warnings=[])


__all__ = [
    "StructuredParseError",
    "build_repair_request",
    "extract_json",
    "generate_with_repair",
    "parse_structured",
    "schema_instruction",
    "strip_reasoning",
    "with_schema_instruction",
]
