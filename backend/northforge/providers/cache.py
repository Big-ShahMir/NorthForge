"""Response caching for deterministic model calls.

Development and evaluation runs repeat the same deterministic prompts
often enough that replaying them saves real quota (the hosted free tier
allows 40 requests/minute per key -- see ``docs/MODEL_ROUTING.md``). This
module wraps a ``ModelProvider`` so callers cannot tell whether a response
came from cache: ``CachingProvider`` satisfies the same protocol and hands
back the same response type, only with ``cache_hit`` set.

Embeddings and reranking are cached unconditionally (they are pure
functions of their input). Generation, structured generation, and tool
calls are cached only when the request is deterministic
(``GenerationRequest.is_deterministic``), since a nonzero temperature makes
replay actively wrong. A cache backend failure never fails the request: it
is treated as a miss and logged.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, Protocol, cast

from pydantic import BaseModel

from northforge.providers.types import (
    EmbeddingRequest,
    EmbeddingResponse,
    GenerationRequest,
    ModelResponse,
    RerankRequest,
    RerankResponse,
    ToolDefinition,
)

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from northforge.providers.base import ModelProvider

logger = logging.getLogger(__name__)


class CacheBackend(Protocol):
    """Minimal string key/value store the caching layer needs."""

    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str, ttl_seconds: int) -> None: ...


class MemoryCacheBackend:
    """In-process cache backend for tests and single-worker deployments."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._store: dict[str, tuple[str, float | None]] = {}

    async def get(self, key: str) -> str | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at is not None and self._clock() >= expires_at:
            del self._store[key]
            return None
        return value

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        expires_at = self._clock() + ttl_seconds if ttl_seconds > 0 else None
        self._store[key] = (value, expires_at)


class RedisCacheBackend:
    """Cache backend on top of a ``redis.asyncio.Redis`` client, namespaced."""

    def __init__(self, redis: Redis, namespace: str = "northforge:model-cache:") -> None:
        self._redis = redis
        self._namespace = namespace

    def _namespaced(self, key: str) -> str:
        return f"{self._namespace}{key}"

    async def get(self, key: str) -> str | None:
        raw = await self._redis.get(self._namespaced(key))
        if raw is None:
            return None
        return raw.decode() if isinstance(raw, bytes) else str(raw)

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        await self._redis.set(self._namespaced(key), value, ex=ttl_seconds)


def cache_key(provider: str, operation: str, model: str, payload: Mapping[str, Any]) -> str:
    """Stable cache key: sha256 of the canonical JSON of the call's inputs."""
    canonical = json.dumps(
        {"provider": provider, "operation": operation, "model": model, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _generation_payload(request: GenerationRequest) -> dict[str, Any]:
    dumped = request.model_dump(mode="json")
    dumped.pop("metadata", None)
    dumped.pop("timeout_seconds", None)
    return dumped


def _structured_payload(request: GenerationRequest, schema: type[BaseModel]) -> dict[str, Any]:
    payload = _generation_payload(request)
    payload["schema_name"] = schema.__name__
    payload["schema"] = schema.model_json_schema()
    return payload


def _tool_call_payload(request: GenerationRequest, tools: list[ToolDefinition]) -> dict[str, Any]:
    payload = _generation_payload(request)
    payload["tools"] = [tool.model_dump(mode="json") for tool in tools]
    return payload


def _embed_payload(request: EmbeddingRequest) -> dict[str, Any]:
    return {"texts": request.texts, "input_type": request.input_type}


def _rerank_payload(request: RerankRequest) -> dict[str, Any]:
    return {"query": request.query, "passages": request.passages, "top_n": request.top_n}


class CachingProvider:
    """``ModelProvider`` decorator that caches eligible calls behind ``backend``."""

    def __init__(
        self,
        inner: ModelProvider,
        backend: CacheBackend,
        *,
        ttl_seconds: int,
        enabled: bool = True,
    ) -> None:
        self._inner = inner
        self._backend = backend
        self._ttl_seconds = ttl_seconds
        self._enabled = enabled

    @property
    def inner(self) -> ModelProvider:
        return self._inner

    @property
    def name(self) -> str:
        return self._inner.name

    async def _get(self, key: str) -> str | None:
        try:
            return await self._backend.get(key)
        except Exception as exc:
            logger.warning("model cache bypassed", extra={"error": repr(exc)})
            return None

    async def _set(self, key: str, value: str) -> None:
        try:
            await self._backend.set(key, value, self._ttl_seconds)
        except Exception as exc:
            logger.warning("model cache bypassed", extra={"error": repr(exc)})

    async def generate(self, request: GenerationRequest, model: str) -> ModelResponse[None]:
        if not self._enabled or not request.is_deterministic:
            return await self._inner.generate(request, model)
        key = cache_key(self.name, "generate", model, _generation_payload(request))
        cached = await self._get(key)
        if cached is not None:
            response = ModelResponse[None].model_validate_json(cached)
            response.cache_hit = True
            return response
        response = await self._inner.generate(request, model)
        await self._set(key, response.model_dump_json())
        return response

    async def generate_structured[T: BaseModel](
        self, request: GenerationRequest, model: str, schema: type[T]
    ) -> ModelResponse[T]:
        if not self._enabled or not request.is_deterministic:
            return await self._inner.generate_structured(request, model, schema)
        key = cache_key(
            self.name, "generate_structured", model, _structured_payload(request, schema)
        )
        cached = await self._get(key)
        if cached is not None:
            # ``schema`` is only known at runtime, so it cannot be used to
            # parametrise ``ModelResponse`` in a way mypy can check; load
            # into ``ModelResponse[Any]`` and cast back to the declared
            # return type once ``parsed`` has been re-validated below.
            cached_response: ModelResponse[Any] = ModelResponse[Any].model_validate_json(cached)
            cached_response.cache_hit = True
            if cached_response.content is not None:
                cached_response.parsed = schema.model_validate_json(cached_response.content)
            return cast(ModelResponse[T], cached_response)
        response = await self._inner.generate_structured(request, model, schema)
        stored = response.model_dump(mode="json", exclude={"parsed"})
        await self._set(key, json.dumps(stored))
        return response

    async def tool_call(
        self, request: GenerationRequest, model: str, tools: list[ToolDefinition]
    ) -> ModelResponse[None]:
        if not self._enabled or not request.is_deterministic:
            return await self._inner.tool_call(request, model, tools)
        key = cache_key(self.name, "tool_call", model, _tool_call_payload(request, tools))
        cached = await self._get(key)
        if cached is not None:
            response = ModelResponse[None].model_validate_json(cached)
            response.cache_hit = True
            return response
        response = await self._inner.tool_call(request, model, tools)
        await self._set(key, response.model_dump_json())
        return response

    async def embed(self, request: EmbeddingRequest, model: str) -> EmbeddingResponse:
        if not self._enabled:
            return await self._inner.embed(request, model)
        key = cache_key(self.name, "embed", model, _embed_payload(request))
        cached = await self._get(key)
        if cached is not None:
            response = EmbeddingResponse.model_validate_json(cached)
            response.cache_hit = True
            return response
        response = await self._inner.embed(request, model)
        await self._set(key, response.model_dump_json())
        return response

    async def rerank(self, request: RerankRequest, model: str) -> RerankResponse:
        if not self._enabled:
            return await self._inner.rerank(request, model)
        key = cache_key(self.name, "rerank", model, _rerank_payload(request))
        cached = await self._get(key)
        if cached is not None:
            response = RerankResponse.model_validate_json(cached)
            response.cache_hit = True
            return response
        response = await self._inner.rerank(request, model)
        await self._set(key, response.model_dump_json())
        return response

    def count_tokens(self, text: str, model: str) -> int | None:
        return self._inner.count_tokens(text, model)


__all__ = [
    "CacheBackend",
    "CachingProvider",
    "MemoryCacheBackend",
    "RedisCacheBackend",
    "cache_key",
]
