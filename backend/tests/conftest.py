from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from northforge.api.main import create_app
from northforge.api.routes.system import get_readiness_probe
from northforge.core.config import Settings, load_settings
from northforge.core.health import CheckResult, ReadinessProbe, ReadinessReport
from northforge.db.engine import sqlalchemy_url
from northforge.db.models import User
from northforge.storage.memory import MemoryObjectStorage

# Unroutable addresses: nothing in unit tests must reach a real service.
UNIT_DATABASE_URL = "postgresql://northforge:secret@127.0.0.1:1/northforge"
UNIT_REDIS_URL = "redis://127.0.0.1:1/0"

# Dummy S3 settings: unit tests never contact a real object store. Individual
# tests that need a reachable store use the MinIO instance via
# NORTHFORGE_INTEGRATION=1 instead of these values.
UNIT_S3_ENDPOINT = "http://127.0.0.1:1"
UNIT_S3_BUCKET = "northforge-test"
UNIT_S3_ACCESS_KEY = "unit-test-access-key"
UNIT_S3_SECRET_KEY = "unit-test-secret-key"  # noqa: S105 - dummy value, never a real secret


def set_unit_s3_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set dummy S3 env vars so ``load_settings`` validation passes in tests."""

    monkeypatch.setenv("S3_ENDPOINT", UNIT_S3_ENDPOINT)
    monkeypatch.setenv("S3_BUCKET", UNIT_S3_BUCKET)
    monkeypatch.setenv("S3_ACCESS_KEY", UNIT_S3_ACCESS_KEY)
    monkeypatch.setenv("S3_SECRET_KEY", UNIT_S3_SECRET_KEY)


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", UNIT_DATABASE_URL)
    monkeypatch.setenv("REDIS_URL", UNIT_REDIS_URL)
    monkeypatch.setenv("READINESS_TIMEOUT_SECONDS", "0.5")
    monkeypatch.setenv("AUTH_MODE", "dev")
    set_unit_s3_env(monkeypatch)
    return load_settings(env_file=None)


class FakeReadinessProbe(ReadinessProbe):
    """Returns a canned report instead of touching Postgres or Redis."""

    def __init__(self, report: ReadinessReport) -> None:
        self.report = report

    async def run(self) -> ReadinessReport:
        return self.report


def make_report(
    postgres: str = "ok", redis: str = "ok", worker: str = "ok", storage: str = "ok"
) -> ReadinessReport:
    checks = [
        CheckResult("postgres", postgres, 1.0),  # type: ignore[arg-type]
        CheckResult("redis", redis, 1.0),  # type: ignore[arg-type]
        CheckResult("worker", worker, 1.0),  # type: ignore[arg-type]
        CheckResult("storage", storage, 1.0),  # type: ignore[arg-type]
    ]
    if postgres != "ok" or redis != "ok" or storage != "ok":
        status = "not_ready"
    elif worker != "ok":
        status = "degraded"
    else:
        status = "ready"
    return ReadinessReport(status=status, checks=checks)  # type: ignore[arg-type]


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings, storage=MemoryObjectStorage())


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def use_report(app: FastAPI) -> Iterator[type[FakeReadinessProbe]]:
    """Helper: ``use_report(report)`` swaps the readiness probe for a fake."""

    def _install(report: ReadinessReport) -> None:
        app.dependency_overrides[get_readiness_probe] = lambda: FakeReadinessProbe(report)

    yield _install  # type: ignore[misc]
    app.dependency_overrides.clear()


# --- Database fixtures -----------------------------------------------------
#
# These fixtures spin up (or reuse) a real PostgreSQL database for
# migration and repository tests. They are opt-out rather than opt-in: if no
# server is reachable at ``TEST_DATABASE_URL`` the dependent tests are
# skipped, unless ``NORTHFORGE_INTEGRATION=1`` is set, in which case an
# unreachable database is a hard failure.

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent


def _read_repo_env_database_url() -> str | None:
    """Read ``DATABASE_URL`` from the repo-root ``.env`` file, if present."""

    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return None
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if line.startswith("DATABASE_URL="):
            return line[len("DATABASE_URL=") :].strip()
    return None


def _with_database_name(url: str, database_name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(
        (parts.scheme, parts.netloc, f"/{database_name}", parts.query, parts.fragment)
    )


def _database_name(url: str) -> str:
    return urlsplit(url).path.lstrip("/")


def _derive_test_database_url() -> str:
    explicit = os.environ.get("NORTHFORGE_TEST_DATABASE_URL")
    if explicit:
        return explicit
    base = os.environ.get("DATABASE_URL") or _read_repo_env_database_url()
    if base:
        return _with_database_name(base, f"{_database_name(base)}_test")
    return "postgresql://northforge:northforge@localhost:5433/northforge_test"


TEST_DATABASE_URL = _derive_test_database_url()


@pytest.fixture(scope="session")
def migrated_database() -> Iterator[str]:
    """Ensure the test database exists and is migrated to ``head``.

    Runs once per test session. The database is created (if missing) via a
    direct ``asyncpg`` connection to the ``postgres`` maintenance database,
    then migrated with ``alembic downgrade base`` followed by
    ``alembic upgrade head`` in a subprocess, so this fixture never opens an
    asyncio event loop that could conflict with pytest-asyncio's loop.
    """

    integration_required = os.environ.get("NORTHFORGE_INTEGRATION") == "1"

    async def _ensure_database_exists() -> None:
        maintenance_url = _with_database_name(TEST_DATABASE_URL, "postgres")
        database_name = _database_name(TEST_DATABASE_URL)
        conn = await asyncpg.connect(maintenance_url, timeout=2)
        try:
            exists = await conn.fetchval(
                "SELECT 1 FROM pg_database WHERE datname = $1", database_name
            )
            if not exists:
                # CREATE DATABASE does not support bind parameters; the name
                # comes from test configuration (env vars or the repo .env),
                # never from untrusted input.
                await conn.execute(f'CREATE DATABASE "{database_name}"')
        finally:
            await conn.close()

    try:
        asyncio.run(_ensure_database_exists())
    except Exception as exc:
        message = f"test database unreachable at {TEST_DATABASE_URL!r}: {exc!r}"
        if integration_required:
            pytest.fail(message)
        pytest.skip(message)

    env = dict(os.environ)
    env["DATABASE_URL"] = TEST_DATABASE_URL
    for args in (["downgrade", "base"], ["upgrade", "head"]):
        result = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "alembic", *args],
            cwd=str(BACKEND_DIR),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            pytest.fail(f"alembic {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}")

    yield TEST_DATABASE_URL


@pytest_asyncio.fixture
async def db_session(migrated_database: str) -> AsyncIterator[AsyncSession]:
    """A session bound to a rolled-back connection-level transaction.

    Every test using this fixture sees a migrated but empty database and
    leaves no rows behind, regardless of whether the test itself commits.
    """

    engine = create_async_engine(sqlalchemy_url(migrated_database))
    try:
        async with engine.connect() as connection:
            await connection.begin()
            session = AsyncSession(
                bind=connection,
                join_transaction_mode="create_savepoint",
                expire_on_commit=False,
            )
            try:
                yield session
            finally:
                await session.close()
                await connection.rollback()
    finally:
        await engine.dispose()


@pytest.fixture
def make_user() -> Callable[..., Awaitable[User]]:
    """``await make_user(session, subject)`` inserts a ``User`` row via the ORM.

    A minimal helper for tests that need an owning user but do not depend on
    the users repository (written separately).
    """

    async def _make_user(
        session: AsyncSession,
        subject: str,
        *,
        email: str | None = None,
        display_name: str | None = None,
    ) -> User:
        user = User(clerk_user_id=subject, email=email, display_name=display_name)
        session.add(user)
        await session.flush()
        return user

    return _make_user
