"""Tests for provider-agnostic structured-output parsing and repair."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from northforge.providers.errors import ProviderMalformedOutputError
from northforge.providers.structured import (
    StructuredParseError,
    build_repair_request,
    extract_json,
    generate_with_repair,
    parse_structured,
    schema_instruction,
    strip_reasoning,
    with_schema_instruction,
)
from northforge.providers.types import GenerationRequest, Message, ModelResponse


class Ping(BaseModel):
    answer: str
    number: int


def _request(*, content: str = "say pong") -> GenerationRequest:
    return GenerationRequest(messages=[Message(role="user", content=content)])


def _response(content: str) -> ModelResponse[None]:
    return ModelResponse[None](model="m", provider="mock", latency_ms=1.0, content=content)


# -- strip_reasoning ----------------------------------------------------------


def test_strip_reasoning_removes_think_block() -> None:
    text = '<think>pondering things</think>{"answer": "pong", "number": 7}'
    assert "<think>" not in strip_reasoning(text)
    assert "pondering" not in strip_reasoning(text)


def test_strip_reasoning_removes_multiline_think_block() -> None:
    text = '<think>\nline one\nline two\n</think>\n{"a": 1}'
    result = strip_reasoning(text)
    assert "line one" not in result
    assert '{"a": 1}' in result


def test_strip_reasoning_leaves_text_without_think_block_unchanged() -> None:
    text = '{"answer": "pong", "number": 7}'
    assert strip_reasoning(text) == text


# -- extract_json ---------------------------------------------------------------


def test_extract_json_from_fenced_block() -> None:
    text = 'Here you go:\n```json\n{"answer": "pong", "number": 7}\n```\nHope that helps.'
    assert extract_json(text) == '{"answer": "pong", "number": 7}'


def test_extract_json_from_plain_fence_without_json_tag() -> None:
    text = '```\n{"answer": "pong", "number": 7}\n```'
    assert extract_json(text) == '{"answer": "pong", "number": 7}'


def test_extract_json_strips_think_block_first() -> None:
    text = '<think>reasoning...</think>{"answer": "pong", "number": 7}'
    assert extract_json(text) == '{"answer": "pong", "number": 7}'


def test_extract_json_with_prose_around_it() -> None:
    text = 'Sure, the answer is: {"answer": "pong", "number": 7} -- let me know if you need more.'
    assert extract_json(text) == '{"answer": "pong", "number": 7}'


def test_extract_json_handles_nested_braces_in_strings() -> None:
    text = '{"answer": "pong {nested} and {more} braces", "number": 7}'
    assert extract_json(text) == text


def test_extract_json_handles_escaped_quotes_in_strings() -> None:
    text = '{"answer": "she said \\"hi\\"", "number": 7}'
    assert extract_json(text) == text


def test_extract_json_returns_array_when_that_is_the_top_level_value() -> None:
    text = 'prefix [1, 2, {"a": 3}] suffix'
    assert extract_json(text) == '[1, 2, {"a": 3}]'


def test_extract_json_raises_when_no_json_present() -> None:
    with pytest.raises(StructuredParseError) as excinfo:
        extract_json("no json here at all")
    assert excinfo.value.raw_text == "no json here at all"
    assert excinfo.value.detail


# -- parse_structured -------------------------------------------------------------


def test_parse_structured_success() -> None:
    result = parse_structured('{"answer": "pong", "number": 7}', Ping)
    assert result == Ping(answer="pong", number=7)


def test_parse_structured_raises_on_schema_mismatch() -> None:
    with pytest.raises(StructuredParseError) as excinfo:
        parse_structured('{"answer": "pong"}', Ping)
    assert excinfo.value.detail
    assert len(excinfo.value.detail) <= 500


def test_parse_structured_raises_when_no_json_found() -> None:
    with pytest.raises(StructuredParseError):
        parse_structured("not json at all", Ping)


# -- schema_instruction / with_schema_instruction --------------------------------


def test_schema_instruction_mentions_json() -> None:
    instruction = schema_instruction(Ping)
    assert "JSON" in instruction
    assert "answer" in instruction


def test_with_schema_instruction_creates_system_message_when_absent() -> None:
    request = _request()
    updated = with_schema_instruction(request, Ping)
    assert updated.messages[0].role == "system"
    assert "answer" in updated.messages[0].content
    assert updated.messages[1].role == "user"
    # original untouched (frozen request)
    assert request.messages[0].role == "user"


def test_with_schema_instruction_extends_existing_system_message() -> None:
    request = GenerationRequest(
        messages=[
            Message(role="system", content="Be polite."),
            Message(role="user", content="hi"),
        ]
    )
    updated = with_schema_instruction(request, Ping)
    assert updated.messages[0].role == "system"
    assert "Be polite." in updated.messages[0].content
    assert "answer" in updated.messages[0].content
    assert len(updated.messages) == 2


# -- build_repair_request ------------------------------------------------------------


def test_build_repair_request_appends_assistant_and_user_turns() -> None:
    request = _request()
    repaired = build_repair_request(request, "{bad", "field required", Ping)
    assert repaired.messages[-2].role == "assistant"
    assert repaired.messages[-2].content == "{bad"
    assert repaired.messages[-1].role == "user"
    assert "field required" in repaired.messages[-1].content
    assert repaired.temperature == 0.0


# -- generate_with_repair -----------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_with_repair_succeeds_first_try() -> None:
    async def call(_req: GenerationRequest) -> ModelResponse[None]:
        return _response('{"answer": "pong", "number": 7}')

    result = await generate_with_repair(call, _request(), Ping)
    assert result.parsed == Ping(answer="pong", number=7)
    assert result.content == '{"answer": "pong", "number": 7}'
    assert "structured output repaired once" not in result.warnings


@pytest.mark.asyncio
async def test_generate_with_repair_succeeds_on_second_try() -> None:
    attempts: list[GenerationRequest] = []

    async def call(req: GenerationRequest) -> ModelResponse[None]:
        attempts.append(req)
        if len(attempts) == 1:
            return _response("not json at all")
        return _response('{"answer": "pong", "number": 7}')

    result = await generate_with_repair(call, _request(), Ping)
    assert result.parsed == Ping(answer="pong", number=7)
    assert "structured output repaired once" in result.warnings
    assert len(attempts) == 2
    # the repair request carries the original bad output and an error message
    assert attempts[1].messages[-2].content == "not json at all"


@pytest.mark.asyncio
async def test_generate_with_repair_raises_malformed_output_after_second_failure() -> None:
    call_count = 0

    async def call(_req: GenerationRequest) -> ModelResponse[None]:
        nonlocal call_count
        call_count += 1
        return _response(f"still not json #{call_count}")

    with pytest.raises(ProviderMalformedOutputError) as excinfo:
        await generate_with_repair(call, _request(), Ping)

    assert call_count == 2
    assert excinfo.value.raw_text == "still not json #2"
    assert "still not json" not in str(excinfo.value)
    assert "#2" not in str(excinfo.value)


@pytest.mark.asyncio
async def test_generate_with_repair_preserves_other_response_fields() -> None:
    async def call(_req: GenerationRequest) -> ModelResponse[None]:
        return ModelResponse[None](
            model="my-model",
            provider="mock",
            latency_ms=42.0,
            request_id="req-1",
            content='{"answer": "pong", "number": 7}',
        )

    result = await generate_with_repair(call, _request(), Ping)
    assert result.model == "my-model"
    assert result.provider == "mock"
    assert result.request_id == "req-1"
    assert result.latency_ms == 42.0
