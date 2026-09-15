"""Shared fixtures for API route tests.

Every route test needs a FastAPI app wired to the *test* database session
(``db_session``, wrapped in a rolled-back transaction shared by every
fixture in ``tests/conftest.py``) instead of the real per-request session
factory, and a way to pick which ``Principal`` a request is authenticated
as without going through real bearer-token verification.

``api_client`` is an ``httpx2.AsyncClient`` over an ``ASGITransport``
rather than ``starlette.testclient.TestClient``: ``TestClient`` drives the
app from a background thread with its own event loop, and the ``db_session``
fixture's asyncpg connection is bound to pytest-asyncio's event loop for the
current test -- crossing loops raises
``RuntimeError: ... attached to a different loop``. Running requests
in-process against an ``async def`` test keeps everything on one loop.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Protocol

import httpx2 as httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from northforge.api.deps import get_session
from northforge.api.main import create_app
from northforge.auth.dependencies import get_principal
from northforge.auth.principal import Principal
from northforge.core.config import Settings


class AsUser(Protocol):
    def __call__(self, principal: Principal) -> None: ...


@pytest.fixture
def api_app(settings: Settings, db_session: AsyncSession) -> Iterator[FastAPI]:
    """A FastAPI app whose ``get_session`` dependency yields the shared test session.

    The real per-request session factory (built from ``settings.database_url``
    in the app's lifespan) is never used by these tests: overriding
    ``get_session`` replaces it before any request is made, and the override
    yields the already-migrated, rolled-back-at-teardown ``db_session``
    without committing, so tests can inspect what a route wrote via the same
    session object.
    """
    app = create_app(settings)

    async def _override_get_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override_get_session
    yield app
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def api_client(api_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """An async HTTP client talking to ``api_app`` in-process, lifespan included.

    ``settings.database_url`` is an unroutable address (see
    ``tests.conftest.UNIT_DATABASE_URL``), so the app's lifespan builds a real
    ``AsyncEngine`` from it -- but engine construction is lazy and nothing in
    these tests exercises it (``get_session`` is always overridden), so the
    lifespan never needs a reachable database.
    """
    transport = httpx.ASGITransport(app=api_app)
    async with (
        api_app.router.lifespan_context(api_app),
        httpx.AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        yield client


@pytest.fixture
def as_user(api_app: FastAPI) -> Iterator[AsUser]:
    """``as_user(principal)`` makes every subsequent request authenticate as ``principal``."""

    def _install(principal: Principal) -> None:
        api_app.dependency_overrides[get_principal] = lambda: principal

    yield _install
    api_app.dependency_overrides.pop(get_principal, None)
