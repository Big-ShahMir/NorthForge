"""Role-to-model routing: resolves configuration into routes and executes calls.

``resolve_routes`` turns the model catalog's defaults plus environment
overrides into one validated ``Route`` per enabled role, failing fast at
startup with every misconfiguration named at once (rather than one model
at a time, the first time each role is used). ``ModelRouter`` is the single
place that knows how to turn a role into a model call: it walks a route's
candidates (primary, then fallbacks), skipping models that cannot perform
the operation, respecting each model's circuit breaker, retrying transient
failures, and falling back to the next candidate only when the failure
category justifies it (see ``FALLBACK_CATEGORIES`` in ``errors.py``).

Callers never pick a model themselves; they ask for a role.
"""

from __future__ import annotations

import functools
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel

from northforge.core.config import Settings
from northforge.core.errors import ConfigurationError
from northforge.providers.base import ModelProvider
from northforge.providers.capabilities import CapabilityRegistry
from northforge.providers.errors import (
    ProviderCapabilityError,
    ProviderCircuitOpenError,
    ProviderError,
    ProviderNotConfiguredError,
)
from northforge.providers.resilience import (
    CircuitBreakerRegistry,
    CircuitState,
    ConcurrencyLimiter,
    RetryPolicy,
)
from northforge.providers.types import (
    MODEL_ROLES,
    EmbeddingRequest,
    EmbeddingResponse,
    GenerationRequest,
    ModelInvocationRecord,
    ModelResponse,
    ModelRole,
    Operation,
    RerankRequest,
    RerankResponse,
    ToolDefinition,
    Usage,
)

logger = logging.getLogger(__name__)

# Environment variable names used in ``ConfigurationError`` problem prefixes.
_PRIMARY_ENV: dict[ModelRole, str] = {
    "planner": "NVIDIA_MODEL_PLANNER",
    "extractor": "NVIDIA_MODEL_EXTRACTION",
    "drafter": "NVIDIA_MODEL_DRAFTER",
    "evaluator": "NVIDIA_MODEL_EVALUATOR",
    "embedding": "EMBEDDING_MODEL",
    "reranker": "RERANKER_MODEL",
}
_FALLBACKS_ENV: dict[ModelRole, str | None] = {
    "planner": "NVIDIA_MODEL_PLANNER_FALLBACKS",
    "extractor": "NVIDIA_MODEL_EXTRACTION_FALLBACKS",
    "drafter": "NVIDIA_MODEL_DRAFTER_FALLBACKS",
    "evaluator": "NVIDIA_MODEL_EVALUATOR_FALLBACKS",
    # Embedding and reranker fallbacks have no environment override; they
    # always come from the catalog's default routes.
    "embedding": None,
    "reranker": None,
}

# Failure categories that count toward opening a model's circuit breaker.
_BREAKER_CATEGORIES: frozenset[str] = frozenset({"unavailable", "timeout", "rate_limited"})


@dataclass(frozen=True)
class Route:
    """One role's resolved model selection: which model to try, in what order."""

    role: ModelRole
    primary: str
    fallbacks: tuple[str, ...]
    provider: str


def resolve_routes(settings: Settings, registry: CapabilityRegistry) -> dict[ModelRole, Route]:
    """Resolve every role's route from the catalog's defaults and environment overrides.

    A role is omitted from the result when its provider is configured as
    ``"none"`` (only possible for embedding and reranker). Every remaining
    primary and fallback model is validated against ``registry.satisfies``;
    all problems across all roles are collected and raised together so a
    misconfiguration is reported completely, not one role at a time.
    """
    defaults = registry.default_routes
    primary_overrides: dict[ModelRole, str | None] = {
        "planner": settings.nvidia_model_planner,
        "extractor": settings.nvidia_model_extraction,
        "drafter": settings.nvidia_model_drafter,
        "evaluator": settings.nvidia_model_evaluator,
        "embedding": settings.embedding_model,
        "reranker": settings.reranker_model,
    }
    fallback_overrides: dict[ModelRole, list[str] | None] = {
        "planner": settings.nvidia_model_planner_fallbacks,
        "extractor": settings.nvidia_model_extraction_fallbacks,
        "drafter": settings.nvidia_model_drafter_fallbacks,
        "evaluator": settings.nvidia_model_evaluator_fallbacks,
        "embedding": None,
        "reranker": None,
    }
    provider_for_role: dict[ModelRole, str] = {
        "planner": settings.model_provider,
        "extractor": settings.model_provider,
        "drafter": settings.model_provider,
        "evaluator": settings.model_provider,
        "embedding": settings.embedding_provider,
        "reranker": settings.reranker_provider,
    }

    routes: dict[ModelRole, Route] = {}
    problems: list[str] = []
    for role in MODEL_ROLES:
        provider_name = provider_for_role[role]
        if provider_name == "none":
            continue
        default = defaults[role]
        primary = primary_overrides[role] or default.primary
        fallback_override = fallback_overrides[role]
        fallback_source = fallback_override if fallback_override is not None else default.fallbacks
        fallbacks = tuple(model for model in fallback_source if model != primary)

        primary_env = _PRIMARY_ENV[role]
        for problem in registry.satisfies(primary, role):
            problems.append(f"{primary_env}: {problem}")

        fallback_env = _FALLBACKS_ENV[role] or "MODEL_CAPABILITIES_FILE"
        for fallback_model in fallbacks:
            for problem in registry.satisfies(fallback_model, role):
                problems.append(f"{fallback_env}: {problem}")

        routes[role] = Route(
            role=role, primary=primary, fallbacks=fallbacks, provider=provider_name
        )

    if problems:
        raise ConfigurationError(
            f"Model routing is invalid ({len(problems)} problem(s))", problems=problems
        )
    return routes


class _RoutedResponse(Protocol):
    """Structural shape shared by every response type the router hands back."""

    model: str
    warnings: list[str]
    cache_hit: bool
    attempts: int
    fallback_used: bool
    usage: Usage
    request_id: str | None


def _error_info(error: ProviderError) -> dict[str, Any]:
    return {
        "code": error.code,
        "category": error.category,
        "at": datetime.now(UTC).isoformat(),
    }


class ModelRouter:
    """Executes model calls by role: candidate selection, retries, breakers, fallback."""

    def __init__(
        self,
        routes: Mapping[ModelRole, Route],
        providers: Mapping[str, ModelProvider],
        registry: CapabilityRegistry,
        *,
        retry_policy: RetryPolicy,
        breakers: CircuitBreakerRegistry,
        limiter: ConcurrencyLimiter,
        cache_enabled: bool,
        trace: Callable[[ModelInvocationRecord], None] | None = None,
    ) -> None:
        self._routes = dict(routes)
        self._providers = dict(providers)
        self._registry = registry
        self._retry_policy = retry_policy
        self._breakers = breakers
        self._limiter = limiter
        self._cache_enabled = cache_enabled
        self._trace = trace
        self._last_errors: dict[str, dict[str, Any]] = {}

    def route(self, role: ModelRole) -> Route:
        route = self._routes.get(role)
        if route is None:
            raise ProviderNotConfiguredError(f"Model role {role!r} is disabled or not configured.")
        return route

    def provider_for(self, route: Route) -> ModelProvider:
        provider = self._providers.get(route.provider)
        if provider is None:
            raise ProviderNotConfiguredError(
                f"No provider registered for {route.provider!r} (role {route.role!r})."
            )
        return provider

    def providers(self) -> dict[str, ModelProvider]:
        """The providers by name, as configured (cache wrappers included)."""
        return dict(self._providers)

    def roles(self) -> list[ModelRole]:
        return list(self._routes.keys())

    def last_errors(self) -> dict[str, dict[str, Any]]:
        return dict(self._last_errors)

    def breaker_states(self) -> dict[str, CircuitState]:
        return self._breakers.states()

    def _with_role_metadata(self, role: ModelRole, request: GenerationRequest) -> GenerationRequest:
        return request.model_copy(update={"metadata": {"role": role, **request.metadata}})

    def _emit_trace(
        self,
        *,
        operation: Operation,
        route: Route,
        model: str,
        outcome: str,
        latency_ms: float,
        role: ModelRole,
        attempts: int,
        cache_hit: bool,
        fallback_used: bool,
        usage: Usage,
        request_id: str | None,
    ) -> None:
        if self._trace is None:
            return
        record = ModelInvocationRecord(
            operation=operation,
            provider=route.provider,
            model=model,
            outcome=outcome,
            latency_ms=latency_ms,
            role=role,
            attempts=attempts,
            cache_hit=cache_hit,
            fallback_used=fallback_used,
            usage=usage,
            request_id=request_id,
        )
        try:
            self._trace(record)
        except Exception as exc:
            logger.warning("model trace callback failed", extra={"error": repr(exc)})

    async def _execute[ResponseT: _RoutedResponse](
        self,
        role: ModelRole,
        operation: Operation,
        call: Callable[[ModelProvider, str], Awaitable[ResponseT]],
        *,
        extra_requirement: str | None = None,
    ) -> ResponseT:
        started = time.perf_counter()
        route = self.route(role)
        candidates = [route.primary, *route.fallbacks]
        skip_reasons: list[str] = []
        # The most recent fallback-eligible failure. If every remaining
        # candidate is then skipped on capability grounds, this -- not a
        # capability error -- is what the caller must see.
        last_error: ProviderError | None = None

        for index, model in enumerate(candidates):
            is_last = index == len(candidates) - 1
            problems = self._registry.satisfies(model, role)
            if extra_requirement is not None:
                capabilities = self._registry.get(model)
                if capabilities is not None:
                    if extra_requirement == "tools" and not capabilities.supports_tools:
                        problems = [*problems, f"model {model!r} does not support tool calls"]
                    elif (
                        extra_requirement == "structured"
                        and not capabilities.supports_structured_output
                    ):
                        problems = [
                            *problems,
                            f"model {model!r} does not support structured output",
                        ]
            if problems:
                skip_reasons.extend(problems)
                continue

            breaker = self._breakers.get(model)
            if not breaker.allow():
                error: ProviderError = ProviderCircuitOpenError(
                    f"circuit open for model {model!r}", provider=route.provider, model=model
                )
                self._last_errors[model] = _error_info(error)
                if error.fallback_eligible and not is_last:
                    last_error = error
                    continue
                self._emit_trace(
                    operation=operation,
                    route=route,
                    model=model,
                    outcome=error.code,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    role=role,
                    attempts=1,
                    cache_hit=False,
                    fallback_used=False,
                    usage=Usage(),
                    request_id=None,
                )
                raise error

            provider = self.provider_for(route)
            try:
                async with self._limiter.slot(route.provider, model):
                    # functools.partial binds provider/model immediately (unlike a
                    # closure), so this is safe to call after the loop advances.
                    bound_call = functools.partial(call, provider, model)
                    response, attempts = await self._retry_policy.run(bound_call)
            except ProviderError as exc:
                if exc.category in _BREAKER_CATEGORIES:
                    breaker.record_failure()
                self._last_errors[model] = _error_info(exc)
                if exc.fallback_eligible and not is_last:
                    last_error = exc
                    continue
                self._emit_trace(
                    operation=operation,
                    route=route,
                    model=model,
                    outcome=exc.code,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    role=role,
                    attempts=getattr(exc, "attempts", 1),
                    cache_hit=False,
                    fallback_used=False,
                    usage=Usage(),
                    request_id=None,
                )
                raise
            else:
                breaker.record_success()
                response.attempts = attempts
                response.fallback_used = model != route.primary
                response.warnings = [*response.warnings, *skip_reasons]
                self._emit_trace(
                    operation=operation,
                    route=route,
                    model=model,
                    outcome="ok",
                    latency_ms=(time.perf_counter() - started) * 1000,
                    role=role,
                    attempts=attempts,
                    cache_hit=response.cache_hit,
                    fallback_used=response.fallback_used,
                    usage=response.usage,
                    request_id=response.request_id,
                )
                return response

        if last_error is not None:
            # A real model failed and the remaining fallbacks could not serve
            # the operation: surface the provider failure, not a capability one.
            self._emit_trace(
                operation=operation,
                route=route,
                model=last_error.model or route.primary,
                outcome=last_error.code,
                latency_ms=(time.perf_counter() - started) * 1000,
                role=role,
                attempts=getattr(last_error, "attempts", 1),
                cache_hit=False,
                fallback_used=False,
                usage=Usage(),
                request_id=None,
            )
            raise last_error

        # Every candidate was skipped for lacking a required capability; no
        # call was ever attempted, so there is no ProviderError to re-raise.
        raise ProviderCapabilityError(
            f"No candidate model for role {role!r} satisfies operation {operation!r}: "
            + "; ".join(skip_reasons),
            provider=route.provider,
        )

    async def generate(self, role: ModelRole, request: GenerationRequest) -> ModelResponse[None]:
        request = self._with_role_metadata(role, request)

        async def call(provider: ModelProvider, model: str) -> ModelResponse[None]:
            return await provider.generate(request, model)

        return await self._execute(role, "generate", call)

    async def generate_structured[T: BaseModel](
        self, role: ModelRole, request: GenerationRequest, schema: type[T]
    ) -> ModelResponse[T]:
        request = self._with_role_metadata(role, request)

        async def call(provider: ModelProvider, model: str) -> ModelResponse[T]:
            return await provider.generate_structured(request, model, schema)

        return await self._execute(
            role, "generate_structured", call, extra_requirement="structured"
        )

    async def tool_call(
        self, role: ModelRole, request: GenerationRequest, tools: list[ToolDefinition]
    ) -> ModelResponse[None]:
        request = self._with_role_metadata(role, request)

        async def call(provider: ModelProvider, model: str) -> ModelResponse[None]:
            return await provider.tool_call(request, model, tools)

        return await self._execute(role, "tool_call", call, extra_requirement="tools")

    async def embed(
        self, request: EmbeddingRequest, role: ModelRole = "embedding"
    ) -> EmbeddingResponse:
        async def call(provider: ModelProvider, model: str) -> EmbeddingResponse:
            return await provider.embed(request, model)

        return await self._execute(role, "embed", call)

    async def rerank(self, request: RerankRequest, role: ModelRole = "reranker") -> RerankResponse:
        async def call(provider: ModelProvider, model: str) -> RerankResponse:
            return await provider.rerank(request, model)

        return await self._execute(role, "rerank", call)

    def snapshot(self) -> dict[str, Any]:
        """JSON-serialisable, secret-free description of the current routing."""
        roles: dict[str, Any] = {}
        for role in MODEL_ROLES:
            route = self._routes.get(role)
            if route is None:
                roles[role] = {"enabled": False}
                continue
            capabilities = self._registry.get(route.primary)
            roles[role] = {
                "provider": route.provider,
                "model": route.primary,
                "fallbacks": list(route.fallbacks),
                "supports_structured_output": (
                    capabilities.supports_structured_output if capabilities else None
                ),
                "structured_output_mode": (
                    capabilities.structured_output_mode if capabilities else None
                ),
                "supports_tools": capabilities.supports_tools if capabilities else None,
                "context_window": capabilities.context_window if capabilities else None,
            }
        return {
            "catalog_version": self._registry.version,
            "cache_enabled": self._cache_enabled,
            "roles": roles,
        }


__all__ = ["ModelRouter", "Route", "resolve_routes"]
