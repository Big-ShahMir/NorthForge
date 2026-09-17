"""Build the ``ModelRouter`` the API and worker share, from settings alone.

This is the only place that decides which concrete provider serves which
role, whether the cache wraps it, and what happens when credentials are
missing. The rule for missing credentials is deliberate: the application
starts anyway. Documents, search, and workflow editing do not need a model,
so an absent ``NVIDIA_API_KEY`` must not take the whole API down; every
model call instead fails with ``PROVIDER_NOT_CONFIGURED`` and
``GET /api/provider-status`` reports ``configured: false``.

Routing problems (a model missing from the catalog, or lacking what its
role needs) are configuration errors and do stop startup, because they
would otherwise surface only when the planner first runs.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from redis.asyncio import Redis

from northforge.core.config import Settings
from northforge.providers.base import ModelProvider, NotConfiguredProvider
from northforge.providers.cache import CacheBackend, CachingProvider, RedisCacheBackend
from northforge.providers.capabilities import CapabilityRegistry
from northforge.providers.mock import MockProvider
from northforge.providers.nvidia import NvidiaProvider
from northforge.providers.resilience import (
    CircuitBreakerRegistry,
    ConcurrencyLimiter,
    RetryPolicy,
)
from northforge.providers.router import ModelRouter, resolve_routes
from northforge.providers.types import ModelInvocationRecord

logger = logging.getLogger(__name__)

_NOT_CONFIGURED_REASON = (
    "No model provider credentials are configured; set NVIDIA_API_KEY "
    "(or MODEL_PROVIDER=mock outside production)."
)


def build_model_router(
    settings: Settings,
    redis: Redis | None = None,
    *,
    cache_backend: CacheBackend | None = None,
    trace: Callable[[ModelInvocationRecord], None] | None = None,
) -> ModelRouter:
    """Construct the router with every provider the configured routes need.

    ``redis`` (or an explicit ``cache_backend``) enables the deterministic
    request cache when ``settings.model_cache_active`` is true; with neither,
    responses are never cached. Raises ``ConfigurationError`` for invalid
    routing.
    """
    registry = CapabilityRegistry.load(settings.model_capabilities_file)
    routes = resolve_routes(settings, registry)

    needed = {route.provider for route in routes.values()}
    providers: dict[str, ModelProvider] = {}
    if "nvidia" in needed:
        if settings.nvidia_api_key is None:
            logger.warning("model provider not configured; model calls will fail until fixed")
            providers["nvidia"] = NotConfiguredProvider(_NOT_CONFIGURED_REASON)
        else:
            providers["nvidia"] = NvidiaProvider(settings, registry)
    if "mock" in needed:
        providers["mock"] = MockProvider()

    backend = cache_backend
    if backend is None and redis is not None:
        backend = RedisCacheBackend(redis)
    cache_enabled = settings.model_cache_active and backend is not None
    if cache_enabled:
        assert backend is not None
        providers = {
            name: CachingProvider(provider, backend, ttl_seconds=settings.model_cache_ttl_seconds)
            for name, provider in providers.items()
        }

    router = ModelRouter(
        routes,
        providers,
        registry,
        retry_policy=RetryPolicy(max_attempts=settings.model_max_attempts),
        breakers=CircuitBreakerRegistry(),
        limiter=ConcurrencyLimiter(
            per_provider=settings.model_provider_concurrency,
            per_model=settings.model_per_model_concurrency,
        ),
        cache_enabled=cache_enabled,
        trace=trace,
    )
    logger.info(
        "model router ready",
        extra={
            "providers": sorted(providers),
            "configured": settings.model_provider == "mock" or settings.nvidia_api_key is not None,
            "cache_enabled": cache_enabled,
            "roles": {role: route.primary for role, route in routes.items()},
        },
    )
    return router


async def close_model_router(router: ModelRouter) -> None:
    """Release HTTP connections held by the providers behind ``router``."""
    for provider in router.providers().values():
        inner = provider.inner if isinstance(provider, CachingProvider) else provider
        if isinstance(inner, NvidiaProvider):
            await inner.aclose()


__all__ = ["build_model_router", "close_model_router"]
