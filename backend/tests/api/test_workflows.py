"""API tests for workflow and workflow-version routes."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import httpx2 as httpx

from northforge.auth.principal import Principal
from tests.api.conftest import AsUser
from tests.fixtures_workflows import COMPLETE_DEFINITION

ALICE = Principal(subject="dev|alice")
BOB = Principal(subject="dev|bob")

_VALID_DEFINITION: dict[str, Any] = {
    "schema_version": 1,
    "name": "Contract review",
    "steps": [
        {"id": "retrieve", "type": "retrieve_documents", "label": "Retrieve"},
        {"id": "finish", "type": "finish", "label": "Finish"},
    ],
    "edges": [{"source": "retrieve", "target": "finish"}],
    "tools": ["doc_search"],
}


async def _create_project(api_client: httpx.AsyncClient) -> str:
    response = await api_client.post("/api/projects", json={"name": "Project"})
    return str(response.json()["data"]["id"])


async def _create_workflow(
    api_client: httpx.AsyncClient, project_id: str, definition: dict[str, Any] | None = None
) -> dict[str, Any]:
    response = await api_client.post(
        f"/api/projects/{project_id}/workflows",
        json={"name": "Review", "definition": definition or _VALID_DEFINITION},
    )
    data: dict[str, Any] = response.json()["data"]
    return data


async def test_create_workflow_returns_201_with_draft_version(
    api_client: httpx.AsyncClient, as_user: AsUser
) -> None:
    as_user(ALICE)
    project_id = await _create_project(api_client)

    response = await api_client.post(
        f"/api/projects/{project_id}/workflows",
        json={"name": "Review", "definition": _VALID_DEFINITION},
    )

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["name"] == "Review"
    assert len(data["versions"]) == 1
    assert data["versions"][0]["version_number"] == 1
    assert data["versions"][0]["status"] == "draft"
    assert data["current_version_id"] == data["versions"][0]["id"]


async def test_create_workflow_with_invalid_definition_returns_422(
    api_client: httpx.AsyncClient, as_user: AsUser
) -> None:
    as_user(ALICE)
    project_id = await _create_project(api_client)

    response = await api_client.post(
        f"/api/projects/{project_id}/workflows",
        json={
            "name": "Review",
            "definition": {
                "schema_version": 1,
                "name": "Bad",
                "steps": [{"id": "a", "type": "finish", "label": "A"}],
                "edges": [{"source": "a", "target": "missing"}],
            },
        },
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "INVALID_WORKFLOW"
    assert body["error"]["details"]


async def test_create_workflow_for_other_users_project_returns_404(
    api_client: httpx.AsyncClient, as_user: AsUser
) -> None:
    as_user(ALICE)
    project_id = await _create_project(api_client)

    as_user(BOB)
    response = await api_client.post(
        f"/api/projects/{project_id}/workflows",
        json={"name": "Review", "definition": _VALID_DEFINITION},
    )

    assert response.status_code == 404


async def test_list_workflows_for_project(api_client: httpx.AsyncClient, as_user: AsUser) -> None:
    as_user(ALICE)
    project_id = await _create_project(api_client)
    await _create_workflow(api_client, project_id)

    response = await api_client.get(f"/api/projects/{project_id}/workflows")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["current_version_number"] == 1
    assert data["items"][0]["current_version_status"] == "draft"


async def test_list_workflows_for_other_users_project_returns_404(
    api_client: httpx.AsyncClient, as_user: AsUser
) -> None:
    as_user(ALICE)
    project_id = await _create_project(api_client)

    as_user(BOB)
    response = await api_client.get(f"/api/projects/{project_id}/workflows")

    assert response.status_code == 404


async def test_get_workflow_returns_versions(
    api_client: httpx.AsyncClient, as_user: AsUser
) -> None:
    as_user(ALICE)
    project_id = await _create_project(api_client)
    workflow = await _create_workflow(api_client, project_id)

    response = await api_client.get(f"/api/workflows/{workflow['id']}")

    assert response.status_code == 200
    assert response.json()["data"]["id"] == workflow["id"]


async def test_get_workflow_for_other_user_returns_404(
    api_client: httpx.AsyncClient, as_user: AsUser
) -> None:
    as_user(ALICE)
    project_id = await _create_project(api_client)
    workflow = await _create_workflow(api_client, project_id)

    as_user(BOB)
    response = await api_client.get(f"/api/workflows/{workflow['id']}")

    assert response.status_code == 404


async def test_create_version_increments_number(
    api_client: httpx.AsyncClient, as_user: AsUser
) -> None:
    as_user(ALICE)
    project_id = await _create_project(api_client)
    workflow = await _create_workflow(api_client, project_id)

    response = await api_client.post(
        f"/api/workflows/{workflow['id']}/versions",
        json={"definition": _VALID_DEFINITION, "source_request": "add a step"},
    )

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["version_number"] == 2
    assert data["status"] == "draft"
    assert data["source_request"] == "add a step"


async def test_get_version_returns_definition_and_warnings(
    api_client: httpx.AsyncClient, as_user: AsUser
) -> None:
    as_user(ALICE)
    project_id = await _create_project(api_client)
    workflow = await _create_workflow(api_client, project_id)
    version_id = workflow["versions"][0]["id"]

    response = await api_client.get(f"/api/workflow-versions/{version_id}")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["definition"]["name"] == "Contract review"
    assert isinstance(data["validation_warnings"], list)


async def test_get_version_for_other_user_returns_404(
    api_client: httpx.AsyncClient, as_user: AsUser
) -> None:
    as_user(ALICE)
    project_id = await _create_project(api_client)
    workflow = await _create_workflow(api_client, project_id)
    version_id = workflow["versions"][0]["id"]

    as_user(BOB)
    response = await api_client.get(f"/api/workflow-versions/{version_id}")

    assert response.status_code == 404


async def test_patch_version_resets_to_draft(
    api_client: httpx.AsyncClient, as_user: AsUser
) -> None:
    as_user(ALICE)
    project_id = await _create_project(api_client)
    workflow = await _create_workflow(api_client, project_id)
    version_id = workflow["versions"][0]["id"]
    await api_client.post(f"/api/workflow-versions/{version_id}/validate")

    response = await api_client.patch(
        f"/api/workflow-versions/{version_id}", json={"definition": _VALID_DEFINITION}
    )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "draft"


async def test_validate_then_approve_happy_path(
    api_client: httpx.AsyncClient, as_user: AsUser
) -> None:
    as_user(ALICE)
    project_id = await _create_project(api_client)
    workflow = await _create_workflow(api_client, project_id, COMPLETE_DEFINITION)
    version_id = workflow["versions"][0]["id"]

    validated = await api_client.post(f"/api/workflow-versions/{version_id}/validate")
    assert validated.status_code == 200
    assert validated.json()["data"]["status"] == "validated"

    approved = await api_client.post(f"/api/workflow-versions/{version_id}/approve")
    assert approved.status_code == 200
    assert approved.json()["data"]["status"] == "approved"
    assert approved.json()["data"]["approved_at"] is not None


async def test_approve_before_validate_returns_409(
    api_client: httpx.AsyncClient, as_user: AsUser
) -> None:
    as_user(ALICE)
    project_id = await _create_project(api_client)
    workflow = await _create_workflow(api_client, project_id, COMPLETE_DEFINITION)
    version_id = workflow["versions"][0]["id"]

    response = await api_client.post(f"/api/workflow-versions/{version_id}/approve")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VERSION_NOT_VALIDATED"


async def test_patch_approved_version_returns_409(
    api_client: httpx.AsyncClient, as_user: AsUser
) -> None:
    as_user(ALICE)
    project_id = await _create_project(api_client)
    workflow = await _create_workflow(api_client, project_id, COMPLETE_DEFINITION)
    version_id = workflow["versions"][0]["id"]
    await api_client.post(f"/api/workflow-versions/{version_id}/validate")
    await api_client.post(f"/api/workflow-versions/{version_id}/approve")

    response = await api_client.patch(
        f"/api/workflow-versions/{version_id}", json={"definition": _VALID_DEFINITION}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VERSION_IMMUTABLE"


async def test_validate_complete_definition_returns_zero_warnings(
    api_client: httpx.AsyncClient, as_user: AsUser
) -> None:
    as_user(ALICE)
    project_id = await _create_project(api_client)
    workflow = await _create_workflow(api_client, project_id, COMPLETE_DEFINITION)
    version_id = workflow["versions"][0]["id"]

    response = await api_client.post(f"/api/workflow-versions/{version_id}/validate")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "validated"
    assert data["validation_warnings"] == []


async def test_validate_with_broken_reference_returns_422(
    api_client: httpx.AsyncClient, as_user: AsUser
) -> None:
    as_user(ALICE)
    project_id = await _create_project(api_client)
    workflow = await _create_workflow(api_client, project_id, COMPLETE_DEFINITION)
    version_id = workflow["versions"][0]["id"]

    broken = deepcopy(COMPLETE_DEFINITION)
    # 'extract' does not have 'draft' as an ancestor: not wired via edges.
    broken["steps"][1]["evidence"] = "$step.draft.summary_markdown"
    await api_client.patch(f"/api/workflow-versions/{version_id}", json={"definition": broken})

    response = await api_client.post(f"/api/workflow-versions/{version_id}/validate")

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "INVALID_WORKFLOW"
    problems = body["error"]["details"]
    assert any(
        problem["code"] == "reference_not_ancestor" and problem["path"] for problem in problems
    )

    version = await api_client.get(f"/api/workflow-versions/{version_id}")
    assert version.json()["data"]["status"] == "draft"


async def test_validate_with_unregistered_tool_returns_tool_not_registered(
    api_client: httpx.AsyncClient, as_user: AsUser
) -> None:
    as_user(ALICE)
    project_id = await _create_project(api_client)
    workflow = await _create_workflow(api_client, project_id, COMPLETE_DEFINITION)
    version_id = workflow["versions"][0]["id"]

    broken = deepcopy(COMPLETE_DEFINITION)
    broken["tools"].append("not_a_real_tool")
    broken["steps"][0]["tool"] = "not_a_real_tool"
    await api_client.patch(f"/api/workflow-versions/{version_id}", json={"definition": broken})

    response = await api_client.post(f"/api/workflow-versions/{version_id}/validate")

    assert response.status_code == 422
    problems = response.json()["error"]["details"]
    assert any(problem["code"] == "tool_not_registered" for problem in problems)


async def test_approve_refuses_invalid_version_with_422(
    api_client: httpx.AsyncClient, as_user: AsUser
) -> None:
    as_user(ALICE)
    project_id = await _create_project(api_client)
    workflow = await _create_workflow(api_client, project_id, COMPLETE_DEFINITION)
    version_id = workflow["versions"][0]["id"]
    await api_client.post(f"/api/workflow-versions/{version_id}/validate")

    broken = deepcopy(COMPLETE_DEFINITION)
    broken["steps"][1]["evidence"] = "$step.draft.summary_markdown"
    # PATCH resets the version to draft, so validate it back to 'validated'
    # first is not possible with a broken definition; approve must refuse
    # a version whose *current* definition fails semantic validation even
    # though it was validated before being edited.
    await api_client.patch(f"/api/workflow-versions/{version_id}", json={"definition": broken})

    response = await api_client.post(f"/api/workflow-versions/{version_id}/approve")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_WORKFLOW"


async def test_restore_creates_new_draft(api_client: httpx.AsyncClient, as_user: AsUser) -> None:
    as_user(ALICE)
    project_id = await _create_project(api_client)
    workflow = await _create_workflow(api_client, project_id)
    version_id = workflow["versions"][0]["id"]

    response = await api_client.post(f"/api/workflow-versions/{version_id}/restore")

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["version_number"] == 2
    assert data["status"] == "draft"
    assert data["source_request"] == "Restored from version 1"


async def test_restore_for_other_user_returns_404(
    api_client: httpx.AsyncClient, as_user: AsUser
) -> None:
    as_user(ALICE)
    project_id = await _create_project(api_client)
    workflow = await _create_workflow(api_client, project_id)
    version_id = workflow["versions"][0]["id"]

    as_user(BOB)
    response = await api_client.post(f"/api/workflow-versions/{version_id}/restore")

    assert response.status_code == 404
