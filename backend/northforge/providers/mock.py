"""In-process ``ModelProvider`` for tests and offline demos.

Deterministic by default (the same text always embeds to the same vector),
and scriptable two ways:

- ``responses`` queues exact outputs per operation, consumed in call order.
- ``failures`` makes a chosen number of matching calls fail with a specific
  ``ProviderError`` before falling through to the scripted/default
  response, to exercise retry, fallback, and repair paths without a
  network.

Every call -- successful or failed -- is recorded on ``self.calls`` so a
test can assert exactly what the router or planner sent.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ValidationError

from northforge.providers.base import estimate_tokens
from northforge.providers.errors import (
    ProviderAuthError,
    ProviderMalformedOutputError,
    ProviderRateLimitedError,
    ProviderRequestError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from northforge.providers.structured import generate_with_repair
from northforge.providers.types import (
    EmbeddingRequest,
    EmbeddingResponse,
    GenerationRequest,
    ModelResponse,
    Operation,
    RerankRequest,
    RerankResponse,
    RerankResult,
    ToolCallRequest,
    ToolDefinition,
    Usage,
)

FailureKind = Literal[
    "timeout", "rate_limited", "server_error", "auth", "malformed_json", "bad_request"
]


@dataclass(frozen=True)
class ScriptedFailure:
    """Make the next ``times`` matching calls fail before normal responses resume."""

    kind: FailureKind
    times: int = 1
    operation: Operation | None = None
    model: str | None = None


@dataclass(frozen=True)
class MockCall:
    """One recorded invocation, whether it succeeded or raised."""

    operation: Operation
    model: str
    request: GenerationRequest | EmbeddingRequest | RerankRequest


def _tokenize(text: str) -> set[str]:
    return {word for word in text.lower().split() if word}


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


class MockProvider:
    """``ModelProvider`` implementation backed by in-process, scriptable logic."""

    def __init__(
        self,
        *,
        responses: Mapping[Operation, Sequence[object]] | None = None,
        failures: Sequence[ScriptedFailure] = (),
        embedding_dimensions: int = 2048,
        latency_ms: float = 1.0,
    ) -> None:
        self._responses: dict[Operation, list[object]] = {
            operation: list(items) for operation, items in (responses or {}).items()
        }
        self._failures: list[dict[str, object]] = [
            {
                "kind": failure.kind,
                "remaining": failure.times,
                "operation": failure.operation,
                "model": failure.model,
            }
            for failure in failures
        ]
        self._embedding_dimensions = embedding_dimensions
        self._latency_ms = latency_ms
        self.calls: list[MockCall] = []

    @property
    def name(self) -> str:
        return "mock"

    # -- failure scripting --------------------------------------------------

    def _consume_failure(self, operation: Operation, model: str) -> FailureKind | None:
        for entry in self._failures:
            remaining = entry["remaining"]
            assert isinstance(remaining, int)
            if remaining <= 0:
                continue
            entry_operation = entry["operation"]
            if entry_operation is not None and entry_operation != operation:
                continue
            entry_model = entry["model"]
            if entry_model is not None and entry_model != model:
                continue
            entry["remaining"] = remaining - 1
            kind = entry["kind"]
            assert isinstance(kind, str)
            return kind  # type: ignore[return-value]
        return None

    def _raise_failure(self, kind: FailureKind, model: str) -> None:
        if kind == "timeout":
            raise ProviderTimeoutError("mock provider timed out", provider=self.name, model=model)
        if kind == "rate_limited":
            raise ProviderRateLimitedError(
                retry_after_seconds=0.01, provider=self.name, model=model
            )
        if kind == "server_error":
            raise ProviderUnavailableError(
                "mock provider is unavailable", provider=self.name, model=model
            )
        if kind == "auth":
            raise ProviderAuthError(provider=self.name, model=model)
        if kind == "bad_request":
            raise ProviderRequestError(
                "mock provider rejected the request", provider=self.name, model=model
            )
        raise AssertionError(f"unhandled failure kind: {kind}")  # pragma: no cover

    # -- usage estimation -----------------------------------------------------

    def _usage(self, request: GenerationRequest, content: str) -> Usage:
        prompt_text = "\n".join(message.content for message in request.messages)
        prompt_tokens = estimate_tokens(prompt_text) if prompt_text else 0
        completion_tokens = estimate_tokens(content) if content else 0
        return Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )

    # -- scripted response consumption -----------------------------------------

    def _next_generate_content(self, request: GenerationRequest) -> str:
        queue = self._responses.get("generate")
        if queue:
            value = queue.pop(0)
            if not isinstance(value, str):
                raise TypeError(f"unsupported scripted generate response: {value!r}")
            return value
        last_user = next(
            (message.content for message in reversed(request.messages) if message.role == "user"),
            "",
        )
        return f"mock:{last_user}"

    def _next_structured_content[T: BaseModel](self, model: str, schema: type[T]) -> str:
        queue = self._responses.get("generate_structured")
        if queue:
            value = queue.pop(0)
            if isinstance(value, BaseModel):
                return value.model_dump_json()
            if isinstance(value, dict):
                return json.dumps(value)
            if isinstance(value, str):
                return value
            raise TypeError(f"unsupported scripted generate_structured response: {value!r}")
        try:
            default = schema.model_validate({})
        except ValidationError as exc:
            raise ProviderMalformedOutputError(provider=self.name, model=model) from exc
        return default.model_dump_json()

    def _next_tool_calls(self) -> list[ToolCallRequest]:
        queue = self._responses.get("tool_call")
        if queue:
            value = queue.pop(0)
            if not isinstance(value, list):
                raise TypeError(f"unsupported scripted tool_call response: {value!r}")
            return value
        return []

    # -- ModelProvider protocol --------------------------------------------------

    async def generate(self, request: GenerationRequest, model: str) -> ModelResponse[None]:
        self.calls.append(MockCall(operation="generate", model=model, request=request))
        kind = self._consume_failure("generate", model)
        if kind is not None:
            if kind == "malformed_json":
                raise ProviderMalformedOutputError(provider=self.name, model=model)
            self._raise_failure(kind, model)
        content = self._next_generate_content(request)
        return ModelResponse[None](
            model=model,
            provider=self.name,
            latency_ms=self._latency_ms,
            usage=self._usage(request, content),
            content=content,
        )

    async def generate_structured[T: BaseModel](
        self, request: GenerationRequest, model: str, schema: type[T]
    ) -> ModelResponse[T]:
        async def call(req: GenerationRequest) -> ModelResponse[None]:
            self.calls.append(MockCall(operation="generate_structured", model=model, request=req))
            kind = self._consume_failure("generate_structured", model)
            if kind is not None and kind != "malformed_json":
                self._raise_failure(kind, model)
            content = (
                "{not json"
                if kind == "malformed_json"
                else self._next_structured_content(model, schema)
            )
            return ModelResponse[None](
                model=model,
                provider=self.name,
                latency_ms=self._latency_ms,
                usage=self._usage(req, content),
                content=content,
            )

        return await generate_with_repair(call, request, schema)

    async def tool_call(
        self, request: GenerationRequest, model: str, tools: list[ToolDefinition]
    ) -> ModelResponse[None]:
        del tools  # the mock does not inspect tool definitions, only scripted responses
        self.calls.append(MockCall(operation="tool_call", model=model, request=request))
        kind = self._consume_failure("tool_call", model)
        if kind is not None:
            if kind == "malformed_json":
                raise ProviderMalformedOutputError(provider=self.name, model=model)
            self._raise_failure(kind, model)
        tool_calls = self._next_tool_calls()
        return ModelResponse[None](
            model=model,
            provider=self.name,
            latency_ms=self._latency_ms,
            usage=self._usage(request, ""),
            tool_calls=tool_calls,
        )

    async def embed(self, request: EmbeddingRequest, model: str) -> EmbeddingResponse:
        self.calls.append(MockCall(operation="embed", model=model, request=request))
        kind = self._consume_failure("embed", model)
        if kind is not None:
            if kind == "malformed_json":
                raise ProviderMalformedOutputError(provider=self.name, model=model)
            self._raise_failure(kind, model)
        vectors = [self._embed_one(text) for text in request.texts]
        prompt_tokens = sum(estimate_tokens(text) for text in request.texts)
        return EmbeddingResponse(
            model=model,
            provider=self.name,
            latency_ms=self._latency_ms,
            usage=Usage(prompt_tokens=prompt_tokens, total_tokens=prompt_tokens),
            vectors=vectors,
            dimensions=self._embedding_dimensions,
        )

    def _embed_one(self, text: str) -> list[float]:
        seed = hashlib.sha256(text.encode("utf-8")).digest()
        rng = random.Random(seed)  # noqa: S311 - deterministic mock data, not cryptographic use
        raw = [rng.uniform(-1.0, 1.0) for _ in range(self._embedding_dimensions)]
        norm = sum(value * value for value in raw) ** 0.5
        if norm == 0.0:
            return raw
        return [value / norm for value in raw]

    async def rerank(self, request: RerankRequest, model: str) -> RerankResponse:
        self.calls.append(MockCall(operation="rerank", model=model, request=request))
        kind = self._consume_failure("rerank", model)
        if kind is not None:
            if kind == "malformed_json":
                raise ProviderMalformedOutputError(provider=self.name, model=model)
            self._raise_failure(kind, model)
        query_terms = _tokenize(request.query)
        results = [
            RerankResult(index=index, score=_jaccard(query_terms, _tokenize(passage)))
            for index, passage in enumerate(request.passages)
        ]
        results.sort(key=lambda result: result.score, reverse=True)
        if request.top_n is not None:
            results = results[: request.top_n]
        return RerankResponse(
            model=model, provider=self.name, latency_ms=self._latency_ms, rankings=results
        )

    def count_tokens(self, text: str, model: str) -> int | None:
        del model
        return estimate_tokens(text)


__all__ = ["MockCall", "MockProvider", "ScriptedFailure"]
