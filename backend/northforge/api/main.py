"""FastAPI application factory.

Start locally with ``python -m northforge.api`` or
``uvicorn northforge.api.main:create_app --factory``. The factory form keeps
module import free of side effects so tests can build isolated apps.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from redis.asyncio import Redis

from northforge import __version__
from northforge.api.errors import register_error_handlers
from northforge.api.middleware import RequestContextMiddleware
from northforge.api.routes import system
from northforge.core.config import Settings, get_settings
from northforge.core.health import ReadinessProbe
from northforge.core.logging import configure_logging

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        redis = Redis.from_url(resolved.redis_url)
        app.state.settings = resolved
        app.state.redis = redis
        app.state.readiness_probe = ReadinessProbe(resolved, redis)
        logger.info("api started", extra={"version": __version__, "app_env": resolved.app_env})
        try:
            yield
        finally:
            await redis.aclose()
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
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)
    app.include_router(system.router)
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
