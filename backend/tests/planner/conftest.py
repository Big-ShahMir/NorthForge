"""Shared planner test helpers, importable as plain functions (no fixtures).

``test_service.py`` (owned by another agent, persisting through a real
database session) imports these directly, so they must not depend on
pytest fixtures such as ``monkeypatch``.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import SecretStr

from northforge.core.config import Settings
from northforge.planner.graph import PlannerDeps
from northforge.planner.schema import GroundingSummary
from northforge.providers.capabilities import CapabilityRegistry
from northforge.providers.mock import MockProvider, ScriptedFailure
from northforge.providers.resilience import CircuitBreakerRegistry, ConcurrencyLimiter, RetryPolicy
from northforge.providers.router import ModelRouter, resolve_routes
from northforge.providers.types import Operation, ToolCallRequest
from northforge.tools.context import ToolContext
from northforge.tools.registry import default_registry
from tests.tools.conftest import make_context

_UNIT_DATABASE_URL = "postgresql://northforge:secret@127.0.0.1:1/northforge"
_UNIT_REDIS_URL = "redis://127.0.0.1:1/0"


async def _no_sleep(seconds: float) -> None:
    del seconds


def _mock_settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        app_env="test",
        database_url=_UNIT_DATABASE_URL,
        redis_url=_UNIT_REDIS_URL,
        auth_mode="dev",
        model_provider="mock",
        embedding_provider="mock",
        reranker_provider="mock",
        s3_endpoint="http://127.0.0.1:1",
        s3_bucket="northforge-test",
        s3_access_key=SecretStr("unit-test-access-key"),
        s3_secret_key=SecretStr("unit-test-secret-key"),
    )


def build_mock_router(
    *,
    structured: Sequence[object] = (),
    tool_calls: Sequence[list[ToolCallRequest]] = (),
    failures: Sequence[ScriptedFailure] = (),
) -> tuple[ModelRouter, MockProvider]:
    """Build a ``ModelRouter`` over a scriptable ``MockProvider`` for planner tests.

    Mirrors ``tests/providers/test_factory.py``'s mock settings and
    ``tests/providers/test_router.py``'s router construction: real routes
    resolved from the catalog (``MODEL_PROVIDER=mock`` routes every
    generation role to the ``"mock"`` provider), a two-attempt retry policy
    with no real sleeping, and caching disabled.
    """
    settings = _mock_settings()
    registry = CapabilityRegistry.load()
    routes = resolve_routes(settings, registry)

    responses: dict[Operation, Sequence[object]] = {}
    if structured:
        responses["generate_structured"] = list(structured)
    if tool_calls:
        responses["tool_call"] = list(tool_calls)

    provider = MockProvider(responses=responses, failures=list(failures))
    router = ModelRouter(
        routes,
        {"mock": provider},
        registry,
        retry_policy=RetryPolicy(max_attempts=2, sleep=_no_sleep, rand=lambda: 0.5),
        breakers=CircuitBreakerRegistry(),
        limiter=ConcurrencyLimiter(per_provider=4, per_model=4),
        cache_enabled=False,
    )
    return router, provider


def planner_tool_context() -> ToolContext:
    """A fixture-backed ``ToolContext`` (no database), for planner tests."""
    return make_context()


def _default_grounding() -> GroundingSummary:
    return GroundingSummary(
        document_types={"contract": 5, "policy": 3},
        vendors={"Acme Cloud Services": 2},
        policy_areas={"renewal": 2, "liability": 1},
    )


def planner_deps(router: ModelRouter, *, probe_tools: bool = False) -> PlannerDeps:
    """Build ``PlannerDeps`` around ``router`` and a fixture ``ToolContext``."""
    return PlannerDeps(
        model_router=router,
        tool_registry=default_registry(),
        tool_context=planner_tool_context(),
        grounding=_default_grounding(),
        probe_tools=probe_tools,
    )


__all__ = ["build_mock_router", "planner_deps", "planner_tool_context"]
