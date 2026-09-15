from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from northforge.api.main import create_app
from northforge.api.routes.system import get_readiness_probe
from northforge.core.config import Settings, load_settings
from northforge.core.health import CheckResult, ReadinessProbe, ReadinessReport

# Unroutable addresses: nothing in unit tests must reach a real service.
UNIT_DATABASE_URL = "postgresql://northforge:secret@127.0.0.1:1/northforge"
UNIT_REDIS_URL = "redis://127.0.0.1:1/0"


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", UNIT_DATABASE_URL)
    monkeypatch.setenv("REDIS_URL", UNIT_REDIS_URL)
    monkeypatch.setenv("READINESS_TIMEOUT_SECONDS", "0.5")
    return load_settings(env_file=None)


class FakeReadinessProbe(ReadinessProbe):
    """Returns a canned report instead of touching Postgres or Redis."""

    def __init__(self, report: ReadinessReport) -> None:
        self.report = report

    async def run(self) -> ReadinessReport:
        return self.report


def make_report(postgres: str = "ok", redis: str = "ok", worker: str = "ok") -> ReadinessReport:
    checks = [
        CheckResult("postgres", postgres, 1.0),  # type: ignore[arg-type]
        CheckResult("redis", redis, 1.0),  # type: ignore[arg-type]
        CheckResult("worker", worker, 1.0),  # type: ignore[arg-type]
    ]
    if postgres != "ok" or redis != "ok":
        status = "not_ready"
    elif worker != "ok":
        status = "degraded"
    else:
        status = "ready"
    return ReadinessReport(status=status, checks=checks)  # type: ignore[arg-type]


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


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
