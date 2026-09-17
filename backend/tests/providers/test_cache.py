from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from northforge.providers.cache import (
    CachingProvider,
    MemoryCacheBackend,
    RedisCacheBackend,
    cache_key,
)
from northforge.providers.types import (
    EmbeddingRequest,
    EmbeddingResponse,
    GenerationRequest,
    Message,
    ModelResponse,
    RerankRequest,
)
from tests.providers.test_router import FakeProvider, _rerank_response


class _Answer(BaseModel):
    value: str


def _request(**overrides: object) -> GenerationRequest:
    base: dict[str, object] = {"messages": [Message(role="user", content="hi")]}
    base.update(overrides)
    return GenerationRequest(**base)  # type: ignore[arg-type]


def _response(**overrides: object) -> ModelResponse[None]:
    base: dict[str, object] = {"model": "m1", "provider": "mock", "latency_ms": 1.0}
    base.update(overrides)
    return ModelResponse[None](**base)  # type: ignore[arg-type]


def _structured_response(content: str, **overrides: object) -> ModelResponse[Any]:
    base: dict[str, object] = {
        "model": "m1",
        "provider": "mock",
        "latency_ms": 1.0,
        "content": content,
    }
    base.update(overrides)
    parsed = base.pop("parsed", None)
    # ``ModelResponse`` is generic; its type parameter cannot be resolved
    # from a runtime value, so build it as ``ModelResponse[Any]`` and set
    # ``parsed`` separately rather than subscripting with a variable.
    response: ModelResponse[Any] = ModelResponse[Any](**base)  # type: ignore[arg-type]
    response.parsed = parsed
    return response


class _FailingBackend:
    def __init__(self, *, fail_get: bool = False, fail_set: bool = False) -> None:
        self.fail_get = fail_get
        self.fail_set = fail_set
        self.set_calls: list[tuple[str, str, int]] = []

    async def get(self, key: str) -> str | None:
        if self.fail_get:
            raise RuntimeError("backend get exploded")
        return None

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        if self.fail_set:
            raise RuntimeError("backend set exploded")
        self.set_calls.append((key, value, ttl_seconds))


class _FakeRedis:
    """Minimal stand-in for ``redis.asyncio.Redis`` -- no real redis needed."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.set_calls: list[tuple[str, str, int | None]] = []

    async def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value.encode()
        self.set_calls.append((key, value, ex))


# --- generate --------------------------------------------------------------


async def test_generate_deterministic_miss_then_hit() -> None:
    provider = FakeProvider()
    provider.script("generate", "m1", [_response(content="hello")])
    caching = CachingProvider(provider, MemoryCacheBackend(), ttl_seconds=60)
    request = _request(temperature=0.0)

    first = await caching.generate(request, "m1")
    assert first.cache_hit is False
    second = await caching.generate(request, "m1")
    assert second.cache_hit is True
    assert second.content == "hello"
    # Only one real provider call was made.
    assert provider.calls == [("generate", "m1")]


async def test_generate_non_deterministic_not_cached() -> None:
    provider = FakeProvider()
    provider.script("generate", "m1", [_response(content="a"), _response(content="b")])
    caching = CachingProvider(provider, MemoryCacheBackend(), ttl_seconds=60)
    request = _request(temperature=0.7)

    await caching.generate(request, "m1")
    await caching.generate(request, "m1")
    assert provider.calls == [("generate", "m1"), ("generate", "m1")]


async def test_generate_explicit_deterministic_false_not_cached() -> None:
    provider = FakeProvider()
    provider.script("generate", "m1", [_response(content="a"), _response(content="b")])
    caching = CachingProvider(provider, MemoryCacheBackend(), ttl_seconds=60)
    request = _request(temperature=0.0, deterministic=False)

    await caching.generate(request, "m1")
    await caching.generate(request, "m1")
    assert len(provider.calls) == 2


async def test_generate_explicit_deterministic_true_with_temperature_cached() -> None:
    provider = FakeProvider()
    provider.script("generate", "m1", [_response(content="a")])
    caching = CachingProvider(provider, MemoryCacheBackend(), ttl_seconds=60)
    request = _request(temperature=0.9, deterministic=True)

    first = await caching.generate(request, "m1")
    second = await caching.generate(request, "m1")
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert len(provider.calls) == 1


# --- generate_structured ----------------------------------------------------


async def test_generate_structured_hit_reparses_content() -> None:
    provider = FakeProvider()
    provider.script(
        "generate_structured",
        "m1",
        [_structured_response('{"value": "x"}', parsed=_Answer(value="x"))],
    )
    caching = CachingProvider(provider, MemoryCacheBackend(), ttl_seconds=60)
    request = _request()

    first = await caching.generate_structured(request, "m1", _Answer)
    assert first.cache_hit is False
    second = await caching.generate_structured(request, "m1", _Answer)
    assert second.cache_hit is True
    assert second.parsed == _Answer(value="x")
    assert len(provider.calls) == 1


class _OtherSchema(BaseModel):
    other: str


async def test_generate_structured_different_schema_different_key() -> None:
    provider = FakeProvider()
    provider.script(
        "generate_structured",
        "m1",
        [
            _structured_response('{"value": "x"}', parsed=_Answer(value="x")),
            _structured_response('{"other": "y"}', parsed=_OtherSchema(other="y")),
        ],
    )
    caching = CachingProvider(provider, MemoryCacheBackend(), ttl_seconds=60)
    request = _request()

    await caching.generate_structured(request, "m1", _Answer)
    await caching.generate_structured(request, "m1", _OtherSchema)
    assert len(provider.calls) == 2


# --- tool_call ---------------------------------------------------------------


async def test_tool_call_deterministic_cached() -> None:
    provider = FakeProvider()
    provider.script("tool_call", "m1", [_response()])
    caching = CachingProvider(provider, MemoryCacheBackend(), ttl_seconds=60)
    request = _request()

    await caching.tool_call(request, "m1", tools=[])
    await caching.tool_call(request, "m1", tools=[])
    assert len(provider.calls) == 1


# --- embed / rerank ------------------------------------------------------


async def test_embed_always_cached() -> None:
    provider = FakeProvider()
    provider.script(
        "embed",
        "e1",
        [
            EmbeddingResponse(
                model="e1", provider="mock", latency_ms=1.0, vectors=[[0.1]], dimensions=1
            )
        ],
    )
    caching = CachingProvider(provider, MemoryCacheBackend(), ttl_seconds=60)
    request = EmbeddingRequest(texts=["hello"])

    first = await caching.embed(request, "e1")
    second = await caching.embed(request, "e1")
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert len(provider.calls) == 1


async def test_rerank_always_cached() -> None:
    provider = FakeProvider()
    provider.script("rerank", "r1", [_rerank_response("r1")])
    caching = CachingProvider(provider, MemoryCacheBackend(), ttl_seconds=60)
    request = RerankRequest(query="q", passages=["a", "b"])

    first = await caching.rerank(request, "r1")
    second = await caching.rerank(request, "r1")
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert len(provider.calls) == 1


# --- backend failure handling -----------------------------------------------


async def test_backend_get_failure_bypasses_cache() -> None:
    provider = FakeProvider()
    provider.script("generate", "m1", [_response(content="a"), _response(content="b")])
    backend = _FailingBackend(fail_get=True)
    caching = CachingProvider(provider, backend, ttl_seconds=60)
    request = _request()

    first = await caching.generate(request, "m1")
    second = await caching.generate(request, "m1")
    assert first.cache_hit is False
    assert second.cache_hit is False
    assert len(provider.calls) == 2


async def test_backend_set_failure_bypasses_cache() -> None:
    provider = FakeProvider()
    provider.script("generate", "m1", [_response(content="a"), _response(content="b")])
    backend = _FailingBackend(fail_set=True)
    caching = CachingProvider(provider, backend, ttl_seconds=60)
    request = _request()

    first = await caching.generate(request, "m1")
    second = await caching.generate(request, "m1")
    assert first.cache_hit is False
    assert second.cache_hit is False  # nothing was ever stored


async def test_disabled_cache_never_touches_backend() -> None:
    provider = FakeProvider()
    provider.script("generate", "m1", [_response(content="a"), _response(content="b")])

    class _ExplodingBackend:
        async def get(self, key: str) -> str | None:
            raise AssertionError("backend should not be touched when disabled")

        async def set(self, key: str, value: str, ttl_seconds: int) -> None:
            raise AssertionError("backend should not be touched when disabled")

    caching = CachingProvider(provider, _ExplodingBackend(), ttl_seconds=60, enabled=False)
    request = _request()

    await caching.generate(request, "m1")
    await caching.generate(request, "m1")
    assert len(provider.calls) == 2


# --- RedisCacheBackend -------------------------------------------------------


async def test_redis_backend_namespaces_keys_and_passes_ex() -> None:
    fake_redis = _FakeRedis()
    backend = RedisCacheBackend(fake_redis, namespace="northforge:model-cache:")  # type: ignore[arg-type]

    await backend.set("abc123", '{"x": 1}', 300)
    assert fake_redis.set_calls == [("northforge:model-cache:abc123", '{"x": 1}', 300)]

    value = await backend.get("abc123")
    assert value == '{"x": 1}'


# --- cache_key ---------------------------------------------------------------


def test_cache_key_is_stable_and_order_independent() -> None:
    key_a = cache_key("nvidia", "generate", "m1", {"a": 1, "b": 2})
    key_b = cache_key("nvidia", "generate", "m1", {"b": 2, "a": 1})
    assert key_a == key_b


def test_cache_key_differs_on_payload() -> None:
    key_a = cache_key("nvidia", "generate", "m1", {"a": 1})
    key_b = cache_key("nvidia", "generate", "m1", {"a": 2})
    assert key_a != key_b


def test_inner_property_exposes_wrapped_provider() -> None:
    provider = FakeProvider()
    caching = CachingProvider(provider, MemoryCacheBackend(), ttl_seconds=60)
    assert caching.inner is provider
    assert caching.name == provider.name
