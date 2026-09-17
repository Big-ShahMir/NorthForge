"""Tests for the hosted-NVIDIA ``ModelProvider`` adapter.

All HTTP is faked with ``httpx2.MockTransport``; nothing here reaches the
network. ``NVIDIA_API_KEY`` is set to an obviously-fake value
(``nvapi-test-secret``) purely so the key is distinctive enough to search
for in logs, reprs, and exception text.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

import httpx2 as httpx
import pytest
from pydantic import BaseModel

from northforge.core.config import Settings, load_settings
from northforge.core.errors import ConfigurationError
from northforge.providers.base import ModelProvider
from northforge.providers.capabilities import CapabilityRegistry
from northforge.providers.errors import (
    ProviderAuthError,
    ProviderCapabilityError,
    ProviderRateLimitedError,
    ProviderRequestError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from northforge.providers.nvidia import NvidiaProvider
from northforge.providers.types import (
    EmbeddingRequest,
    GenerationRequest,
    Message,
    RerankRequest,
    ToolCallRequest,
    ToolDefinition,
)
from tests.conftest import UNIT_DATABASE_URL, UNIT_REDIS_URL, set_unit_s3_env

API_KEY = "nvapi-test-secret"

# Models drawn from the real default catalog so registry lookups are realistic.
PLANNER_MODEL = "nvidia/nemotron-3-super-120b-a12b"  # structured, tools, reasoning toggle
NON_REASONING_MODEL = "moonshotai/kimi-k3"  # structured, tools, no reasoning toggle
EMBED_MODEL = "nvidia/nemotron-3-embed-1b"  # no tools, no structured output
RERANK_MODEL = "nvidia/llama-nemotron-rerank-vl-1b-v2"
UNKNOWN_MODEL = "some-vendor/unlisted-model"


class Ping(BaseModel):
    answer: str
    number: int


@pytest.fixture
def nvidia_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", UNIT_DATABASE_URL)
    monkeypatch.setenv("REDIS_URL", UNIT_REDIS_URL)
    monkeypatch.setenv("READINESS_TIMEOUT_SECONDS", "0.5")
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("NVIDIA_API_KEY", API_KEY)
    set_unit_s3_env(monkeypatch)
    return load_settings(env_file=None)


@pytest.fixture
def settings_without_key(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", UNIT_DATABASE_URL)
    monkeypatch.setenv("REDIS_URL", UNIT_REDIS_URL)
    monkeypatch.setenv("READINESS_TIMEOUT_SECONDS", "0.5")
    monkeypatch.setenv("AUTH_MODE", "dev")
    set_unit_s3_env(monkeypatch)
    return load_settings(env_file=None)


@pytest.fixture
def registry() -> CapabilityRegistry:
    return CapabilityRegistry.load()


Handler = Callable[[httpx.Request], httpx.Response]


def make_provider(
    settings: Settings, registry: CapabilityRegistry, handler: Handler
) -> NvidiaProvider:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return NvidiaProvider(settings, registry, client=client)


def chat_response(
    *,
    content: str | None = "hello",
    tool_calls: list[dict[str, Any]] | None = None,
    request_id: str | None = "resp-id-1",
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
    finish_reason: str = "stop",
    status_code: int = 200,
    header_request_id: str | None = "hdr-req-1",
) -> httpx.Response:
    message: dict[str, Any] = {"content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    body = {
        "id": request_id,
        "choices": [{"message": message, "finish_reason": finish_reason}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }
    headers = {"x-request-id": header_request_id} if header_request_id else {}
    return httpx.Response(status_code, json=body, headers=headers)


def _simple_request(*, content: str = "hi", **kwargs: object) -> GenerationRequest:
    return GenerationRequest(messages=[Message(role="user", content=content)], **kwargs)  # type: ignore[arg-type]


# -- construction -----------------------------------------------------------------


def test_missing_api_key_raises_configuration_error(
    settings_without_key: Settings, registry: CapabilityRegistry
) -> None:
    with pytest.raises(ConfigurationError):
        NvidiaProvider(settings_without_key, registry)


def test_is_a_model_provider(nvidia_settings: Settings, registry: CapabilityRegistry) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return chat_response()

    provider = make_provider(nvidia_settings, registry, handler)
    assert isinstance(provider, ModelProvider)
    assert provider.name == "nvidia"


def test_repr_never_contains_api_key(
    nvidia_settings: Settings, registry: CapabilityRegistry
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return chat_response()

    provider = make_provider(nvidia_settings, registry, handler)
    assert API_KEY not in repr(provider)


# -- request building: headers, body, messages -------------------------------------


@pytest.mark.asyncio
async def test_generate_sends_bearer_header_and_model(
    nvidia_settings: Settings, registry: CapabilityRegistry
) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["body"] = json.loads(request.content)
        captured["url"] = str(request.url)
        return chat_response()

    provider = make_provider(nvidia_settings, registry, handler)
    await provider.generate(_simple_request(), PLANNER_MODEL)

    headers = captured["headers"]
    assert headers["authorization"] == f"Bearer {API_KEY}"
    assert headers["accept"] == "application/json"
    assert headers["content-type"] == "application/json"
    body = captured["body"]
    assert body["model"] == PLANNER_MODEL
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert captured["url"].endswith("/chat/completions")


@pytest.mark.asyncio
async def test_message_mapping_round_trips_tool_calls(
    nvidia_settings: Settings, registry: CapabilityRegistry
) -> None:
    captured_body: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
        return chat_response()

    provider = make_provider(nvidia_settings, registry, handler)
    request = GenerationRequest(
        messages=[
            Message(role="user", content="find the policy"),
            Message(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCallRequest(id="call-1", name="lookup_policy", arguments={"q": "x"})
                ],
            ),
            Message(
                role="tool", content="result text", tool_call_id="call-1", name="lookup_policy"
            ),
        ]
    )
    await provider.generate(request, PLANNER_MODEL)

    messages = captured_body["messages"]
    assert messages[0] == {"role": "user", "content": "find the policy"}
    assert messages[1]["role"] == "assistant"
    assert messages[1]["tool_calls"] == [
        {
            "id": "call-1",
            "type": "function",
            "function": {"name": "lookup_policy", "arguments": json.dumps({"q": "x"})},
        }
    ]
    assert messages[2] == {
        "role": "tool",
        "content": "result text",
        "tool_call_id": "call-1",
        "name": "lookup_policy",
    }


@pytest.mark.asyncio
async def test_per_request_timeout_override_reaches_client(
    nvidia_settings: Settings, registry: CapabilityRegistry
) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["timeout"] = request.extensions["timeout"]
        return chat_response()

    provider = make_provider(nvidia_settings, registry, handler)
    await provider.generate(_simple_request(timeout_seconds=1.5), PLANNER_MODEL)
    assert captured["timeout"]["read"] == 1.5


# -- structured output modes --------------------------------------------------------


@pytest.mark.asyncio
async def test_structured_output_json_schema_mode(
    nvidia_settings: Settings, registry: CapabilityRegistry
) -> None:
    captured_body: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
        return chat_response(content='{"answer": "pong", "number": 7}')

    provider = make_provider(nvidia_settings, registry, handler)
    response = await provider.generate_structured(
        _simple_request(), PLANNER_MODEL, Ping, mode="json_schema"
    )
    assert response.parsed == Ping(answer="pong", number=7)
    assert captured_body["response_format"]["type"] == "json_schema"
    assert captured_body["response_format"]["json_schema"]["name"] == "Ping"
    assert captured_body["response_format"]["json_schema"]["strict"] is True
    assert "nvext" not in captured_body


@pytest.mark.asyncio
async def test_structured_output_nvext_guided_json_mode(
    nvidia_settings: Settings, registry: CapabilityRegistry
) -> None:
    captured_body: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
        return chat_response(content='{"answer": "pong", "number": 7}')

    provider = make_provider(nvidia_settings, registry, handler)
    response = await provider.generate_structured(
        _simple_request(), PLANNER_MODEL, Ping, mode="nvext_guided_json"
    )
    assert response.parsed == Ping(answer="pong", number=7)
    assert "response_format" not in captured_body
    assert captured_body["nvext"]["guided_json"] == Ping.model_json_schema()


@pytest.mark.asyncio
async def test_structured_output_prompt_only_mode_adds_system_instruction(
    nvidia_settings: Settings, registry: CapabilityRegistry
) -> None:
    captured_body: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
        return chat_response(content='{"answer": "pong", "number": 7}')

    provider = make_provider(nvidia_settings, registry, handler)
    response = await provider.generate_structured(
        _simple_request(), PLANNER_MODEL, Ping, mode="prompt_only"
    )
    assert response.parsed == Ping(answer="pong", number=7)
    assert "response_format" not in captured_body
    assert "nvext" not in captured_body
    messages = captured_body["messages"]
    assert messages[0]["role"] == "system"
    assert "answer" in messages[0]["content"]


@pytest.mark.asyncio
async def test_structured_output_unsupported_model_raises_capability_error(
    nvidia_settings: Settings, registry: CapabilityRegistry
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return chat_response()

    provider = make_provider(nvidia_settings, registry, handler)
    with pytest.raises(ProviderCapabilityError):
        await provider.generate_structured(_simple_request(), EMBED_MODEL, Ping)


@pytest.mark.asyncio
async def test_structured_output_uses_repair_loop(
    nvidia_settings: Settings, registry: CapabilityRegistry
) -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return chat_response(content="not json at all")
        return chat_response(content='{"answer": "pong", "number": 7}')

    provider = make_provider(nvidia_settings, registry, handler)
    response = await provider.generate_structured(_simple_request(), PLANNER_MODEL, Ping)
    assert response.parsed == Ping(answer="pong", number=7)
    assert call_count == 2
    assert "structured output repaired once" in response.warnings


# -- reasoning toggle -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_reasoning_toggle_sent_when_model_supports_it(
    nvidia_settings: Settings, registry: CapabilityRegistry
) -> None:
    captured_body: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
        return chat_response()

    provider = make_provider(nvidia_settings, registry, handler)
    response = await provider.generate(_simple_request(reasoning=True), PLANNER_MODEL)
    assert captured_body["chat_template_kwargs"] == {"enable_thinking": True}
    assert response.warnings == []


@pytest.mark.asyncio
async def test_reasoning_toggle_warns_when_model_does_not_support_it(
    nvidia_settings: Settings, registry: CapabilityRegistry
) -> None:
    captured_body: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
        return chat_response()

    provider = make_provider(nvidia_settings, registry, handler)
    response = await provider.generate(_simple_request(reasoning=True), NON_REASONING_MODEL)
    assert "chat_template_kwargs" not in captured_body
    assert any("reasoning" in warning for warning in response.warnings)


@pytest.mark.asyncio
async def test_reasoning_toggle_not_sent_when_not_requested(
    nvidia_settings: Settings, registry: CapabilityRegistry
) -> None:
    captured_body: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
        return chat_response()

    provider = make_provider(nvidia_settings, registry, handler)
    await provider.generate(_simple_request(), PLANNER_MODEL)
    assert "chat_template_kwargs" not in captured_body


# -- tools ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_call_sends_tools_and_tool_choice(
    nvidia_settings: Settings, registry: CapabilityRegistry
) -> None:
    captured_body: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
        return chat_response(
            content=None,
            tool_calls=[
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "lookup_policy", "arguments": '{"q": "refunds"}'},
                }
            ],
        )

    provider = make_provider(nvidia_settings, registry, handler)
    tools = [
        ToolDefinition(
            name="lookup_policy", description="look up a policy", parameters={"type": "object"}
        )
    ]
    request = _simple_request(tool_choice="required")
    response = await provider.tool_call(request, PLANNER_MODEL, tools)

    assert captured_body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "lookup_policy",
                "description": "look up a policy",
                "parameters": {"type": "object"},
            },
        }
    ]
    assert captured_body["tool_choice"] == "required"
    assert response.tool_calls == [
        ToolCallRequest(id="call-1", name="lookup_policy", arguments={"q": "refunds"})
    ]


@pytest.mark.asyncio
async def test_tool_call_unsupported_model_raises_capability_error(
    nvidia_settings: Settings, registry: CapabilityRegistry
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return chat_response()

    provider = make_provider(nvidia_settings, registry, handler)
    with pytest.raises(ProviderCapabilityError):
        await provider.tool_call(_simple_request(), EMBED_MODEL, [])


@pytest.mark.asyncio
async def test_unknown_model_generate_works_but_structured_and_tools_are_rejected(
    nvidia_settings: Settings, registry: CapabilityRegistry
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return chat_response()

    provider = make_provider(nvidia_settings, registry, handler)
    response = await provider.generate(_simple_request(), UNKNOWN_MODEL)
    assert response.content == "hello"
    with pytest.raises(ProviderCapabilityError):
        await provider.generate_structured(_simple_request(), UNKNOWN_MODEL, Ping)
    with pytest.raises(ProviderCapabilityError):
        await provider.tool_call(_simple_request(), UNKNOWN_MODEL, [])


# -- response parsing -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_parses_content_usage_and_finish_reason(
    nvidia_settings: Settings, registry: CapabilityRegistry
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return chat_response(
            content="the answer",
            prompt_tokens=11,
            completion_tokens=3,
            finish_reason="stop",
            header_request_id="hdr-req-9",
        )

    provider = make_provider(nvidia_settings, registry, handler)
    response = await provider.generate(_simple_request(), PLANNER_MODEL)
    assert response.content == "the answer"
    assert response.usage.prompt_tokens == 11
    assert response.usage.completion_tokens == 3
    assert response.usage.total_tokens == 14
    assert response.finish_reason == "stop"
    assert response.request_id == "hdr-req-9"


@pytest.mark.asyncio
async def test_generate_strips_think_blocks_from_content(
    nvidia_settings: Settings, registry: CapabilityRegistry
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return chat_response(content="<think>pondering</think>the real answer")

    provider = make_provider(nvidia_settings, registry, handler)
    response = await provider.generate(_simple_request(), PLANNER_MODEL)
    assert response.content == "the real answer"


@pytest.mark.asyncio
async def test_request_id_falls_back_to_body_id_when_header_absent(
    nvidia_settings: Settings, registry: CapabilityRegistry
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return chat_response(request_id="body-id-7", header_request_id=None)

    provider = make_provider(nvidia_settings, registry, handler)
    response = await provider.generate(_simple_request(), PLANNER_MODEL)
    assert response.request_id == "body-id-7"


@pytest.mark.asyncio
async def test_tool_calls_decoded_with_json_arguments(
    nvidia_settings: Settings, registry: CapabilityRegistry
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return chat_response(
            content=None,
            tool_calls=[
                {
                    "id": "call-9",
                    "type": "function",
                    "function": {"name": "lookup_policy", "arguments": '{"q": "vacation"}'},
                }
            ],
        )

    provider = make_provider(nvidia_settings, registry, handler)
    response = await provider.generate(_simple_request(), PLANNER_MODEL)
    assert response.tool_calls == [
        ToolCallRequest(id="call-9", name="lookup_policy", arguments={"q": "vacation"})
    ]


# -- embeddings -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_builds_request_and_parses_ordered_vectors(
    nvidia_settings: Settings, registry: CapabilityRegistry
) -> None:
    captured_body: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
        texts = captured_body["input"]
        # Return items out of order to verify the provider sorts by index.
        data = [
            {"index": 1, "embedding": [0.2] * 4},
            {"index": 0, "embedding": [0.1] * 4},
        ][: len(texts)]
        return httpx.Response(
            200,
            json={"data": data, "usage": {"prompt_tokens": 4, "total_tokens": 4}},
        )

    provider = make_provider(nvidia_settings, registry, handler)
    response = await provider.embed(
        EmbeddingRequest(texts=["a", "b"], input_type="query"), EMBED_MODEL
    )
    assert captured_body["model"] == EMBED_MODEL
    assert captured_body["input_type"] == "query"
    assert captured_body["encoding_format"] == "float"
    assert captured_body["truncate"] == "END"
    assert response.vectors == [[0.1] * 4, [0.2] * 4]
    assert response.dimensions == 4


@pytest.mark.asyncio
async def test_embed_chunks_at_32_texts_per_request(
    nvidia_settings: Settings, registry: CapabilityRegistry
) -> None:
    request_bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        request_bodies.append(body)
        data = [{"index": i, "embedding": [1.0]} for i in range(len(body["input"]))]
        return httpx.Response(
            200, json={"data": data, "usage": {"prompt_tokens": 1, "total_tokens": 1}}
        )

    provider = make_provider(nvidia_settings, registry, handler)
    texts = [f"text-{i}" for i in range(70)]
    response = await provider.embed(EmbeddingRequest(texts=texts), EMBED_MODEL)

    assert len(request_bodies) == 3
    assert [len(b["input"]) for b in request_bodies] == [32, 32, 6]
    assert len(response.vectors) == 70


@pytest.mark.asyncio
async def test_embed_warns_when_dimensions_differ_from_catalog(
    nvidia_settings: Settings, registry: CapabilityRegistry
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}],
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            },
        )

    provider = make_provider(nvidia_settings, registry, handler)
    response = await provider.embed(EmbeddingRequest(texts=["hi"]), EMBED_MODEL)
    assert response.dimensions == 3
    assert any("dimensions" in warning for warning in response.warnings)


# -- rerank -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rerank_builds_url_and_sorts_by_score(
    nvidia_settings: Settings, registry: CapabilityRegistry
) -> None:
    captured_url: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_url["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "rankings": [
                    {"index": 0, "logit": 0.1},
                    {"index": 1, "logit": 0.9},
                    {"index": 2, "logit": 0.5},
                ]
            },
        )

    provider = make_provider(nvidia_settings, registry, handler)
    response = await provider.rerank(
        RerankRequest(query="q", passages=["a", "b", "c"]), RERANK_MODEL
    )
    assert captured_url["url"].endswith(f"/retrieval/{RERANK_MODEL}/reranking")
    assert [r.index for r in response.rankings] == [1, 2, 0]
    assert [r.score for r in response.rankings] == [0.9, 0.5, 0.1]


@pytest.mark.asyncio
async def test_rerank_applies_top_n(
    nvidia_settings: Settings, registry: CapabilityRegistry
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "rankings": [
                    {"index": 0, "logit": 0.1},
                    {"index": 1, "logit": 0.9},
                    {"index": 2, "logit": 0.5},
                ]
            },
        )

    provider = make_provider(nvidia_settings, registry, handler)
    response = await provider.rerank(
        RerankRequest(query="q", passages=["a", "b", "c"], top_n=1), RERANK_MODEL
    )
    assert len(response.rankings) == 1
    assert response.rankings[0].index == 1


# -- error mapping ------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected_error"),
    [
        (400, ProviderRequestError),
        (404, ProviderRequestError),
        (422, ProviderRequestError),
        (401, ProviderAuthError),
        (403, ProviderAuthError),
        (408, ProviderUnavailableError),
        (500, ProviderUnavailableError),
        (503, ProviderUnavailableError),
    ],
)
async def test_status_code_error_mapping(
    nvidia_settings: Settings,
    registry: CapabilityRegistry,
    status_code: int,
    expected_error: type[Exception],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": "nope"})

    provider = make_provider(nvidia_settings, registry, handler)
    with pytest.raises(expected_error) as excinfo:
        await provider.generate(_simple_request(), PLANNER_MODEL)
    assert API_KEY not in str(excinfo.value)


@pytest.mark.asyncio
async def test_429_parses_numeric_retry_after(
    nvidia_settings: Settings, registry: CapabilityRegistry
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "slow down"}, headers={"retry-after": "7"})

    provider = make_provider(nvidia_settings, registry, handler)
    with pytest.raises(ProviderRateLimitedError) as excinfo:
        await provider.generate(_simple_request(), PLANNER_MODEL)
    assert excinfo.value.retry_after_seconds == 7.0


@pytest.mark.asyncio
async def test_429_non_numeric_retry_after_is_none(
    nvidia_settings: Settings, registry: CapabilityRegistry
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"error": "slow down"},
            headers={"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"},
        )

    provider = make_provider(nvidia_settings, registry, handler)
    with pytest.raises(ProviderRateLimitedError) as excinfo:
        await provider.generate(_simple_request(), PLANNER_MODEL)
    assert excinfo.value.retry_after_seconds is None


@pytest.mark.asyncio
async def test_timeout_exception_maps_to_provider_timeout_error(
    nvidia_settings: Settings, registry: CapabilityRegistry
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    provider = make_provider(nvidia_settings, registry, handler)
    with pytest.raises(ProviderTimeoutError):
        await provider.generate(_simple_request(), PLANNER_MODEL)


@pytest.mark.asyncio
async def test_connect_error_maps_to_provider_unavailable_error(
    nvidia_settings: Settings, registry: CapabilityRegistry
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    provider = make_provider(nvidia_settings, registry, handler)
    with pytest.raises(ProviderUnavailableError):
        await provider.generate(_simple_request(), PLANNER_MODEL)


@pytest.mark.asyncio
async def test_2xx_non_json_body_is_unavailable(
    nvidia_settings: Settings, registry: CapabilityRegistry
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json at all")

    provider = make_provider(nvidia_settings, registry, handler)
    with pytest.raises(ProviderUnavailableError):
        await provider.generate(_simple_request(), PLANNER_MODEL)


@pytest.mark.asyncio
async def test_2xx_missing_expected_key_is_unavailable(
    nvidia_settings: Settings, registry: CapabilityRegistry
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    provider = make_provider(nvidia_settings, registry, handler)
    with pytest.raises(ProviderUnavailableError):
        await provider.generate(_simple_request(), PLANNER_MODEL)


# -- secrecy: the API key must never leak ------------------------------------------------


@pytest.mark.asyncio
async def test_api_key_never_appears_in_logs(
    nvidia_settings: Settings, registry: CapabilityRegistry, caplog: pytest.LogCaptureFixture
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return chat_response()

    provider = make_provider(nvidia_settings, registry, handler)
    with caplog.at_level(logging.DEBUG, logger="northforge.providers.nvidia"):
        await provider.generate(_simple_request(), PLANNER_MODEL)
    assert API_KEY not in caplog.text


@pytest.mark.asyncio
async def test_api_key_never_appears_in_logs_on_error(
    nvidia_settings: Settings, registry: CapabilityRegistry, caplog: pytest.LogCaptureFixture
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "bad request body"}})

    provider = make_provider(nvidia_settings, registry, handler)
    with caplog.at_level(logging.DEBUG, logger="northforge.providers.nvidia"):
        with pytest.raises(ProviderRequestError):
            await provider.generate(_simple_request(), PLANNER_MODEL)
    assert API_KEY not in caplog.text


@pytest.mark.asyncio
async def test_debug_log_never_includes_message_content(
    nvidia_settings: Settings, registry: CapabilityRegistry, caplog: pytest.LogCaptureFixture
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return chat_response()

    provider = make_provider(nvidia_settings, registry, handler)
    distinctive_prompt = "the distinctive prompt content"
    with caplog.at_level(logging.DEBUG, logger="northforge.providers.nvidia"):
        await provider.generate(_simple_request(content=distinctive_prompt), PLANNER_MODEL)
    assert distinctive_prompt not in caplog.text
    assert any("message_count" in record.__dict__ for record in caplog.records)
