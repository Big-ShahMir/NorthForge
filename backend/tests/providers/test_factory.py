"""``build_model_router``: provider selection, cache wrapping, and startup validation."""

from __future__ import annotations

import pytest

from northforge.core.config import Settings, load_settings
from northforge.core.errors import ConfigurationError
from northforge.providers.base import NotConfiguredProvider
from northforge.providers.cache import CachingProvider, MemoryCacheBackend
from northforge.providers.errors import ProviderNotConfiguredError
from northforge.providers.factory import build_model_router, close_model_router
from northforge.providers.mock import MockProvider
from northforge.providers.nvidia import NvidiaProvider
from northforge.providers.types import EmbeddingRequest, GenerationRequest, Message
from tests.conftest import set_unit_s3_env


def _settings(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> Settings:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    set_unit_s3_env(monkeypatch)
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)
    return load_settings(env_file=None)


async def test_without_key_router_starts_and_calls_fail_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = build_model_router(_settings(monkeypatch))

    assert isinstance(router.providers()["nvidia"], NotConfiguredProvider)
    assert router.roles() == [
        "planner",
        "extractor",
        "drafter",
        "evaluator",
        "embedding",
        "reranker",
    ]
    with pytest.raises(ProviderNotConfiguredError) as excinfo:
        await router.generate(
            "planner", GenerationRequest(messages=[Message(role="user", content="hi")])
        )
    assert excinfo.value.code == "PROVIDER_NOT_CONFIGURED"
    assert "NVIDIA_API_KEY" in excinfo.value.message


async def test_with_key_builds_nvidia_provider_and_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    router = build_model_router(_settings(monkeypatch, NVIDIA_API_KEY="nvapi-test-secret"))

    assert isinstance(router.providers()["nvidia"], NvidiaProvider)
    await close_model_router(router)  # must not raise


async def test_mock_provider_serves_every_role(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(
        monkeypatch,
        MODEL_PROVIDER="mock",
        EMBEDDING_PROVIDER="mock",
        RERANKER_PROVIDER="mock",
    )
    router = build_model_router(settings)

    assert set(router.providers()) == {"mock"}
    assert isinstance(router.providers()["mock"], MockProvider)
    response = await router.generate(
        "drafter", GenerationRequest(messages=[Message(role="user", content="hello")])
    )
    assert response.provider == "mock"
    assert response.model == "moonshotai/kimi-k3"
    vectors = await router.embed(EmbeddingRequest(texts=["a", "b"]))
    assert vectors.dimensions == 2048


async def test_cache_wraps_providers_when_backend_given(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, MODEL_PROVIDER="mock")
    backend = MemoryCacheBackend()
    router = build_model_router(settings, cache_backend=backend)

    assert isinstance(router.providers()["mock"], CachingProvider)
    request = GenerationRequest(messages=[Message(role="user", content="cache me")])
    first = await router.generate("drafter", request)
    second = await router.generate("drafter", request)
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert router.snapshot()["cache_enabled"] is True


def test_cache_disabled_without_backend_or_when_switched_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    no_backend = build_model_router(_settings(monkeypatch, MODEL_PROVIDER="mock"))
    assert no_backend.snapshot()["cache_enabled"] is False

    switched_off = build_model_router(
        _settings(monkeypatch, MODEL_PROVIDER="mock", MODEL_CACHE_ENABLED="false"),
        cache_backend=MemoryCacheBackend(),
    )
    assert switched_off.snapshot()["cache_enabled"] is False
    assert not isinstance(switched_off.providers()["mock"], CachingProvider)


def test_disabled_embedding_and_reranker_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    router = build_model_router(
        _settings(monkeypatch, EMBEDDING_PROVIDER="none", RERANKER_PROVIDER="none")
    )

    assert "embedding" not in router.roles()
    assert router.snapshot()["roles"]["reranker"] == {"enabled": False}


def test_invalid_route_stops_startup_with_every_problem(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(
        monkeypatch,
        NVIDIA_MODEL_PLANNER="nvidia/nemotron-3-embed-1b",
        NVIDIA_MODEL_EVALUATOR="vendor/does-not-exist",
    )

    with pytest.raises(ConfigurationError) as excinfo:
        build_model_router(settings)

    problems = excinfo.value.problems
    assert any(p.startswith("NVIDIA_MODEL_PLANNER:") for p in problems)
    assert any(p.startswith("NVIDIA_MODEL_EVALUATOR:") for p in problems)
