"""API tests for workflow and workflow-version routes."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from copy import deepcopy
from typing import Any

import httpx2 as httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from northforge.api.deps import get_session
from northforge.api.main import create_app
from northforge.auth.dependencies import get_principal
from northforge.auth.principal import Principal
from northforge.core.config import Settings, load_settings
from northforge.db.models import User
from northforge.storage.memory import MemoryObjectStorage
from tests.api.conftest import AsUser
from tests.api.test_documents import _FakeArqPool
from tests.conftest import UNIT_DATABASE_URL, UNIT_REDIS_URL, set_unit_s3_env
from tests.fixtures_workflows import COMPLETE_DEFINITION

ALICE = Principal(subject="dev|alice")
BOB = Principal(subject="dev|bob")


# --- Fixtures for planner-configured (MODEL_PROVIDER=mock) app -----------------
#
# The shared ``settings``/``api_app``/``api_client``/``as_user`` fixtures build
# an app with ``MODEL_PROVIDER=nvidia`` and no key, so the planner routes
# always see ``planner_configured() is False`` there (used for the
# "not configured" tests below). The plan-route "happy path" tests need a
# planner that *is* configured, which means setting ``MODEL_PROVIDER=mock``
# before ``load_settings`` resolves -- these fixtures build a second,
# independent app for that, sharing the same ``db_session`` transaction so
# both apps see the same rows.


@pytest.fixture
def mock_models_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", UNIT_DATABASE_URL)
    monkeypatch.setenv("REDIS_URL", UNIT_REDIS_URL)
    monkeypatch.setenv("READINESS_TIMEOUT_SECONDS", "0.5")
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    set_unit_s3_env(monkeypatch)
    return load_settings(env_file=None)


@pytest.fixture
def api_app_mock_models(
    mock_models_settings: Settings, db_session: AsyncSession
) -> Iterator[FastAPI]:
    app = create_app(mock_models_settings, storage=MemoryObjectStorage())

    async def _override_get_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override_get_session
    yield app
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def api_client_mock_models(
    api_app_mock_models: FastAPI,
) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=api_app_mock_models)
    async with (
        api_app_mock_models.router.lifespan_context(api_app_mock_models),
        httpx.AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        yield client


@pytest.fixture
def as_user_mock_models(api_app_mock_models: FastAPI) -> Iterator[AsUser]:
    def _install(principal: Principal) -> None:
        api_app_mock_models.dependency_overrides[get_principal] = lambda: principal

    yield _install
    api_app_mock_models.dependency_overrides.pop(get_principal, None)


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


async def test_get_version_includes_planner_fields_defaulting_empty(
    api_client: httpx.AsyncClient, as_user: AsUser
) -> None:
    as_user(ALICE)
    project_id = await _create_project(api_client)
    workflow = await _create_workflow(api_client, project_id)
    version_id = workflow["versions"][0]["id"]

    response = await api_client.get(f"/api/workflow-versions/{version_id}")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["planner_output"] == {}
    assert data["model_snapshot"] == {}


# --- Plan a new workflow: POST /api/projects/{project_id}/workflows/plan ------


async def test_plan_new_workflow_returns_503_when_planner_not_configured(
    api_client: httpx.AsyncClient, as_user: AsUser
) -> None:
    as_user(ALICE)
    project_id = await _create_project(api_client)

    response = await api_client.post(
        f"/api/projects/{project_id}/workflows/plan",
        json={"request": "Draft a contract review workflow"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "PROVIDER_NOT_CONFIGURED"


async def test_plan_new_workflow_requires_request(
    api_client: httpx.AsyncClient, as_user: AsUser
) -> None:
    as_user(ALICE)
    project_id = await _create_project(api_client)

    response = await api_client.post(f"/api/projects/{project_id}/workflows/plan", json={})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_plan_new_workflow_404_for_a_non_owner(
    api_client: httpx.AsyncClient, as_user: AsUser
) -> None:
    as_user(ALICE)
    project_id = await _create_project(api_client)

    as_user(BOB)
    response = await api_client.post(
        f"/api/projects/{project_id}/workflows/plan",
        json={"request": "Draft a contract review workflow"},
    )

    assert response.status_code == 404


async def test_plan_new_workflow_returns_202_and_enqueues_the_job(
    api_client: httpx.AsyncClient,
    as_user: AsUser,
    api_client_mock_models: httpx.AsyncClient,
    api_app_mock_models: FastAPI,
    as_user_mock_models: AsUser,
    db_session: AsyncSession,
) -> None:
    as_user(ALICE)
    project_id = await _create_project(api_client)

    pool = _FakeArqPool()
    api_app_mock_models.state.arq_pool = pool
    as_user_mock_models(ALICE)

    response = await api_client_mock_models.post(
        f"/api/projects/{project_id}/workflows/plan",
        json={"request": "Draft a contract review workflow"},
    )

    assert response.status_code == 202
    assert response.json()["data"]["job_id"] == "fake-job-123"

    user_row = (
        await db_session.execute(select(User).where(User.clerk_user_id == "dev|alice"))
    ).scalar_one()
    assert pool.enqueued == [
        (
            "plan_workflow",
            (project_id, str(user_row.id), "Draft a contract review workflow", [], None, None),
        )
    ]


async def test_plan_new_workflow_returns_503_when_queue_unavailable(
    api_client_mock_models: httpx.AsyncClient,
    as_user_mock_models: AsUser,
) -> None:
    as_user_mock_models(ALICE)
    project_response = await api_client_mock_models.post("/api/projects", json={"name": "Project"})
    project_id = project_response.json()["data"]["id"]

    response = await api_client_mock_models.post(
        f"/api/projects/{project_id}/workflows/plan",
        json={"request": "Draft a contract review workflow"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "QUEUE_UNAVAILABLE"


# --- Re-plan an existing workflow: POST /api/workflows/{workflow_id}/plan -----


async def test_replan_workflow_requires_request_or_answers(
    api_client: httpx.AsyncClient, as_user: AsUser
) -> None:
    as_user(ALICE)
    project_id = await _create_project(api_client)
    workflow = await _create_workflow(api_client, project_id)

    response = await api_client.post(f"/api/workflows/{workflow['id']}/plan", json={})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_replan_workflow_404_for_missing_workflow(
    api_client: httpx.AsyncClient, as_user: AsUser
) -> None:
    as_user(ALICE)

    response = await api_client.post(
        "/api/workflows/00000000-0000-0000-0000-000000000000/plan",
        json={"answers": ["Only Acme"]},
    )

    assert response.status_code == 404


async def test_replan_workflow_returns_202_with_answers_only(
    api_client: httpx.AsyncClient,
    as_user: AsUser,
    api_client_mock_models: httpx.AsyncClient,
    api_app_mock_models: FastAPI,
    as_user_mock_models: AsUser,
    db_session: AsyncSession,
) -> None:
    as_user(ALICE)
    project_id = await _create_project(api_client)
    workflow = await _create_workflow(api_client, project_id)

    pool = _FakeArqPool()
    api_app_mock_models.state.arq_pool = pool
    as_user_mock_models(ALICE)

    response = await api_client_mock_models.post(
        f"/api/workflows/{workflow['id']}/plan",
        json={"answers": ["Only Acme"]},
    )

    assert response.status_code == 202
    user_row = (
        await db_session.execute(select(User).where(User.clerk_user_id == "dev|alice"))
    ).scalar_one()
    assert pool.enqueued == [
        (
            "plan_workflow",
            (project_id, str(user_row.id), None, ["Only Acme"], None, workflow["id"]),
        )
    ]
