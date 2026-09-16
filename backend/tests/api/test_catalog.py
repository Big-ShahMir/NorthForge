"""API tests for the read-only tool and workflow-step-type catalog routes."""

from __future__ import annotations

import httpx2 as httpx

from northforge.auth.principal import Principal
from tests.api.conftest import AsUser

ALICE = Principal(subject="dev|alice")

_EXPECTED_TOOL_NAMES = {"search_documents", "get_document_chunk", "lookup_policy_rules"}

_EXPECTED_STEP_TYPES = {
    "retrieve_documents",
    "extract_fields",
    "compare_policy",
    "classify",
    "draft_summary",
    "human_review",
    "validate_output",
    "finish",
}


async def test_list_tools_requires_auth(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get("/api/tools")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_list_workflow_step_types_requires_auth(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get("/api/workflow-step-types")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_list_tools_returns_three_tools_with_forbidding_schemas(
    api_client: httpx.AsyncClient, as_user: AsUser
) -> None:
    as_user(ALICE)

    response = await api_client.get("/api/tools")

    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert {item["name"] for item in items} == _EXPECTED_TOOL_NAMES
    for item in items:
        assert item["input_schema"]["additionalProperties"] is False
        assert item["output_schema"]["additionalProperties"] is False
        assert item["side_effect_class"] in {"read_only", "draft_only"}
        assert item["access_scope"] in {"project_documents", "policy_library"}
        assert item["kind"] in {"retrieval", "lookup"}
        assert item["description"]


async def test_list_workflow_step_types_returns_eight_types(
    api_client: httpx.AsyncClient, as_user: AsUser
) -> None:
    as_user(ALICE)

    response = await api_client.get("/api/workflow-step-types")

    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert len(items) == 8
    assert {item["type"] for item in items} == _EXPECTED_STEP_TYPES
    for item in items:
        assert item["config_schema"]["additionalProperties"] is False
        assert item["output_schema"]["additionalProperties"] is False
        assert item["title"]
        assert item["description"]
