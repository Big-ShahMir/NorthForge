from __future__ import annotations

import asyncio

import pytest

from northforge.providers.errors import (
    ProviderRateLimitedError,
    ProviderRequestError,
    ProviderUnavailableError,
)
from northforge.providers.resilience import (
    CircuitBreaker,
    CircuitBreakerRegistry,
    CircuitState,
    ConcurrencyLimiter,
    RetryPolicy,
)


def _state(breaker: CircuitBreaker) -> CircuitState:
    """Read ``breaker.state`` through a function call.

    Chained equality asserts directly on ``breaker.state`` between mutating
    calls confuse mypy's narrowing (it treats the property expression as
    unchanged across ``record_failure()``/``clock.advance()`` calls and
    reports later asserts as unreachable); routing through a plain function
    call sidesteps that without weakening the check.
    """
    return breaker.state


def _attempts(error: Exception) -> int:
    """Read the ``attempts`` attribute ``RetryPolicy`` sets dynamically on errors.

    ``ProviderError`` does not declare ``attempts`` (it belongs to the
    resilience layer), so ``getattr`` avoids an ``attr-defined`` mypy error.
    """
    value: int = getattr(error, "attempts")  # noqa: B009
    return value


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _RecordingSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def _no_jitter() -> float:
    return 0.5


# --- RetryPolicy -------------------------------------------------------


async def test_retry_succeeds_after_transient_failures() -> None:
    sleep = _RecordingSleep()
    policy = RetryPolicy(max_attempts=3, base_seconds=1.0, sleep=sleep, rand=_no_jitter)
    attempts_made = 0

    async def flaky() -> str:
        nonlocal attempts_made
        attempts_made += 1
        if attempts_made < 3:
            raise ProviderUnavailableError("down")
        return "ok"

    value, attempts = await policy.run(flaky)
    assert value == "ok"
    assert attempts == 3
    assert len(sleep.calls) == 2


async def test_retry_after_honoured_and_capped() -> None:
    sleep = _RecordingSleep()
    policy = RetryPolicy(max_attempts=2, cap_seconds=5.0, sleep=sleep, rand=_no_jitter)

    async def rate_limited() -> str:
        raise ProviderRateLimitedError(retry_after_seconds=100.0)

    with pytest.raises(ProviderRateLimitedError) as excinfo:
        await policy.run(rate_limited)
    assert sleep.calls == [5.0]
    assert _attempts(excinfo.value) == 2


async def test_non_retryable_raises_immediately() -> None:
    sleep = _RecordingSleep()
    policy = RetryPolicy(max_attempts=5, sleep=sleep, rand=_no_jitter)

    async def bad_request() -> str:
        raise ProviderRequestError("nope")

    with pytest.raises(ProviderRequestError) as excinfo:
        await policy.run(bad_request)
    assert sleep.calls == []
    assert _attempts(excinfo.value) == 1


async def test_non_provider_error_passes_through() -> None:
    sleep = _RecordingSleep()
    policy = RetryPolicy(max_attempts=5, sleep=sleep, rand=_no_jitter)

    async def broken() -> str:
        raise ValueError("bug")

    with pytest.raises(ValueError, match="bug"):
        await policy.run(broken)
    assert sleep.calls == []


async def test_max_attempts_sets_error_attempts() -> None:
    sleep = _RecordingSleep()
    policy = RetryPolicy(max_attempts=4, sleep=sleep, rand=_no_jitter)

    async def always_fails() -> str:
        raise ProviderUnavailableError("down")

    with pytest.raises(ProviderUnavailableError) as excinfo:
        await policy.run(always_fails)
    assert _attempts(excinfo.value) == 4
    assert len(sleep.calls) == 3


def test_delay_for_exponential_backoff_capped() -> None:
    policy = RetryPolicy(base_seconds=1.0, cap_seconds=4.0, jitter_fraction=0.0, rand=_no_jitter)
    error = ProviderUnavailableError("down")
    assert policy.delay_for(1, error) == pytest.approx(1.0)
    assert policy.delay_for(2, error) == pytest.approx(2.0)
    assert policy.delay_for(3, error) == pytest.approx(4.0)
    assert policy.delay_for(4, error) == pytest.approx(4.0)


# --- CircuitBreaker ------------------------------------------------------


def test_breaker_opens_after_threshold_within_window() -> None:
    clock = _FakeClock()
    breaker = CircuitBreaker(failure_threshold=3, window_seconds=60.0, clock=clock)
    assert _state(breaker) == "closed"
    breaker.record_failure()
    breaker.record_failure()
    assert _state(breaker) == "closed"
    breaker.record_failure()
    assert _state(breaker) == "open"
    assert breaker.allow() is False


def test_breaker_prunes_old_failures_outside_window() -> None:
    clock = _FakeClock()
    breaker = CircuitBreaker(failure_threshold=3, window_seconds=10.0, clock=clock)
    breaker.record_failure()
    clock.advance(5.0)
    breaker.record_failure()
    clock.advance(6.0)  # first failure now outside the 10s window
    breaker.record_failure()
    assert breaker.state == "closed"


def test_breaker_half_open_after_open_seconds_allows_one_probe() -> None:
    clock = _FakeClock()
    breaker = CircuitBreaker(failure_threshold=1, open_seconds=30.0, clock=clock)
    breaker.record_failure()
    assert _state(breaker) == "open"
    assert breaker.allow() is False
    clock.advance(30.0)
    assert _state(breaker) == "half_open"
    assert breaker.allow() is True
    assert breaker.allow() is False


def test_breaker_success_closes() -> None:
    clock = _FakeClock()
    breaker = CircuitBreaker(failure_threshold=1, open_seconds=10.0, clock=clock)
    breaker.record_failure()
    clock.advance(10.0)
    assert breaker.allow() is True
    breaker.record_success()
    assert breaker.state == "closed"
    assert breaker.allow() is True


def test_breaker_failure_in_half_open_reopens_immediately() -> None:
    clock = _FakeClock()
    breaker = CircuitBreaker(failure_threshold=1, open_seconds=10.0, clock=clock)
    breaker.record_failure()
    clock.advance(10.0)
    assert breaker.allow() is True
    breaker.record_failure()
    assert breaker.state == "open"
    assert breaker.allow() is False


def test_breaker_registry_creates_on_demand_and_reports_states() -> None:
    registry = CircuitBreakerRegistry(failure_threshold=1)
    breaker_a = registry.get("model-a")
    breaker_b = registry.get("model-a")
    assert breaker_a is breaker_b
    breaker_a.record_failure()
    assert registry.states() == {"model-a": "open"}


# --- ConcurrencyLimiter ---------------------------------------------------


async def test_limiter_bounds_concurrency_per_provider_and_model() -> None:
    limiter = ConcurrencyLimiter(per_provider=2, per_model=1)
    provider_in_flight = 0
    max_provider_in_flight = 0
    model_in_flight: dict[str, int] = {"m1": 0, "m2": 0}
    max_model_in_flight: dict[str, int] = {"m1": 0, "m2": 0}
    lock = asyncio.Lock()

    async def work(model: str, hold: float) -> None:
        nonlocal provider_in_flight, max_provider_in_flight
        async with limiter.slot("nvidia", model):
            async with lock:
                provider_in_flight += 1
                model_in_flight[model] += 1
                max_provider_in_flight = max(max_provider_in_flight, provider_in_flight)
                max_model_in_flight[model] = max(max_model_in_flight[model], model_in_flight[model])
            await asyncio.sleep(hold)
            async with lock:
                provider_in_flight -= 1
                model_in_flight[model] -= 1

    # Two concurrent calls to m1 (per-model cap 1) and one to m2.
    await asyncio.gather(
        work("m1", 0.05),
        work("m1", 0.05),
        work("m2", 0.05),
    )
    assert max_provider_in_flight <= 2
    assert max_model_in_flight["m1"] == 1
    assert max_model_in_flight["m2"] == 1
