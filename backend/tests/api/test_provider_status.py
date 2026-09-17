"""``GET /api/provider-status`` over the real app lifespan (unroutable Redis, no key)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from northforge.api.main import create_app
from northforge.core.config import Settings, load_settings
from northforge.storage.memory import MemoryObjectStorage
from tests.conftest import set_unit_s3_env


def test_requires_authentication(client: TestClient) -> None:
    response = client.get("/api/provider-status")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_reports_not_configured_without_key(client: TestClient) -> None:
    response = client.get("/api/provider-status", headers={"X-Dev-User": "alice"})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["provider"] == "nvidia"
    assert data["configured"] is False
    assert data["base_url_host"] == "integrate.api.nvidia.com"
    assert [role["role"] for role in data["roles"]] == [
        "planner",
        "extractor",
        "drafter",
        "evaluator",
        "embedding",
        "reranker",
    ]
    planner = data["roles"][0]
    assert planner["enabled"] is True
    assert planner["model"] == "nvidia/nemotron-3-super-120b-a12b"
    assert planner["fallbacks"] == ["nvidia/nemotron-3.5-lightning-30b-a3b"]
    assert planner["circuit_state"] == "closed"
    assert planner["last_error"] is None


def test_reports_configured_with_key_and_never_leaks_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-secret")
    monkeypatch.setenv("NVIDIA_BASE_URL", "https://user:pw@integrate.api.nvidia.com/v1")
    set_unit_s3_env(monkeypatch)
    settings: Settings = load_settings(env_file=None)
    app = create_app(settings, storage=MemoryObjectStorage())

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/provider-status", headers={"X-Dev-User": "alice"})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["configured"] is True
    assert data["base_url_host"] == "integrate.api.nvidia.com"
    assert "nvapi" not in response.text
    assert "pw" not in data["base_url_host"]
