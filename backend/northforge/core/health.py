"""Dependency readiness checks for PostgreSQL, Redis, and the queue worker.

Each check returns a ``CheckResult`` with a user-safe ``detail``. Connection
strings and exception text are logged server-side only, never returned.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Literal

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from northforge.core.config import Settings
from northforge.core.queue import WORKER_HEALTH_KEY

logger = logging.getLogger(__name__)

CheckStatus = Literal["ok", "error", "unavailable"]
ReadinessStatus = Literal["ready", "degraded", "not_ready"]


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: CheckStatus
    latency_ms: float
    detail: str | None = None


@dataclass(frozen=True)
class ReadinessReport:
    status: ReadinessStatus
    checks: list[CheckResult]

    @property
    def http_status(self) -> int:
        return 503 if self.status == "not_ready" else 200


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


async def check_postgres(engine: AsyncEngine, timeout_seconds: float) -> CheckResult:
    started = time.perf_counter()

    async def _probe() -> None:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    try:
        await asyncio.wait_for(_probe(), timeout=timeout_seconds)
    except (OSError, SQLAlchemyError, TimeoutError) as exc:
        logger.warning("postgres readiness check failed", extra={"error": repr(exc)})
        return CheckResult("postgres", "error", _elapsed_ms(started), "connection failed")
    return CheckResult("postgres", "ok", _elapsed_ms(started))


async def check_redis(redis: Redis, timeout_seconds: float) -> CheckResult:
    started = time.perf_counter()
    try:
        await asyncio.wait_for(redis.ping(), timeout=timeout_seconds)
    except (OSError, RedisError, TimeoutError) as exc:
        logger.warning("redis readiness check failed", extra={"error": repr(exc)})
        return CheckResult("redis", "error", _elapsed_ms(started), "connection failed")
    return CheckResult("redis", "ok", _elapsed_ms(started))


async def check_worker(redis: Redis, timeout_seconds: float) -> CheckResult:
    """Read the health key the arq worker refreshes on every health-check interval."""
    started = time.perf_counter()
    try:
        raw = await asyncio.wait_for(redis.get(WORKER_HEALTH_KEY), timeout=timeout_seconds)
    except (OSError, RedisError, TimeoutError) as exc:
        logger.warning("worker readiness check failed", extra={"error": repr(exc)})
        return CheckResult("worker", "error", _elapsed_ms(started), "redis unreachable")
    if raw is None:
        return CheckResult(
            "worker", "unavailable", _elapsed_ms(started), "no recent worker heartbeat"
        )
    detail = raw.decode() if isinstance(raw, bytes) else str(raw)
    return CheckResult("worker", "ok", _elapsed_ms(started), detail)


class ReadinessProbe:
    """Runs all dependency checks concurrently and aggregates a readiness status."""

    def __init__(self, settings: Settings, redis: Redis, engine: AsyncEngine) -> None:
        self._settings = settings
        self._redis = redis
        self._engine = engine

    async def run(self) -> ReadinessReport:
        timeout_seconds = self._settings.readiness_timeout_seconds
        results = await asyncio.gather(
            check_postgres(self._engine, timeout_seconds),
            check_redis(self._redis, timeout_seconds),
            check_worker(self._redis, timeout_seconds),
        )
        checks = list(results)
        by_name = {check.name: check for check in checks}
        if by_name["postgres"].status != "ok" or by_name["redis"].status != "ok":
            status: ReadinessStatus = "not_ready"
        elif by_name["worker"].status != "ok":
            status = "degraded"
        else:
            status = "ready"
        return ReadinessReport(status=status, checks=checks)
