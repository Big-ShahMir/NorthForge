"""arq worker entry point.

Run with ``python -m northforge.worker``. The worker refreshes a Redis health
key every ``WORKER_HEALTH_CHECK_INTERVAL_SECONDS`` so the API readiness probe
and ``python -m northforge.worker --check`` can verify liveness independently.

``on_startup`` builds the worker's own engine, session factory, and object
storage (the same construction the API's lifespan does, but a separate
instance -- the worker is a separate process) and stores them on ``ctx`` so
job functions such as ``ingest_synthetic_dataset`` never construct their own
resources per call.
"""

from __future__ import annotations

import dataclasses
import logging
import socket
import time
import uuid
from pathlib import Path
from typing import Any

from arq.connections import RedisSettings
from arq.worker import Worker, async_check_health

from northforge.core.config import Settings, get_settings
from northforge.core.logging import configure_logging
from northforge.core.queue import QUEUE_NAME, WORKER_HEALTH_KEY
from northforge.db.engine import create_engine, create_session_factory
from northforge.ingestion.pipeline import ingest_dataset
from northforge.providers.factory import build_model_router, close_model_router
from northforge.storage.s3 import S3ObjectStorage

logger = logging.getLogger(__name__)

# Resolves to <repo>/backend/data/synthetic from this file's location
# (worker/main.py -> worker -> northforge -> backend).
_DATASET_DIR = Path(__file__).resolve().parents[2] / "data" / "synthetic"


async def ping(ctx: dict[str, Any], message: str = "ping") -> dict[str, str]:
    """Round-trip job used to verify the queue end to end."""
    logger.info("ping job received", extra={"job_id": ctx.get("job_id")})
    return {"echo": message, "worker_host": socket.gethostname(), "job_id": str(ctx["job_id"])}


async def ingest_synthetic_dataset(
    ctx: dict[str, Any], project_id: str, dataset_version: str
) -> dict[str, Any]:
    """Ingest the committed synthetic dataset (``backend/data/synthetic``) into a project."""
    started = time.perf_counter()
    session_factory = ctx["session_factory"]
    storage = ctx["storage"]

    async with session_factory() as session:
        report = await ingest_dataset(
            session, storage, uuid.UUID(project_id), _DATASET_DIR, dataset_version
        )
        await session.commit()

    duration_ms = round((time.perf_counter() - started) * 1000, 1)
    logger.info(
        "ingestion job completed",
        extra={
            "job_id": ctx.get("job_id"),
            "project_id": project_id,
            "documents_created": report.documents_created,
            "documents_updated": report.documents_updated,
            "documents_skipped": report.documents_skipped,
            "chunks_created": report.chunks_created,
            "rules_created": report.rules_created,
            "warning_count": len(report.warnings),
            "duration_ms": duration_ms,
        },
    )
    return dataclasses.asdict(report)


async def on_startup(ctx: dict[str, Any]) -> None:
    logger.info("worker started", extra={"queue": QUEUE_NAME, "host": socket.gethostname()})
    settings = get_settings()
    engine = create_engine(settings)
    ctx["engine"] = engine
    ctx["session_factory"] = create_session_factory(engine)
    storage = S3ObjectStorage(settings)
    try:
        await storage.ensure_bucket()
    except Exception as exc:  # storage outage must not crash worker startup
        logger.warning(
            "object storage bucket unavailable at worker startup", extra={"error": repr(exc)}
        )
    ctx["storage"] = storage
    # The worker owns its own router (separate process); the arq redis pool
    # backs the deterministic-request cache.
    ctx["model_router"] = build_model_router(settings, ctx.get("redis"))


async def on_shutdown(ctx: dict[str, Any]) -> None:
    logger.info("worker stopping", extra={"queue": QUEUE_NAME})
    model_router = ctx.get("model_router")
    if model_router is not None:
        await close_model_router(model_router)
    engine = ctx.get("engine")
    if engine is not None:
        await engine.dispose()


def redis_settings_from(settings: Settings) -> RedisSettings:
    return RedisSettings.from_dsn(settings.redis_url)


def build_worker(settings: Settings) -> Worker:
    return Worker(
        functions=[ping, ingest_synthetic_dataset],
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
