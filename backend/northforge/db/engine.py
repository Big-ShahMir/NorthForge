"""Async SQLAlchemy engine and session factory construction."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine as _create_async_engine

if TYPE_CHECKING:
    from northforge.core.config import Settings


def sqlalchemy_url(database_url: str) -> str:
    """Convert a plain ``postgresql://`` DSN to the async ``asyncpg`` driver URL.

    A URL that already names a driver (e.g. ``postgresql+asyncpg://``) is
    left unchanged.
    """

    if database_url.startswith("postgresql+"):
        return database_url
    if database_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + database_url[len("postgresql://") :]
    return database_url


def create_engine(settings: Settings) -> AsyncEngine:
    """Build the application's async engine from settings."""

    pool_size = settings.database_pool_size
    echo = settings.database_echo
    return _create_async_engine(
        sqlalchemy_url(settings.database_url),
        pool_size=pool_size,
        pool_pre_ping=True,
        echo=echo,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build a session factory bound to ``engine`` with commit-safe defaults."""

    return async_sessionmaker(bind=engine, expire_on_commit=False)
