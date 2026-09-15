"""Alembic migration environment.

Resolves the database URL from ``northforge.core.config.load_settings()``
(which reads ``.env`` and environment variables) so the same settings
machinery used by the application governs migrations. Falls back to the
``DATABASE_URL`` environment variable directly if settings fail to load
(for example, in a minimal CI environment that only sets ``DATABASE_URL``).
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Import models so every table is registered on Base.metadata.
from northforge.db import models  # noqa: F401
from northforge.db.base import Base
from northforge.db.engine import sqlalchemy_url

# Alembic Config object, providing access to values within alembic.ini.
config = context.config

# Interpret the config file for Python logging, unless run in a context
# (like our test fixtures) where logging is configured elsewhere.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolve_database_url() -> str:
    try:
        from northforge.core.config import load_settings

        return load_settings().database_url
    except Exception:
        env_url = os.environ.get("DATABASE_URL")
        if env_url:
            return env_url
        raise


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emits SQL without a DB connection)."""

    url = sqlalchemy_url(_resolve_database_url())
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode using an async engine."""

    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = sqlalchemy_url(_resolve_database_url())

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
