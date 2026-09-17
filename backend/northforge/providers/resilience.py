"""Retry, circuit breaking, and concurrency limiting for model providers.

The hosted NVIDIA endpoint is quota-limited and occasionally flaky, so the
router never calls a provider directly: every attempt goes through a
``RetryPolicy`` (bounded exponential backoff, honouring a provider's
``Retry-After`` when it gives one), a per-model ``CircuitBreaker`` (stops
hammering a model that is failing repeatedly), and a ``ConcurrencyLimiter``
(caps in-flight requests per provider and per model so NorthForge itself
never exceeds the quota it was given). These three are deliberately
separate from the router's fallback logic in ``router.py``: this module
only concerns itself with *one* model at a time.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import Literal

from northforge.providers.errors import ProviderError

logger = logging.getLogger(__name__)

CircuitState = Literal["closed", "open", "half_open"]


class RetryPolicy:
    """Bounded exponential backoff for a single model call.

    Only ``ProviderError.retryable`` failures are retried; any other
    exception (a bug, or a non-provider error) propagates immediately so it
    is never mistaken for a transient condition. When a provider reports
    ``retry_after_seconds`` (rate limiting), that value is honoured instead
    of the computed backoff, still capped at ``cap_seconds``.
    """

    def __init__(
        self,
        *,
        max_attempts: int = 3,
        base_seconds: float = 0.5,
        cap_seconds: float = 8.0,
        jitter_fraction: float = 0.1,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rand: Callable[[], float] = random.random,
    ) -> None:
        self.max_attempts = max_attempts
        self.base_seconds = base_seconds
        self.cap_seconds = cap_seconds
        self.jitter_fraction = jitter_fraction
        self._sleep = sleep
        self._rand = rand

    def delay_for(self, attempt: int, error: ProviderError) -> float:
        """Seconds to wait before ``attempt`` (1-indexed retry attempt)."""
        retry_after = getattr(error, "retry_after_seconds", None)
        if isinstance(retry_after, int | float):
            return min(float(retry_after), self.cap_seconds)
        base_delay: float = min(self.base_seconds * (2 ** (attempt - 1)), self.cap_seconds)
        jitter: float = base_delay * self.jitter_fraction * (2 * self._rand() - 1)
        result: float = max(0.0, base_delay + jitter)
        return result

    async def run[T](self, fn: Callable[[], Awaitable[T]]) -> tuple[T, int]:
        """Call ``fn`` until it succeeds or retries are exhausted.

        Returns ``(value, attempts)``. On final failure the last
        ``ProviderError`` is re-raised with ``attempts`` set on the
        instance so callers can record how many tries were made.
        """
        attempt = 1
        while True:
            try:
                value = await fn()
            except ProviderError as exc:
                if exc.retryable and attempt < self.max_attempts:
                    delay = self.delay_for(attempt, exc)
                    await self._sleep(delay)
                    attempt += 1
                    continue
                # ``ProviderError`` does not declare ``attempts`` (it belongs to the
                # resilience layer, not the error taxonomy); set it dynamically so
                # callers can read how many tries were made.
                exc.attempts = attempt  # type: ignore[attr-defined]
                raise
            else:
                return value, attempt


class CircuitBreaker:
    """Per-model failure tracker: opens after repeated failures within a window.

    ``closed`` allows every call. After ``failure_threshold`` failures
    inside ``window_seconds``, the breaker ``open``s and rejects calls for
    ``open_seconds``; it then allows exactly one ``half_open`` probe. A
    successful probe closes the breaker; a failed probe re-opens it
    immediately.
    """

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        window_seconds: float = 60.0,
        open_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.window_seconds = window_seconds
        self.open_seconds = open_seconds
        self._clock = clock
        self._failures: list[float] = []
        self._opened_at: float | None = None
        self._probe_in_flight = False

    def _prune(self) -> None:
        cutoff = self._clock() - self.window_seconds
        self._failures = [ts for ts in self._failures if ts >= cutoff]

    @property
    def state(self) -> CircuitState:
        if self._opened_at is None:
            return "closed"
        if self._clock() - self._opened_at >= self.open_seconds:
            return "half_open"
        return "open"

    def allow(self) -> bool:
        current = self.state
        if current == "closed":
            return True
        if current == "open":
            return False
        # half_open: allow exactly one probe at a time.
        if self._probe_in_flight:
            return False
        self._probe_in_flight = True
        return True

    def record_success(self) -> None:
        self._failures.clear()
        self._opened_at = None
        self._probe_in_flight = False

    def record_failure(self) -> None:
        was_half_open = self.state == "half_open"
        self._probe_in_flight = False
        if was_half_open:
            self._opened_at = self._clock()
            return
        self._failures.append(self._clock())
        self._prune()
        if len(self._failures) >= self.failure_threshold:
            self._opened_at = self._clock()


class CircuitBreakerRegistry:
    """Lazily creates and remembers one ``CircuitBreaker`` per model id."""

    def __init__(self, **breaker_kwargs: object) -> None:
        self._breaker_kwargs = breaker_kwargs
        self._breakers: dict[str, CircuitBreaker] = {}

    def get(self, model: str) -> CircuitBreaker:
        breaker = self._breakers.get(model)
        if breaker is None:
            breaker = CircuitBreaker(**self._breaker_kwargs)  # type: ignore[arg-type]
            self._breakers[model] = breaker
        return breaker

    def states(self) -> dict[str, CircuitState]:
        return {model: breaker.state for model, breaker in self._breakers.items()}


class ConcurrencyLimiter:
    """Caps concurrent in-flight requests per provider and per model.

    Semaphores are created lazily on first use of a given provider or model
    key, so the limiter never needs to know the full set of providers or
    models up front.
    """

    def __init__(self, per_provider: int, per_model: int) -> None:
        self._per_provider = per_provider
        self._per_model = per_model
        self._provider_semaphores: dict[str, asyncio.Semaphore] = {}
        self._model_semaphores: dict[str, asyncio.Semaphore] = {}

    def _provider_semaphore(self, provider: str) -> asyncio.Semaphore:
        semaphore = self._provider_semaphores.get(provider)
        if semaphore is None:
            semaphore = asyncio.Semaphore(self._per_provider)
            self._provider_semaphores[provider] = semaphore
        return semaphore

    def _model_semaphore(self, model: str) -> asyncio.Semaphore:
        semaphore = self._model_semaphores.get(model)
        if semaphore is None:
            semaphore = asyncio.Semaphore(self._per_model)
            self._model_semaphores[model] = semaphore
        return semaphore

    class _Slot:
        def __init__(self, provider_sem: asyncio.Semaphore, model_sem: asyncio.Semaphore) -> None:
            self._provider_sem = provider_sem
            self._model_sem = model_sem

        async def __aenter__(self) -> None:
            await self._provider_sem.acquire()
            try:
                await self._model_sem.acquire()
            except BaseException:
                self._provider_sem.release()
                raise

        async def __aexit__(self, *exc_info: object) -> None:
            self._model_sem.release()
            self._provider_sem.release()

    def slot(self, provider: str, model: str) -> ConcurrencyLimiter._Slot:
        """Async context manager acquiring the provider slot, then the model slot."""
        return ConcurrencyLimiter._Slot(
            self._provider_semaphore(provider), self._model_semaphore(model)
        )


__all__ = [
    "CircuitBreaker",
    "CircuitBreakerRegistry",
    "CircuitState",
    "ConcurrencyLimiter",
    "RetryPolicy",
]
