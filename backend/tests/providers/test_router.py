from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import BaseModel

from northforge.core.config import Settings, load_settings
from northforge.core.errors import ConfigurationError
from northforge.providers.capabilities import (
    CapabilityRegistry,
    ModelCapabilities,
    ModelCatalog,
)
from northforge.providers.errors import (
    ProviderCapabilityError,
    ProviderCircuitOpenError,
    ProviderError,
    ProviderMalformedOutputError,
    ProviderNotConfiguredError,
    ProviderRateLimitedError,
    ProviderRequestError,
    ProviderUnavailableError,
)
from northforge.providers.resilience import CircuitBreakerRegistry, ConcurrencyLimiter, RetryPolicy
from northforge.providers.router import ModelRouter, Route, resolve_routes
from northforge.providers.types import (
    MODEL_ROLES,
    EmbeddingRequest,
    EmbeddingResponse,
    GenerationRequest,
    Message,
    ModelInvocationRecord,
    ModelResponse,
    ModelRole,
    RerankRequest,
    RerankResponse,
    RerankResult,
    ToolDefinition,
)
from tests.conftest import set_unit_s3_env


class FakeProvider:
    """Scriptable fake ``ModelProvider`` for router (and cache/status) tests.

    ``script(operation, model, outcomes)`` queues a sequence of outcomes for
    that (operation, model) pair; each outcome is either a response object
    (returned) or an exception instance (raised). Calls and the requests
    passed for each call are recorded for assertions.
    """

    def __init__(self, name: str = "mock") -> None:
        self._name = name
        self._scripts: dict[tuple[str, str], list[Any]] = {}
        self.calls: list[tuple[str, str]] = []
        self.requests: list[Any] = []

    @property
    def name(self) -> str:
        return self._name

    def script(self, operation: str, model: str, outcomes: list[Any]) -> None:
        self._scripts.setdefault((operation, model), []).extend(outcomes)

    def _next(self, operation: str, model: str, request: object) -> Any:
        self.calls.append((operation, model))
        self.requests.append(request)
        queue = self._scripts.get((operation, model))
        if not queue:
            raise AssertionError(f"no scripted outcome for {operation!r} on {model!r}")
        outcome = queue.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def generate(self, request: GenerationRequest, model: str) -> ModelResponse[None]:
        return self._next("generate", model, request)  # type: ignore[no-any-return]

    async def generate_structured[T: BaseModel](
        self, request: GenerationRequest, model: str, schema: type[T]
    ) -> ModelResponse[T]:
        return self._next("generate_structured", model, request)  # type: ignore[no-any-return]

    async def tool_call(
        self, request: GenerationRequest, model: str, tools: list[ToolDefinition]
    ) -> ModelResponse[None]:
        return self._next("tool_call", model, request)  # type: ignore[no-any-return]

    async def embed(self, request: EmbeddingRequest, model: str) -> EmbeddingResponse:
        return self._next("embed", model, request)  # type: ignore[no-any-return]

    async def rerank(self, request: RerankRequest, model: str) -> RerankResponse:
        return self._next("rerank", model, request)  # type: ignore[no-any-return]

    def count_tokens(self, text: str, model: str) -> int | None:
        return None


async def _no_sleep(seconds: float) -> None:
    return None


def _model(
    model_id: str, *, supports_tools: bool = True, supports_structured_output: bool = True
) -> ModelCapabilities:
    return ModelCapabilities(
        model=model_id,
        provider="mock",
        modality="generation",
        supports_structured_output=supports_structured_output,
        structured_output_mode="json_schema" if supports_structured_output else None,
        supports_tools=supports_tools,
        supports_reasoning_toggle=False,
        context_window=8192,
    )


def _embedding_model(model_id: str) -> ModelCapabilities:
    return ModelCapabilities(
        model=model_id,
        provider="mock",
        modality="embedding",
        supports_structured_output=False,
        structured_output_mode=None,
        supports_tools=False,
        supports_reasoning_toggle=False,
        context_window=4096,
        embedding_dimensions=8,
    )


def _rerank_model(model_id: str) -> ModelCapabilities:
    return ModelCapabilities(
        model=model_id,
        provider="mock",
        modality="rerank",
        supports_structured_output=False,
        structured_output_mode=None,
        supports_tools=False,
        supports_reasoning_toggle=False,
        context_window=4096,
    )


def _registry(models: list[ModelCapabilities]) -> CapabilityRegistry:
    catalog = ModelCatalog(version="test", models=models, default_routes={})
    return CapabilityRegistry(catalog)


def _request(**overrides: object) -> GenerationRequest:
    base: dict[str, object] = {"messages": [Message(role="user", content="hi")]}
    base.update(overrides)
    return GenerationRequest(**base)  # type: ignore[arg-type]


def _response(model: str, **overrides: object) -> ModelResponse[None]:
    base: dict[str, object] = {"model": model, "provider": "mock", "latency_ms": 1.0}
    base.update(overrides)
    return ModelResponse[None](**base)  # type: ignore[arg-type]


def _embedding_response(model: str) -> EmbeddingResponse:
    return EmbeddingResponse(
        model=model, provider="mock", latency_ms=1.0, vectors=[[0.1, 0.2]], dimensions=2
    )


def _rerank_response(model: str) -> RerankResponse:
    return RerankResponse(
        model=model,
        provider="mock",
        latency_ms=1.0,
        rankings=[RerankResult(index=0, score=0.9)],
    )


def _router(
    routes: dict[ModelRole, Route],
    providers: dict[str, Any],
    registry: CapabilityRegistry,
    *,
    retry_policy: RetryPolicy | None = None,
    breakers: CircuitBreakerRegistry | None = None,
    trace: Callable[[ModelInvocationRecord], None] | None = None,
) -> ModelRouter:
    return ModelRouter(
        routes,
        providers,
        registry,
        retry_policy=retry_policy or RetryPolicy(max_attempts=2, sleep=_no_sleep, rand=lambda: 0.5),
        breakers=breakers or CircuitBreakerRegistry(failure_threshold=1),
        limiter=ConcurrencyLimiter(per_provider=4, per_model=4),
        cache_enabled=False,
        trace=trace,
    )


def _make_settings(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> Settings:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    monkeypatch.setenv("AUTH_MODE", "dev")
    set_unit_s3_env(monkeypatch)
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)
    return load_settings(env_file=None)


# --- resolve_routes -------------------------------------------------------


def test_resolve_routes_defaults_match_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = CapabilityRegistry.load()
    settings = _make_settings(monkeypatch)
    routes = resolve_routes(settings, registry)
    for role in MODEL_ROLES:
        default = registry.default_routes[role]
        assert routes[role].primary == default.primary
        assert routes[role].fallbacks == tuple(default.fallbacks)
        assert routes[role].provider == "nvidia"


def test_resolve_routes_env_overrides_primary_and_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = CapabilityRegistry.load()
    settings = _make_settings(
        monkeypatch,
        NVIDIA_MODEL_PLANNER="nvidia/nemotron-3.5-lightning-30b-a3b",
        NVIDIA_MODEL_PLANNER_FALLBACKS="moonshotai/kimi-k3,deepseek-ai/deepseek-v4-flash-0731",
    )
    routes = resolve_routes(settings, registry)
    assert routes["planner"].primary == "nvidia/nemotron-3.5-lightning-30b-a3b"
    assert routes["planner"].fallbacks == (
        "moonshotai/kimi-k3",
        "deepseek-ai/deepseek-v4-flash-0731",
    )
    # Unaffected roles keep the catalog defaults.
    assert routes["extractor"].primary == registry.default_routes["extractor"].primary


def test_resolve_routes_fallback_duplicate_of_primary_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = CapabilityRegistry.load()
    settings = _make_settings(
        monkeypatch,
        NVIDIA_MODEL_PLANNER="nvidia/nemotron-3.5-lightning-30b-a3b",
        NVIDIA_MODEL_PLANNER_FALLBACKS="nvidia/nemotron-3.5-lightning-30b-a3b,moonshotai/kimi-k3",
    )
    routes = resolve_routes(settings, registry)
    assert routes["planner"].fallbacks == ("moonshotai/kimi-k3",)


def test_resolve_routes_disables_embedding_and_reranker_via_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = CapabilityRegistry.load()
    settings = _make_settings(monkeypatch, EMBEDDING_PROVIDER="none", RERANKER_PROVIDER="none")
    routes = resolve_routes(settings, registry)
    assert "embedding" not in routes
    assert "reranker" not in routes
    assert "planner" in routes


def test_resolve_routes_collects_multiple_problems_with_env_prefixes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = CapabilityRegistry.load()
    settings = _make_settings(
        monkeypatch,
        NVIDIA_MODEL_PLANNER="not-a-real-model",
        NVIDIA_MODEL_EVALUATOR="also-not-real",
    )
    with pytest.raises(ConfigurationError) as excinfo:
        resolve_routes(settings, registry)
    problems = excinfo.value.problems
    assert len(problems) == 2
    assert any(p.startswith("NVIDIA_MODEL_PLANNER:") for p in problems)
    assert any(p.startswith("NVIDIA_MODEL_EVALUATOR:") for p in problems)
    assert all("not in the model catalog" in p for p in problems)


def test_resolve_routes_unknown_fallback_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = CapabilityRegistry.load()
    settings = _make_settings(monkeypatch, NVIDIA_MODEL_PLANNER_FALLBACKS="unknown-model")
    with pytest.raises(ConfigurationError) as excinfo:
        resolve_routes(settings, registry)
    assert any(p.startswith("NVIDIA_MODEL_PLANNER_FALLBACKS:") for p in excinfo.value.problems)


# --- ModelRouter: retries and fallback ------------------------------------


async def test_retry_succeeds_on_primary_without_fallback() -> None:
    registry = _registry([_model("m1"), _model("m2")])
    provider = FakeProvider()
    provider.script("generate", "m1", [ProviderUnavailableError("down"), _response("m1")])
    routes: dict[ModelRole, Route] = {
        "drafter": Route(role="drafter", primary="m1", fallbacks=("m2",), provider="mock")
    }
    router = _router(routes, {"mock": provider}, registry)
    response = await router.generate("drafter", _request())
    assert response.attempts == 2
    assert response.fallback_used is False
    assert ("generate", "m2") not in provider.calls


async def test_fallback_used_after_retries_exhausted_rate_limited() -> None:
    registry = _registry([_model("m1"), _model("m2")])
    provider = FakeProvider()
    provider.script("generate", "m1", [ProviderRateLimitedError(), ProviderRateLimitedError()])
    provider.script("generate", "m2", [_response("m2")])
    routes: dict[ModelRole, Route] = {
        "drafter": Route(role="drafter", primary="m1", fallbacks=("m2",), provider="mock")
    }
    router = _router(
        routes,
        {"mock": provider},
        registry,
        retry_policy=RetryPolicy(max_attempts=2, sleep=_no_sleep, rand=lambda: 0.5),
    )
    response = await router.generate("drafter", _request())
    assert response.fallback_used is True
    assert response.attempts == 1
    assert router.last_errors()["m1"]["code"] == ProviderRateLimitedError.code


async def test_fallback_on_unavailable_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    from northforge.providers.errors import ProviderTimeoutError

    registry = _registry([_model("m1"), _model("m2")])
    provider = FakeProvider()
    provider.script("generate", "m1", [ProviderTimeoutError("slow")])
    provider.script("generate", "m2", [_response("m2")])
    routes: dict[ModelRole, Route] = {
        "drafter": Route(role="drafter", primary="m1", fallbacks=("m2",), provider="mock")
    }
    router = _router(
        routes,
        {"mock": provider},
        registry,
        retry_policy=RetryPolicy(max_attempts=1, sleep=_no_sleep, rand=lambda: 0.5),
    )
    response = await router.generate("drafter", _request())
    assert response.fallback_used is True
    assert response.model == "m2"


@pytest.mark.parametrize(
    "error",
    [
        ProviderRequestError("bad request"),
        ProviderMalformedOutputError("bad output"),
        ProviderCapabilityError("capability mismatch"),
    ],
)
async def test_no_fallback_for_non_fallback_eligible_errors(error: ProviderError) -> None:
    registry = _registry([_model("m1"), _model("m2")])
    provider = FakeProvider()
    provider.script("generate", "m1", [error])
    routes: dict[ModelRole, Route] = {
        "drafter": Route(role="drafter", primary="m1", fallbacks=("m2",), provider="mock")
    }
    router = _router(
        routes,
        {"mock": provider},
        registry,
        retry_policy=RetryPolicy(max_attempts=3, sleep=_no_sleep),
    )
    with pytest.raises(type(error)):
        await router.generate("drafter", _request())
    assert ("generate", "m2") not in provider.calls


# --- ModelRouter: capability skip and circuit breaker ---------------------


async def test_capability_skip_falls_back_with_warning() -> None:
    registry = _registry([_model("d1", supports_tools=False), _model("d2", supports_tools=True)])
    provider = FakeProvider()
    provider.script("tool_call", "d2", [_response("d2")])
    routes: dict[ModelRole, Route] = {
        "drafter": Route(role="drafter", primary="d1", fallbacks=("d2",), provider="mock")
    }
    router = _router(routes, {"mock": provider}, registry)
    response = await router.tool_call("drafter", _request(), tools=[])
    assert response.fallback_used is True
    assert any("does not support tool calls" in warning for warning in response.warnings)
    assert ("tool_call", "d1") not in provider.calls


async def test_breaker_open_skips_primary() -> None:
    registry = _registry([_model("m1"), _model("m2")])
    provider = FakeProvider()
    provider.script("generate", "m2", [_response("m2")])
    routes: dict[ModelRole, Route] = {
        "drafter": Route(role="drafter", primary="m1", fallbacks=("m2",), provider="mock")
    }
    breakers = CircuitBreakerRegistry(failure_threshold=1)
    breakers.get("m1").record_failure()
    router = _router(routes, {"mock": provider}, registry, breakers=breakers)
    response = await router.generate("drafter", _request())
    assert response.fallback_used is True
    assert ("generate", "m1") not in provider.calls
    assert router.last_errors()["m1"]["code"] == ProviderCircuitOpenError.code


# --- ModelRouter: trace ----------------------------------------------------


async def test_trace_receives_ok_record() -> None:
    registry = _registry([_model("m1")])
    provider = FakeProvider()
    provider.script("generate", "m1", [_response("m1")])
    routes: dict[ModelRole, Route] = {
        "drafter": Route(role="drafter", primary="m1", fallbacks=(), provider="mock")
    }
    records: list[ModelInvocationRecord] = []
    router = _router(routes, {"mock": provider}, registry, trace=records.append)
    await router.generate("drafter", _request())
    assert len(records) == 1
    assert records[0].outcome == "ok"
    assert records[0].role == "drafter"


async def test_trace_receives_error_record() -> None:
    registry = _registry([_model("m1")])
    provider = FakeProvider()
    provider.script("generate", "m1", [ProviderRequestError("bad")])
    routes: dict[ModelRole, Route] = {
        "drafter": Route(role="drafter", primary="m1", fallbacks=(), provider="mock")
    }
    records: list[ModelInvocationRecord] = []
    router = _router(routes, {"mock": provider}, registry, trace=records.append)
    with pytest.raises(ProviderRequestError):
        await router.generate("drafter", _request())
    assert len(records) == 1
    assert records[0].outcome == ProviderRequestError.code


async def test_trace_callback_raising_does_not_break_call() -> None:
    registry = _registry([_model("m1")])
    provider = FakeProvider()
    provider.script("generate", "m1", [_response("m1")])
    routes: dict[ModelRole, Route] = {
        "drafter": Route(role="drafter", primary="m1", fallbacks=(), provider="mock")
    }

    def bad_trace(record: ModelInvocationRecord) -> None:
        raise RuntimeError("boom")

    router = _router(routes, {"mock": provider}, registry, trace=bad_trace)
    response = await router.generate("drafter", _request())
    assert response.model == "m1"


# --- ModelRouter: metadata, embed/rerank, config errors -------------------


async def test_role_metadata_injected() -> None:
    registry = _registry([_model("m1")])
    provider = FakeProvider()
    provider.script("generate", "m1", [_response("m1")])
    routes: dict[ModelRole, Route] = {
        "drafter": Route(role="drafter", primary="m1", fallbacks=(), provider="mock")
    }
    router = _router(routes, {"mock": provider}, registry)
    await router.generate("drafter", _request(metadata={"step": "s1"}))
    assert provider.requests[-1].metadata == {"role": "drafter", "step": "s1"}


async def test_embed_and_rerank_routed() -> None:
    registry = _registry([_embedding_model("e1"), _rerank_model("r1")])
    provider = FakeProvider()
    provider.script("embed", "e1", [_embedding_response("e1")])
    provider.script("rerank", "r1", [_rerank_response("r1")])
    routes: dict[ModelRole, Route] = {
        "embedding": Route(role="embedding", primary="e1", fallbacks=(), provider="mock"),
        "reranker": Route(role="reranker", primary="r1", fallbacks=(), provider="mock"),
    }
    router = _router(routes, {"mock": provider}, registry)
    embed_response = await router.embed(EmbeddingRequest(texts=["a"]))
    rerank_response = await router.rerank(RerankRequest(query="q", passages=["p"]))
    assert embed_response.model == "e1"
    assert rerank_response.model == "r1"


async def test_disabled_role_raises_not_configured() -> None:
    registry = _registry([_model("m1")])
    router = _router({}, {}, registry)
    with pytest.raises(ProviderNotConfiguredError):
        await router.generate("drafter", _request())


async def test_missing_provider_raises_not_configured() -> None:
    registry = _registry([_model("m1")])
    routes: dict[ModelRole, Route] = {
        "drafter": Route(role="drafter", primary="m1", fallbacks=(), provider="mock")
    }
    router = _router(routes, {}, registry)
    with pytest.raises(ProviderNotConfiguredError):
        await router.generate("drafter", _request())


async def test_snapshot_json_serialisable_and_secret_free(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = CapabilityRegistry.load()
    settings = _make_settings(monkeypatch, NVIDIA_API_KEY="nvapi-test-secret")
    routes = resolve_routes(settings, registry)
    provider = FakeProvider(name="nvidia")
    router = _router(routes, {"nvidia": provider}, registry)
    snapshot = router.snapshot()
    text = json.dumps(snapshot)
    assert "nvapi" not in text
    assert set(snapshot["roles"].keys()) == set(MODEL_ROLES)
    assert snapshot["catalog_version"] == registry.version
    assert snapshot["cache_enabled"] is False


async def test_provider_failure_wins_over_capability_skipped_fallback() -> None:
    """Primary rate-limited, only fallback lacks tools: the caller sees the rate limit."""
    from northforge.providers.errors import ProviderRateLimitedError

    registry = CapabilityRegistry.load()
    routes: dict[ModelRole, Route] = {
        "planner": Route(
            role="planner",
            primary="nvidia/nemotron-3-super-120b-a12b",
            fallbacks=("nvidia/nemotron-3-embed-1b",),
            provider="mock",
        )
    }
    provider = FakeProvider()
    provider.script(
        "tool_call",
        "nvidia/nemotron-3-super-120b-a12b",
        [ProviderRateLimitedError(retry_after_seconds=0.0), ProviderRateLimitedError()],
    )
    router = _router(routes, {"mock": provider}, registry)

    with pytest.raises(ProviderRateLimitedError):
        await router.tool_call("planner", _request(), [])
    assert provider.calls == [("tool_call", "nvidia/nemotron-3-super-120b-a12b")] * 2
