"""Authentication behaviour of the project/workflow API routes."""

from __future__ import annotations

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient

from northforge.api.main import create_app
from northforge.core.config import Settings, load_settings
from tests.conftest import UNIT_DATABASE_URL, UNIT_REDIS_URL


async def test_dev_mode_without_header_returns_401(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get("/api/projects")

    assert response.status_code == 401
    body = response.json()
    assert body["data"] is None
    assert body["error"]["code"] == "UNAUTHENTICATED"


async def test_dev_mode_with_header_authenticates(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get("/api/projects", headers={"X-Dev-User": "alice"})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data == {"items": [], "total": 0, "limit": 20, "offset": 0}


def _clerk_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", UNIT_DATABASE_URL)
    monkeypatch.setenv("REDIS_URL", UNIT_REDIS_URL)
    monkeypatch.setenv("READINESS_TIMEOUT_SECONDS", "0.5")
    monkeypatch.setenv("AUTH_MODE", "clerk")
    monkeypatch.setenv("CLERK_JWKS_URL", "https://example-clerk.invalid/.well-known/jwks.json")
    monkeypatch.setenv("CLERK_ISSUER", "https://example-clerk.invalid")
    return load_settings(env_file=None)


def test_clerk_mode_without_bearer_token_returns_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _clerk_settings(monkeypatch)
    app = create_app(settings)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/projects")

    assert response.status_code == 401
    body = response.json()
    assert body["data"] is None
    assert body["error"]["code"] == "UNAUTHENTICATED"


def test_clerk_mode_with_malformed_authorization_header_returns_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _clerk_settings(monkeypatch)
    app = create_app(settings)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/projects", headers={"Authorization": "Token abc"})

    assert response.status_code == 401


async def test_request_id_propagates_through_authenticated_route(
    api_client: httpx.AsyncClient,
) -> None:
    response = await api_client.get(
        "/api/projects", headers={"X-Dev-User": "alice", "X-Request-ID": "req-42"}
    )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req-42"
    assert response.json()["request_id"] == "req-42"
