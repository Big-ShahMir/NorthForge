"""Per-request dependencies: the database session and the model router.

``get_session`` commits on a clean return, rolls back on any exception
(including one raised by a route handler after the session was used), and
always closes the session. ``get_model_router`` hands out the process-wide
router built in the app lifespan.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from northforge.providers.router import ModelRouter


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()


def get_model_router(request: Request) -> ModelRouter:
    router = getattr(request.app.state, "model_router", None)
    if router is None:
        raise RuntimeError("model router not initialised; app lifespan did not run")
    assert isinstance(router, ModelRouter)
    return router
