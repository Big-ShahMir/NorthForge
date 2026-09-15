"""arq worker entry point.

Run with ``python -m northforge.worker``. The worker refreshes a Redis health
key every ``WORKER_HEALTH_CHECK_INTERVAL_SECONDS`` so the API readiness probe
and ``python -m northforge.worker --check`` can verify liveness independently.
"""

from __future__ import annotations

import logging
import socket
from typing import Any

from arq.connections import RedisSettings
from arq.worker import Worker, async_check_health

from northforge.core.config import Settings, get_settings
from northforge.core.logging import configure_logging
from northforge.core.queue import QUEUE_NAME, WORKER_HEALTH_KEY

logger = logging.getLogger(__name__)


async def ping(ctx: dict[str, Any], message: str = "ping") -> dict[str, str]:
    """Round-trip job used to verify the queue end to end."""
    logger.info("ping job received", extra={"job_id": ctx.get("job_id")})
    return {"echo": message, "worker_host": socket.gethostname(), "job_id": str(ctx["job_id"])}


async def on_startup(ctx: dict[str, Any]) -> None:
    logger.info("worker started", extra={"queue": QUEUE_NAME, "host": socket.gethostname()})


async def on_shutdown(ctx: dict[str, Any]) -> None:
    logger.info("worker stopping", extra={"queue": QUEUE_NAME})


def redis_settings_from(settings: Settings) -> RedisSettings:
    return RedisSettings.from_dsn(settings.redis_url)


def build_worker(settings: Settings) -> Worker:
    return Worker(
        functions=[ping],
        redis_settings=redis_settings_from(settings),
        queue_name=QUEUE_NAME,
        health_check_key=WORKER_HEALTH_KEY,
        health_check_interval=settings.worker_health_check_interval_seconds,
        on_startup=on_startup,
        on_shutdown=on_shutdown,
        max_jobs=4,
        job_timeout=300,
        retry_jobs=False,
    )


async def check_health(settings: Settings) -> int:
    """Return 0 when a worker heartbeat is present, 1 otherwise."""
    return await async_check_health(
        redis_settings_from(settings),
        health_check_key=WORKER_HEALTH_KEY,
        queue_name=QUEUE_NAME,
    )


def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    worker = build_worker(settings)
    worker.run()
