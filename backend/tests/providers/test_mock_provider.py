"""Tests for the in-process, scriptable ``MockProvider``."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from northforge.providers.base import ModelProvider
from northforge.providers.errors import (
    ProviderAuthError,
    ProviderMalformedOutputError,
    ProviderRateLimitedError,
    ProviderRequestError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from northforge.providers.mock import MockCall, MockProvider, ScriptedFailure
from northforge.providers.types import (
    EmbeddingRequest,
    GenerationRequest,
    Message,
    RerankRequest,
    ToolCallRequest,
    ToolDefinition,
)


class Ping(BaseModel):
    answer: str
    number: int


def _gen_request(content: str = "hello there") -> GenerationRequest:
    return GenerationRequest(messages=[Message(role="user", content=content)])


def test_mock_provider_is_a_model_provider() -> None:
    provider = MockProvider()
    assert isinstance(provider, ModelProvider)
    assert provider.name == "mock"


# -- generate -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_default_echoes_last_user_message() -> None:
    provider = MockProvider()
    response = await provider.generate(_gen_request("what is up"), "any-model")
    assert response.content == "mock:what is up"
    assert response.model == "any-model"
    assert response.provider == "mock"


@pytest.mark.asyncio
async def test_generate_consumes_scripted_responses_in_order() -> None:
    provider = MockProvider(responses={"generate": ["first", "second"]})
    first = await provider.generate(_gen_request(), "m")
    second = await provider.generate(_gen_request(), "m")
    third = await provider.generate(_gen_request("last one"), "m")
    assert first.content == "first"
    assert second.content == "second"
    assert third.content == "mock:last one"


# -- generate_structured ----------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_structured_scripted_model_instance() -> None:
    provider = MockProvider(responses={"generate_structured": [Ping(answer="pong", number=7)]})
    response = await provider.generate_structured(_gen_request(), "m", Ping)
    assert response.parsed == Ping(answer="pong", number=7)


@pytest.mark.asyncio
async def test_generate_structured_scripted_dict() -> None:
    provider = MockProvider(responses={"generate_structured": [{"answer": "pong", "number": 7}]})
    response = await provider.generate_structured(_gen_request(), "m", Ping)
    assert response.parsed == Ping(answer="pong", number=7)


@pytest.mark.asyncio
async def test_generate_structured_default_uses_schema_defaults() -> None:
    class WithDefault(BaseModel):
        answer: str = "default"
        number: int = 0

    provider = MockProvider()
    response = await provider.generate_structured(_gen_request(), "m", WithDefault)
    assert response.parsed == WithDefault()


@pytest.mark.asyncio
async def test_generate_structured_default_raises_when_schema_has_no_defaults() -> None:
    provider = MockProvider()
    with pytest.raises(ProviderMalformedOutputError):
        await provider.generate_structured(_gen_request(), "m", Ping)


@pytest.mark.asyncio
async def test_generate_structured_scripted_bad_string_triggers_repair_then_good_value() -> None:
    provider = MockProvider(
        responses={"generate_structured": ["{not json", Ping(answer="pong", number=7)]}
    )
    response = await provider.generate_structured(_gen_request(), "m", Ping)
    assert response.parsed == Ping(answer="pong", number=7)
    assert "structured output repaired once" in response.warnings


@pytest.mark.asyncio
async def test_generate_structured_malformed_json_failure_triggers_repair_then_success() -> None:
    provider = MockProvider(
        failures=[ScriptedFailure(kind="malformed_json", operation="generate_structured")],
        responses={"generate_structured": [Ping(answer="pong", number=7)]},
    )
    response = await provider.generate_structured(_gen_request(), "m", Ping)
    assert response.parsed == Ping(answer="pong", number=7)
    assert "structured output repaired once" in response.warnings
    # two calls recorded: the malformed first attempt and the repair
    structured_calls = [c for c in provider.calls if c.operation == "generate_structured"]
    assert len(structured_calls) == 2


# -- tool_call --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_call_default_returns_no_tool_calls() -> None:
    provider = MockProvider()
    tools = [ToolDefinition(name="lookup", description="look things up", parameters={})]
    response = await provider.tool_call(_gen_request(), "m", tools)
    assert response.tool_calls == []


@pytest.mark.asyncio
async def test_tool_call_scripted_response() -> None:
    scripted = [ToolCallRequest(id="call-1", name="lookup", arguments={"q": "policy"})]
    provider = MockProvider(responses={"tool_call": [scripted]})
    response = await provider.tool_call(_gen_request(), "m", [])
    assert response.tool_calls == scripted


# -- failures ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failures_are_consumed_then_normal_response_resumes() -> None:
    provider = MockProvider(
        failures=[ScriptedFailure(kind="timeout", times=2)],
        responses={"generate": ["after failures"]},
    )
    with pytest.raises(ProviderTimeoutError):
        await provider.generate(_gen_request(), "m")
    with pytest.raises(ProviderTimeoutError):
        await provider.generate(_gen_request(), "m")
    response = await provider.generate(_gen_request(), "m")
    assert response.content == "after failures"


@pytest.mark.asyncio
async def test_failure_scoped_to_operation_and_model() -> None:
    provider = MockProvider(
        failures=[ScriptedFailure(kind="rate_limited", operation="embed", model="special")]
    )
    # different operation: unaffected
    response = await provider.generate(_gen_request(), "special")
    assert response.content is not None
    assert response.content.startswith("mock:")
    # different model: unaffected
    embed_response = await provider.embed(EmbeddingRequest(texts=["hi"]), "other-model")
    assert embed_response.dimensions == 2048
    # matching operation and model: fails
    with pytest.raises(ProviderRateLimitedError) as excinfo:
        await provider.embed(EmbeddingRequest(texts=["hi"]), "special")
    assert excinfo.value.retry_after_seconds == 0.01


@pytest.mark.asyncio
async def test_all_scripted_failure_kinds_map_to_expected_errors() -> None:
    kind_to_error = {
        "timeout": ProviderTimeoutError,
        "rate_limited": ProviderRateLimitedError,
        "server_error": ProviderUnavailableError,
        "auth": ProviderAuthError,
        "bad_request": ProviderRequestError,
    }
    for kind, expected in kind_to_error.items():
        provider = MockProvider(failures=[ScriptedFailure(kind=kind)])  # type: ignore[arg-type]
        with pytest.raises(expected):
            await provider.generate(_gen_request(), "m")


@pytest.mark.asyncio
async def test_malformed_json_failure_on_non_structured_operation_raises_directly() -> None:
    provider = MockProvider(failures=[ScriptedFailure(kind="malformed_json", operation="generate")])
    with pytest.raises(ProviderMalformedOutputError):
        await provider.generate(_gen_request(), "m")


# -- embeddings ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_is_deterministic_for_same_text() -> None:
    provider = MockProvider()
    response_a = await provider.embed(EmbeddingRequest(texts=["hello world"]), "m")
    response_b = await provider.embed(EmbeddingRequest(texts=["hello world"]), "m")
    assert response_a.vectors == response_b.vectors


@pytest.mark.asyncio
async def test_embed_differs_for_different_text() -> None:
    provider = MockProvider()
    response = await provider.embed(EmbeddingRequest(texts=["alpha", "beta"]), "m")
    assert response.vectors[0] != response.vectors[1]


@pytest.mark.asyncio
async def test_embed_respects_configured_dimensions() -> None:
    provider = MockProvider(embedding_dimensions=16)
    response = await provider.embed(EmbeddingRequest(texts=["hi"]), "m")
    assert response.dimensions == 16
    assert len(response.vectors[0]) == 16


@pytest.mark.asyncio
async def test_embed_vectors_are_l2_normalised() -> None:
    provider = MockProvider(embedding_dimensions=8)
    response = await provider.embed(EmbeddingRequest(texts=["hi"]), "m")
    norm = sum(v * v for v in response.vectors[0]) ** 0.5
    assert norm == pytest.approx(1.0, abs=1e-6)


# -- rerank -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rerank_orders_by_overlap_descending() -> None:
    provider = MockProvider()
    request = RerankRequest(
        query="cats and dogs",
        passages=[
            "a passage entirely about aardvarks",
            "cats and dogs living together",
            "dogs are mentioned here",
        ],
    )
    response = await provider.rerank(request, "m")
    assert response.rankings[0].index == 1
    scores = [r.score for r in response.rankings]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_rerank_applies_top_n() -> None:
    provider = MockProvider()
    request = RerankRequest(query="cats and dogs", passages=["cats", "dogs", "birds"], top_n=2)
    response = await provider.rerank(request, "m")
    assert len(response.rankings) == 2


# -- calls recorded -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_calls_are_recorded_including_failures() -> None:
    provider = MockProvider(failures=[ScriptedFailure(kind="timeout")])
    with pytest.raises(ProviderTimeoutError):
        await provider.generate(_gen_request(), "m")
    await provider.generate(_gen_request(), "m")
    assert len(provider.calls) == 2
    assert all(isinstance(call, MockCall) for call in provider.calls)
    assert provider.calls[0].operation == "generate"
    assert provider.calls[0].model == "m"


def test_count_tokens_returns_an_estimate() -> None:
    provider = MockProvider()
    assert provider.count_tokens("hello world", "m") is not None
