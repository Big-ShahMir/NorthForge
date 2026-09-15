"""Runs only against live PostgreSQL and Redis (``NORTHFORGE_INTEGRATION=1``)."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from northforge.api.main import create_app
from northforge.core.config import load_settings

pytestmark = pytest.mark.integration

if os.environ.get("NORTHFORGE_INTEGRATION") != "1":
    pytest.skip("set NORTHFORGE_INTEGRATION=1 to run", allow_module_level=True)


def test_ready_against_live_dependencies() -> None:
    app = create_app(load_settings())
    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    data = response.json()["data"]
    statuses = {c["name"]: c["status"] for c in data["checks"]}
    assert statuses["postgres"] == "ok"
    assert statuses["redis"] == "ok"
    assert data["status"] in {"ready", "degraded"}
