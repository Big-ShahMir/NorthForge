"""API tests for project routes."""

from __future__ import annotations

import httpx2 as httpx

from northforge.auth.principal import Principal
from tests.api.conftest import AsUser

ALICE = Principal(subject="dev|alice")
BOB = Principal(subject="dev|bob")


async def test_create_project_returns_201(api_client: httpx.AsyncClient, as_user: AsUser) -> None:
    as_user(ALICE)

    response = await api_client.post(
        "/api/projects", json={"name": "Contract review", "description": "desc"}
    )

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["name"] == "Contract review"
    assert data["description"] == "desc"
    assert data["vertical"] == "contract_review"


async def test_create_project_defaults_vertical(
    api_client: httpx.AsyncClient, as_user: AsUser
) -> None:
    as_user(ALICE)

    response = await api_client.post("/api/projects", json={"name": "Minimal"})

    assert response.status_code == 201
    assert response.json()["data"]["vertical"] == "contract_review"


async def test_create_project_rejects_blank_name(
    api_client: httpx.AsyncClient, as_user: AsUser
) -> None:
    as_user(ALICE)

    response = await api_client.post("/api/projects", json={"name": ""})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_list_projects_is_scoped_to_owner(
    api_client: httpx.AsyncClient, as_user: AsUser
) -> None:
    as_user(ALICE)
    await api_client.post("/api/projects", json={"name": "Alice's project"})

    as_user(BOB)
    await api_client.post("/api/projects", json={"name": "Bob's first"})
    await api_client.post("/api/projects", json={"name": "Bob's second"})

    response = await api_client.get("/api/projects")

    data = response.json()["data"]
    assert data["total"] == 2
    assert {project["name"] for project in data["items"]} == {"Bob's first", "Bob's second"}


async def test_list_projects_pagination(api_client: httpx.AsyncClient, as_user: AsUser) -> None:
    as_user(ALICE)
    for index in range(3):
        await api_client.post("/api/projects", json={"name": f"Project {index}"})

    response = await api_client.get("/api/projects", params={"limit": 1, "offset": 1})

    data = response.json()["data"]
    assert data["total"] == 3
    assert data["limit"] == 1
    assert data["offset"] == 1
    assert len(data["items"]) == 1


async def test_get_project_returns_project(api_client: httpx.AsyncClient, as_user: AsUser) -> None:
    as_user(ALICE)
    created = (await api_client.post("/api/projects", json={"name": "P"})).json()["data"]

    response = await api_client.get(f"/api/projects/{created['id']}")

    assert response.status_code == 200
    assert response.json()["data"]["id"] == created["id"]


async def test_get_project_unknown_id_returns_404(
    api_client: httpx.AsyncClient, as_user: AsUser
) -> None:
    as_user(ALICE)

    response = await api_client.get("/api/projects/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_get_other_users_project_returns_404(
    api_client: httpx.AsyncClient, as_user: AsUser
) -> None:
    as_user(ALICE)
    created = (await api_client.post("/api/projects", json={"name": "Alice's project"})).json()[
        "data"
    ]

    as_user(BOB)
    response = await api_client.get(f"/api/projects/{created['id']}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_patch_project_updates_name(api_client: httpx.AsyncClient, as_user: AsUser) -> None:
    as_user(ALICE)
    created = (await api_client.post("/api/projects", json={"name": "Old name"})).json()["data"]

    response = await api_client.patch(f"/api/projects/{created['id']}", json={"name": "New name"})

    assert response.status_code == 200
    assert response.json()["data"]["name"] == "New name"


async def test_patch_project_with_no_fields_returns_422(
    api_client: httpx.AsyncClient, as_user: AsUser
) -> None:
    as_user(ALICE)
    created = (await api_client.post("/api/projects", json={"name": "P"})).json()["data"]

    response = await api_client.patch(f"/api/projects/{created['id']}", json={})

    assert response.status_code == 422


async def test_patch_other_users_project_returns_404(
    api_client: httpx.AsyncClient, as_user: AsUser
) -> None:
    as_user(ALICE)
    created = (await api_client.post("/api/projects", json={"name": "Alice's project"})).json()[
        "data"
    ]

    as_user(BOB)
    response = await api_client.patch(f"/api/projects/{created['id']}", json={"name": "Hijacked"})

    assert response.status_code == 404
