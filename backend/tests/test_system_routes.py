from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI
from fastapi.testclient import TestClient

from northforge import __version__
from northforge.core.errors import NotFoundError
from northforge.core.health import ReadinessReport
from tests.conftest import make_report

Install = Callable[[ReadinessReport], None]


def test_health_returns_envelope_and_request_id(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "abc-123"})

    assert response.status_code == 200
    body = response.json()
    assert body["error"] is None
    assert body["request_id"] == "abc-123"
    assert response.headers["X-Request-ID"] == "abc-123"
    assert body["data"] == {"status": "ok", "version": __version__, "app_env": "test"}


def test_health_generates_request_id_when_absent(client: TestClient) -> None:
    response = client.get("/health")

    request_id = response.headers["X-Request-ID"]
    assert len(request_id) == 32
    assert response.json()["request_id"] == request_id


def test_ready_all_ok(client: TestClient, use_report: Install) -> None:
    use_report(make_report())

    response = client.get("/ready")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "ready"
    assert [c["name"] for c in data["checks"]] == ["postgres", "redis", "worker", "storage"]


def test_ready_is_degraded_without_worker(client: TestClient, use_report: Install) -> None:
    use_report(make_report(worker="unavailable"))

    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "degraded"


def test_ready_returns_503_when_postgres_down(client: TestClient, use_report: Install) -> None:
    use_report(make_report(postgres="error"))

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["data"]["status"] == "not_ready"


def test_ready_against_unreachable_dependencies_reports_not_ready(client: TestClient) -> None:
    """No fake: the real probe must fail fast against unroutable addresses."""
    response = client.get("/ready")

    assert response.status_code == 503
    data = response.json()["data"]
    statuses = {c["name"]: c["status"] for c in data["checks"]}
    assert statuses["postgres"] == "error"
    assert statuses["redis"] == "error"
    # Tests inject in-memory object storage, so storage reports ok here;
    # the S3 failure path is covered by tests/test_health_checks.py.
    assert statuses["storage"] == "ok"
    details = " ".join(c["detail"] or "" for c in data["checks"])
    assert "secret" not in details
    assert "127.0.0.1" not in details


def test_unknown_route_uses_envelope(client: TestClient) -> None:
    response = client.get("/nope")

    assert response.status_code == 404
    body = response.json()
    assert body["data"] is None
    assert body["error"]["code"] == "NOT_FOUND"


def test_app_error_maps_to_code(app: FastAPI, client: TestClient) -> None:
    @app.get("/missing")
    async def missing() -> None:
        raise NotFoundError("Project not found.")

    response = client.get("/missing")

    assert response.status_code == 404
    assert response.json()["error"] == {
        "code": "NOT_FOUND",
        "message": "Project not found.",
        "details": None,
    }


def test_unhandled_exception_hides_internals(app: FastAPI, client: TestClient) -> None:
    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("database password is hunter2")

    response = client.get("/boom")

    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert "hunter2" not in response.text


def test_validation_error_uses_envelope(app: FastAPI, client: TestClient) -> None:
    @app.get("/typed")
    async def typed(limit: int) -> dict[str, int]:
        return {"limit": limit}

    response = client.get("/typed", params={"limit": "many"})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"][0]["loc"] == ["query", "limit"]
