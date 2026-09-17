"""``ModelProvider`` adapter for the hosted NVIDIA (build.nvidia.com) API.

Talks OpenAI-compatible chat completions for generation, structured output,
and tool calls; a separate embeddings endpoint; and a differently-hosted
reranking endpoint. See ``docs/MODEL_ROUTING.md`` for the verified request
and response shapes this adapter relies on.

This adapter never retries -- the router owns retries, fallback, and
circuit breaking (``FALLBACK_CATEGORIES`` in ``providers.errors``). It only
translates one request to one HTTP call and maps the result (or failure)
to the provider-agnostic types.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx2 as httpx
from pydantic import BaseModel

from northforge.core.config import Settings
from northforge.core.errors import ConfigurationError
from northforge.providers import structured
from northforge.providers.capabilities import CapabilityRegistry, ModelCapabilities
from northforge.providers.errors import (
    ProviderAuthError,
    ProviderCapabilityError,
    ProviderError,
    ProviderMalformedOutputError,
    ProviderRateLimitedError,
    ProviderRequestError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from northforge.providers.types import (
    EmbeddingRequest,
    EmbeddingResponse,
    GenerationRequest,
    Message,
    ModelResponse,
    Operation,
    RerankRequest,
    RerankResponse,
    RerankResult,
    StructuredOutputMode,
    ToolCallRequest,
    ToolDefinition,
    Usage,
)

logger = logging.getLogger(__name__)

_EMBEDDING_BATCH_SIZE = 32
_REJECTED_BODY_PREVIEW_CHARS = 500


def _message_to_openai(message: Message) -> dict[str, Any]:
    """Map a provider-agnostic ``Message`` to an OpenAI-compatible chat message dict."""
    payload: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.name is not None:
        payload["name"] = message.name
    if message.tool_call_id is not None:
        payload["tool_call_id"] = message.tool_call_id
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
            }
            for call in message.tool_calls
        ]
    return payload


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


class NvidiaProvider:
    """``ModelProvider`` implementation talking to the hosted NVIDIA API."""

    def __init__(
        self,
        settings: Settings,
        registry: CapabilityRegistry,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if settings.nvidia_api_key is None:
            raise ConfigurationError(
                "NVIDIA provider needs NVIDIA_API_KEY",
                problems=["NVIDIA_API_KEY: required when MODEL_PROVIDER=nvidia"],
            )
        self._api_key = settings.nvidia_api_key
        self._base_url = settings.nvidia_base_url.rstrip("/")
        self._rerank_base_url = settings.nvidia_rerank_base_url.rstrip("/")
        self._registry = registry
        self._default_timeout = settings.model_request_timeout_seconds
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=5.0,
                read=settings.model_request_timeout_seconds,
                write=10.0,
                pool=5.0,
            ),
            limits=httpx.Limits(max_connections=settings.model_provider_concurrency * 2),
        )

    def __repr__(self) -> str:
        return f"NvidiaProvider(base_url={self._base_url!r})"

    @property
    def name(self) -> str:
        return "nvidia"

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # -- shared request plumbing ------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key.get_secret_value()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _map_error_status(
        self, status_code: int, response: httpx.Response, *, model: str, request_id: str | None
    ) -> ProviderError:
        if status_code in (400, 404, 422):
            logger.warning(
                "provider.request.rejected",
                extra={
                    "provider": self.name,
                    "model": model,
                    "status_code": status_code,
                    "body": response.text[:_REJECTED_BODY_PREVIEW_CHARS],
                },
            )
            return ProviderRequestError(
                f"Model provider rejected the request (HTTP {status_code}).",
                provider=self.name,
                model=model,
                request_id=request_id,
            )
        if status_code in (401, 403):
            return ProviderAuthError(provider=self.name, model=model, request_id=request_id)
        if status_code == 429:
            retry_after = _parse_retry_after(response.headers.get("retry-after"))
            return ProviderRateLimitedError(
                retry_after_seconds=retry_after,
                provider=self.name,
                model=model,
                request_id=request_id,
            )
        if status_code == 408 or status_code >= 500:
            return ProviderUnavailableError(
                f"Model provider is unavailable (HTTP {status_code}).",
                provider=self.name,
                model=model,
                request_id=request_id,
            )
        return ProviderUnavailableError(
            f"Model provider returned an unexpected status (HTTP {status_code}).",
            provider=self.name,
            model=model,
            request_id=request_id,
        )

    async def _request(
        self,
        *,
        method: str,
        url: str,
        body: dict[str, Any],
        model: str,
        operation: Operation,
        role: str | None,
        timeout_seconds: float | None,
        expected_key: str,
    ) -> tuple[dict[str, Any], str | None, float]:
        if timeout_seconds is None:
            capabilities = self._registry.get(model)
            model_timeout = capabilities.timeout_seconds if capabilities is not None else None
            timeout_seconds = model_timeout if model_timeout is not None else self._default_timeout
        timeout = httpx.Timeout(
            connect=5.0,
            read=timeout_seconds,
            write=10.0,
            pool=5.0,
        )
        started = time.perf_counter()
        status_code: int | None = None
        request_id: str | None = None
        error_category: str | None = None
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        latency_ms = 0.0
        try:
            try:
                response = await self._client.request(
                    method, url, headers=self._headers(), json=body, timeout=timeout
                )
            except httpx.TimeoutException as exc:
                raise ProviderTimeoutError(
                    "Model provider request timed out.", provider=self.name, model=model
                ) from exc
            except httpx.TransportError as exc:
                raise ProviderUnavailableError(
                    "Model provider could not be reached.", provider=self.name, model=model
                ) from exc

            status_code = response.status_code
            request_id = response.headers.get("x-request-id")

            if status_code >= 400:
                raise self._map_error_status(
                    status_code, response, model=model, request_id=request_id
                )

            try:
                data = response.json()
            except ValueError as exc:
                raise ProviderUnavailableError(
                    "Model provider returned an unreadable response.",
                    provider=self.name,
                    model=model,
                    request_id=request_id,
                ) from exc

            if not isinstance(data, dict) or expected_key not in data:
                raise ProviderUnavailableError(
                    "Model provider returned an unreadable response.",
                    provider=self.name,
                    model=model,
                    request_id=request_id,
                )

            if request_id is None:
                body_id = data.get("id")
                request_id = body_id if isinstance(body_id, str) else None

            usage = data.get("usage")
            if isinstance(usage, dict):
                raw_prompt_tokens = usage.get("prompt_tokens")
                raw_completion_tokens = usage.get("completion_tokens")
                prompt_tokens = raw_prompt_tokens if isinstance(raw_prompt_tokens, int) else None
                completion_tokens = (
                    raw_completion_tokens if isinstance(raw_completion_tokens, int) else None
                )

            latency_ms = round((time.perf_counter() - started) * 1000, 1)
            return data, request_id, latency_ms
        except ProviderError as exc:
            error_category = exc.category
            if exc.request_id is not None:
                request_id = exc.request_id
            raise
        finally:
            if latency_ms == 0.0:
                latency_ms = round((time.perf_counter() - started) * 1000, 1)
            logger.info(
                "provider.request",
                extra={
                    "provider": self.name,
                    "model": model,
                    "operation": operation,
                    "role": role,
                    "status_code": status_code,
                    "latency_ms": latency_ms,
                    "request_id": request_id,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "error_category": error_category,
                },
            )

    def _log_debug_messages(
        self, model: str, operation: Operation, messages: list[Message]
    ) -> None:
        if logger.isEnabledFor(logging.DEBUG):
            total_chars = sum(len(message.content) for message in messages)
            logger.debug(
                "provider.request.detail",
                extra={
                    "provider": self.name,
                    "model": model,
                    "operation": operation,
                    "message_count": len(messages),
                    "input_chars": total_chars,
                },
            )

    # -- chat body construction and response parsing ----------------------------

    def _build_chat_body(self, request: GenerationRequest, model: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": [_message_to_openai(message) for message in request.messages],
            "temperature": request.temperature,
        }
        if request.top_p is not None:
            body["top_p"] = request.top_p
        if request.max_tokens is not None:
            body["max_tokens"] = request.max_tokens
        return body

    def _apply_reasoning_toggle(
        self,
        body: dict[str, Any],
        request: GenerationRequest,
        model: str,
        capabilities: ModelCapabilities | None,
        warnings: list[str],
    ) -> None:
        reasoning = request.reasoning
        if reasoning is None and capabilities is not None:
            reasoning = capabilities.default_reasoning
        if reasoning is None:
            return
        if capabilities is not None and capabilities.supports_reasoning_toggle:
            body["chat_template_kwargs"] = {"enable_thinking": reasoning}
        else:
            warnings.append(
                f"model {model!r} does not support the reasoning toggle; request ignored"
            )

    def _decode_tool_calls(
        self, raw: object, *, model: str, request_id: str | None
    ) -> list[ToolCallRequest]:
        if not raw:
            return []
        if not isinstance(raw, list):
            raise ProviderMalformedOutputError(
                provider=self.name, model=model, request_id=request_id
            )
        calls: list[ToolCallRequest] = []
        for item in raw:
            try:
                function = item["function"]
                raw_arguments = function.get("arguments")
                arguments = json.loads(raw_arguments) if raw_arguments else {}
                calls.append(
                    ToolCallRequest(id=item["id"], name=function["name"], arguments=arguments)
                )
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise ProviderMalformedOutputError(
                    provider=self.name, model=model, request_id=request_id
                ) from exc
        return calls

    def _parse_chat_response(
        self,
        data: dict[str, Any],
        *,
        model: str,
        request_id: str | None,
        latency_ms: float,
        warnings: list[str],
    ) -> ModelResponse[None]:
        choices = data.get("choices") or []
        if not choices:
            raise ProviderUnavailableError(
                "Model provider returned an unreadable response.",
                provider=self.name,
                model=model,
                request_id=request_id,
            )
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            content = structured.strip_reasoning(content)
        finish_reason = choices[0].get("finish_reason")
        tool_calls = self._decode_tool_calls(
            message.get("tool_calls"), model=model, request_id=request_id
        )
        usage_data = data.get("usage") or {}
        usage = Usage(
            prompt_tokens=usage_data.get("prompt_tokens"),
            completion_tokens=usage_data.get("completion_tokens"),
            total_tokens=usage_data.get("total_tokens"),
        )
        return ModelResponse[None](
            model=model,
            provider=self.name,
            latency_ms=latency_ms,
            request_id=request_id,
            usage=usage,
            warnings=warnings,
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
        )

    # -- ModelProvider protocol --------------------------------------------------

    async def generate(self, request: GenerationRequest, model: str) -> ModelResponse[None]:
        capabilities = self._registry.get(model)
        warnings: list[str] = []
        body = self._build_chat_body(request, model)
        self._apply_reasoning_toggle(body, request, model, capabilities, warnings)
        self._log_debug_messages(model, "generate", request.messages)
        data, request_id, latency_ms = await self._request(
            method="POST",
            url=f"{self._base_url}/chat/completions",
            body=body,
            model=model,
            operation="generate",
            role=request.metadata.get("role"),
            timeout_seconds=request.timeout_seconds,
            expected_key="choices",
        )
        return self._parse_chat_response(
            data, model=model, request_id=request_id, latency_ms=latency_ms, warnings=warnings
        )

    async def generate_structured[T: BaseModel](
        self,
        request: GenerationRequest,
        model: str,
        schema: type[T],
        *,
        mode: StructuredOutputMode | None = None,
    ) -> ModelResponse[T]:
        capabilities = self._registry.get(model)
        if capabilities is None or not capabilities.supports_structured_output:
            raise ProviderCapabilityError(
                f"model {model!r} does not support structured output",
                provider=self.name,
                model=model,
            )
        resolved_mode: StructuredOutputMode = (
            mode or capabilities.structured_output_mode or "json_schema"
        )
        base_request = request
        if resolved_mode == "prompt_only":
            base_request = structured.with_schema_instruction(request, schema)

        async def call(req: GenerationRequest) -> ModelResponse[None]:
            warnings: list[str] = []
            body = self._build_chat_body(req, model)
            self._apply_reasoning_toggle(body, req, model, capabilities, warnings)
            if resolved_mode == "json_schema":
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema.__name__,
                        "schema": schema.model_json_schema(),
                        "strict": True,
                    },
                }
            elif resolved_mode == "nvext_guided_json":
                body["nvext"] = {"guided_json": schema.model_json_schema()}
            self._log_debug_messages(model, "generate_structured", req.messages)
            data, request_id, latency_ms = await self._request(
                method="POST",
                url=f"{self._base_url}/chat/completions",
                body=body,
                model=model,
                operation="generate_structured",
                role=req.metadata.get("role"),
                timeout_seconds=req.timeout_seconds,
                expected_key="choices",
            )
            return self._parse_chat_response(
                data, model=model, request_id=request_id, latency_ms=latency_ms, warnings=warnings
            )

        return await structured.generate_with_repair(call, base_request, schema)

    async def tool_call(
        self, request: GenerationRequest, model: str, tools: list[ToolDefinition]
    ) -> ModelResponse[None]:
        capabilities = self._registry.get(model)
        if capabilities is None or not capabilities.supports_tools:
            raise ProviderCapabilityError(
                f"model {model!r} does not support tool calls", provider=self.name, model=model
            )
        warnings: list[str] = []
        body = self._build_chat_body(request, model)
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in tools
        ]
        body["tool_choice"] = request.tool_choice
        self._apply_reasoning_toggle(body, request, model, capabilities, warnings)
        self._log_debug_messages(model, "tool_call", request.messages)
        data, request_id, latency_ms = await self._request(
            method="POST",
            url=f"{self._base_url}/chat/completions",
            body=body,
            model=model,
            operation="tool_call",
            role=request.metadata.get("role"),
            timeout_seconds=request.timeout_seconds,
            expected_key="choices",
        )
        return self._parse_chat_response(
            data, model=model, request_id=request_id, latency_ms=latency_ms, warnings=warnings
        )

    async def embed(self, request: EmbeddingRequest, model: str) -> EmbeddingResponse:
        capabilities = self._registry.get(model)
        vectors: list[list[float]] = []
        prompt_tokens_total = 0
        total_tokens_total = 0
        has_usage = False
        last_request_id: str | None = None
        latency_total = 0.0
        for start in range(0, len(request.texts), _EMBEDDING_BATCH_SIZE):
            chunk = request.texts[start : start + _EMBEDDING_BATCH_SIZE]
            body = {
                "model": model,
                "input": chunk,
                "input_type": request.input_type,
                "encoding_format": "float",
                "truncate": "END",
            }
            data, request_id, latency_ms = await self._request(
                method="POST",
                url=f"{self._base_url}/embeddings",
                body=body,
                model=model,
                operation="embed",
                role=None,
                timeout_seconds=request.timeout_seconds,
                expected_key="data",
            )
            items = data.get("data")
            if not isinstance(items, list):
                raise ProviderUnavailableError(
                    "Model provider returned an unreadable response.",
                    provider=self.name,
                    model=model,
                    request_id=request_id,
                )
            for item in sorted(items, key=lambda entry: entry.get("index", 0)):
                vectors.append(item["embedding"])
            usage_data = data.get("usage")
            if isinstance(usage_data, dict):
                has_usage = True
                prompt_tokens_total += usage_data.get("prompt_tokens") or 0
                total_tokens_total += usage_data.get("total_tokens") or 0
            last_request_id = request_id
            latency_total += latency_ms

        dimensions = len(vectors[0]) if vectors else 0
        warnings: list[str] = []
        if (
            capabilities is not None
            and capabilities.embedding_dimensions is not None
            and capabilities.embedding_dimensions != dimensions
        ):
            warnings.append(
                f"embedding model {model!r} returned {dimensions} dimensions, "
                f"catalog expects {capabilities.embedding_dimensions}"
            )
        return EmbeddingResponse(
            model=model,
            provider=self.name,
            latency_ms=round(latency_total, 1),
            request_id=last_request_id,
            usage=Usage(
                prompt_tokens=prompt_tokens_total if has_usage else None,
                total_tokens=total_tokens_total if has_usage else None,
            ),
            warnings=warnings,
            vectors=vectors,
            dimensions=dimensions,
        )

    async def rerank(self, request: RerankRequest, model: str) -> RerankResponse:
        body = {
            "model": model,
            "query": {"text": request.query},
            "passages": [{"text": passage} for passage in request.passages],
            "truncate": "END",
        }
        url = f"{self._rerank_base_url}/retrieval/{model}/reranking"
        data, request_id, latency_ms = await self._request(
            method="POST",
            url=url,
            body=body,
            model=model,
            operation="rerank",
            role=None,
            timeout_seconds=request.timeout_seconds,
            expected_key="rankings",
        )
        rankings_raw = data.get("rankings") or []
        results = [RerankResult(index=item["index"], score=item["logit"]) for item in rankings_raw]
        results.sort(key=lambda result: result.score, reverse=True)
        if request.top_n is not None:
            results = results[: request.top_n]
        return RerankResponse(
            model=model,
            provider=self.name,
            latency_ms=latency_ms,
            request_id=request_id,
            rankings=results,
        )

    def count_tokens(self, text: str, model: str) -> int | None:
        return None


__all__ = ["NvidiaProvider"]
