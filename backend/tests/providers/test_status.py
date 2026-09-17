from __future__ import annotations

import pytest

from northforge.core.config import Settings, load_settings
from northforge.providers.capabilities import CapabilityRegistry
from northforge.providers.resilience import CircuitBreakerRegistry, ConcurrencyLimiter, RetryPolicy
from northforge.providers.router import ModelRouter, Route, resolve_routes
from northforge.providers.status import build_provider_status
from northforge.providers.types import MODEL_ROLES, GenerationRequest, Message, ModelRole
from tests.conftest import set_unit_s3_env
from tests.providers.test_router import FakeProvider, _model, _registry


def _make_settings(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> Settings:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    monkeypatch.setenv("AUTH_MODE", "dev")
    set_unit_s3_env(monkeypatch)
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)
    return load_settings(env_file=None)


async def _no_sleep(seconds: float) -> None:
    return None


def _real_router(settings: Settings, registry: CapabilityRegistry) -> ModelRouter:
    routes = resolve_routes(settings, registry)
    provider = FakeProvider(name="nvidia")
    return ModelRouter(
        routes,
        {"nvidia": provider},
        registry,
        retry_policy=RetryPolicy(sleep=_no_sleep),
        breakers=CircuitBreakerRegistry(),
        limiter=ConcurrencyLimiter(per_provider=4, per_model=4),
        cache_enabled=False,
    )


def test_configured_false_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = CapabilityRegistry.load()
    settings = _make_settings(monkeypatch)
    router = _real_router(settings, registry)
    status = build_provider_status(router, settings)
    assert status.configured is False


def test_configured_true_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = CapabilityRegistry.load()
    settings = _make_settings(monkeypatch, NVIDIA_API_KEY="nvapi-test-secret")
    router = _real_router(settings, registry)
    status = build_provider_status(router, settings)
    assert status.configured is True


def test_configured_true_for_mock_provider_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _registry([_model("m1")])
    settings = _make_settings(monkeypatch, MODEL_PROVIDER="mock")
    routes: dict[ModelRole, Route] = {
        "drafter": Route(role="drafter", primary="m1", fallbacks=(), provider="mock")
    }
    router = ModelRouter(
        routes,
        {"mock": FakeProvider()},
        registry,
        retry_policy=RetryPolicy(sleep=_no_sleep),
        breakers=CircuitBreakerRegistry(),
        limiter=ConcurrencyLimiter(per_provider=4, per_model=4),
        cache_enabled=False,
    )
    status = build_provider_status(router, settings)
    assert status.configured is True


def test_base_url_host_only(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = CapabilityRegistry.load()
    settings = _make_settings(
        monkeypatch, NVIDIA_BASE_URL="https://user:secret@integrate.api.nvidia.com/v1/models"
    )
    router = _real_router(settings, registry)
    status = build_provider_status(router, settings)
    assert status.base_url_host == "integrate.api.nvidia.com"
    assert "user" not in (status.base_url_host or "")
    assert "secret" not in (status.base_url_host or "")


def test_six_roles_ordered(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = CapabilityRegistry.load()
    settings = _make_settings(monkeypatch)
    router = _real_router(settings, registry)
    status = build_provider_status(router, settings)
    assert [role_status.role for role_status in status.roles] == list(MODEL_ROLES)


def test_disabled_role_reflected(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = CapabilityRegistry.load()
    settings = _make_settings(monkeypatch, EMBEDDING_PROVIDER="none", RERANKER_PROVIDER="none")
    router = _real_router(settings, registry)
    status = build_provider_status(router, settings)
    by_role = {role_status.role: role_status for role_status in status.roles}
    assert by_role["embedding"].enabled is False
    assert by_role["embedding"].model is None
    assert by_role["reranker"].enabled is False
    assert by_role["planner"].enabled is True


async def test_circuit_state_and_last_error_surfaced() -> None:
    registry = _registry([_model("m1"), _model("m2")])
    provider = FakeProvider()
    from northforge.providers.errors import ProviderRequestError

    provider.script("generate", "m1", [ProviderRequestError("bad request")])
    routes: dict[ModelRole, Route] = {
        "drafter": Route(role="drafter", primary="m1", fallbacks=("m2",), provider="mock")
    }
    breakers = CircuitBreakerRegistry(failure_threshold=1)
    router = ModelRouter(
        routes,
        {"mock": provider},
        registry,
        retry_policy=RetryPolicy(sleep=_no_sleep),
        breakers=breakers,
        limiter=ConcurrencyLimiter(per_provider=4, per_model=4),
        cache_enabled=False,
    )
    with pytest.raises(ProviderRequestError):
        await router.generate("drafter", _fake_request())

    class _FakeSettings:
        model_provider = "mock"
        nvidia_api_key = None
        nvidia_base_url = "https://integrate.api.nvidia.com/v1"

    status = build_provider_status(router, _FakeSettings())  # type: ignore[arg-type]
    by_role = {role_status.role: role_status for role_status in status.roles}
    drafter_status = by_role["drafter"]
    assert drafter_status.last_error is not None
    assert drafter_status.last_error.code == ProviderRequestError.code
    assert drafter_status.last_error.category == "bad_request"


def _fake_request() -> GenerationRequest:
    return GenerationRequest(messages=[Message(role="user", content="hi")])


def test_status_json_contains_no_nvapi_string(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = CapabilityRegistry.load()
    settings = _make_settings(monkeypatch, NVIDIA_API_KEY="nvapi-test-secret")
    router = _real_router(settings, registry)
    status = build_provider_status(router, settings)
    assert "nvapi" not in status.model_dump_json()
