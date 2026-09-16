"""FastAPI application factory.

Start locally with ``python -m northforge.api`` or
``uvicorn northforge.api.main:create_app --factory``. The factory form keeps
module import free of side effects so tests can build isolated apps.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI
from redis.asyncio import Redis

from northforge import __version__
from northforge.api.errors import register_error_handlers
from northforge.api.middleware import RequestContextMiddleware
from northforge.api.routes import catalog, documents, projects, system, workflows
from northforge.auth.tokens import ClerkTokenVerifier
from northforge.core.config import Settings, get_settings
from northforge.core.health import ReadinessProbe
from northforge.core.logging import configure_logging
from northforge.core.queue import QUEUE_NAME
from northforge.db.engine import create_engine, create_session_factory
from northforge.storage.base import ObjectStorage
from northforge.storage.s3 import S3ObjectStorage
from northforge.tools.registry import get_tool_registry

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None, *, storage: ObjectStorage | None = None
) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        redis = Redis.from_url(resolved.redis_url)
        engine = create_engine(resolved)
        session_factory = create_session_factory(engine)
        resolved_storage = storage or S3ObjectStorage(resolved)
        app.state.settings = resolved
        app.state.redis = redis
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.storage = resolved_storage
        app.state.readiness_probe = ReadinessProbe(resolved, redis, engine, resolved_storage)
        app.state.tool_registry = get_tool_registry()
        try:
            await asyncio.wait_for(
                resolved_storage.ensure_bucket(), timeout=resolved.s3_timeout_seconds * 2
            )
        except Exception as exc:  # storage outage must not crash startup
            logger.warning(
                "object storage bucket unavailable at startup", extra={"error": repr(exc)}
            )
        try:
            redis_settings = RedisSettings.from_dsn(resolved.redis_url)
            redis_settings.conn_retries = 0
            app.state.arq_pool = await asyncio.wait_for(
                create_pool(redis_settings, default_queue_name=QUEUE_NAME),
                timeout=resolved.readiness_timeout_seconds,
            )
        except Exception as exc:  # queue outage must not crash startup
            logger.warning("arq pool unavailable at startup", extra={"error": repr(exc)})
            app.state.arq_pool = None
        if resolved.auth_mode == "clerk":
            assert resolved.clerk_jwks_url is not None
            assert resolved.clerk_issuer is not None
            app.state.token_verifier = ClerkTokenVerifier(
                resolved.clerk_jwks_url,
                resolved.clerk_issuer,
                authorized_parties=resolved.clerk_authorized_parties,
            )
        else:
            app.state.token_verifier = None
        logger.info("api started", extra={"version": __version__, "app_env": resolved.app_env})
        try:
            yield
        finally:
            if app.state.arq_pool is not None:
                await app.state.arq_pool.aclose()
            await redis.aclose()
            await engine.dispose()
            logger.info("api stopped")

    app = FastAPI(
        title="NorthForge API",
        version=__version__,
        lifespan=lifespan,
        docs_url=None if resolved.is_production else "/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.state.settings = resolved
    app.state.tool_registry = get_tool_registry()
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)
    app.include_router(system.router)
    app.include_router(projects.router)
    app.include_router(workflows.router)
    app.include_router(catalog.router)
    app.include_router(documents.router)
    return app


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "northforge.api.main:create_app",
        factory=True,
        host=settings.api_host,
        port=settings.api_port,
        reload=not settings.is_production,
        log_config=None,
    )
