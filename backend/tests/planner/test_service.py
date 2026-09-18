"""Database tests for ``planner.service.plan_workflow`` (Stage C, this agent's slice).

NOTE (Stage C agent, 2026-09-18): these tests import ``build_mock_router``
from ``tests.planner.conftest`` and the shared proposal fixtures from
``tests.planner.fixtures`` -- both being written concurrently by the Stage A
(planner graph) agent and not present at the time this file was written.
They also call ``northforge.planner.service.plan_workflow``, whose body is
still ``raise NotImplementedError(...)`` as of this writing. These tests
could not be executed; see the handback report for the exact error.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from northforge.db.models import Project, User, WorkflowVersionStatus
from northforge.db.repositories.projects import ProjectsRepository
from northforge.db.repositories.workflows import WorkflowsRepository
from northforge.planner.service import plan_workflow
from northforge.tools.registry import default_registry
from tests.planner.conftest import build_mock_router
from tests.planner.fixtures import (
    AMBIGUOUS_PROPOSAL,
    COMMON_PROPOSAL,
    COMMON_REQUEST,
    REJECTED_PROPOSAL,
)

MakeUser = Callable[..., Awaitable[User]]


async def _project(db_session: AsyncSession, make_user: MakeUser) -> tuple[User, Project]:
    owner = await make_user(db_session, f"user_{uuid.uuid4().hex}")
    project = await ProjectsRepository(db_session).create(
        owner.id, "Project", "", "contract_review"
    )
    return owner, project


async def test_common_proposal_persists_workflow_and_draft_version(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner, project = await _project(db_session, make_user)
    router, _provider = build_mock_router(structured=[COMMON_PROPOSAL])
    registry = default_registry()

    result = await plan_workflow(
        db_session,
        model_router=router,
        tool_registry=registry,
        project=project,
        user=owner,
        request=COMMON_REQUEST,
    )

    assert result.outcome == "proposed"
    assert result.workflow_id is not None
    assert result.version_id is not None

    workflow = await WorkflowsRepository(db_session).get_for_owner(result.workflow_id, owner.id)
    assert workflow is not None
    assert len(workflow.versions) == 1
    version = workflow.versions[0]
    assert version.version_number == 1
    assert version.status == WorkflowVersionStatus.DRAFT.value
    assert version.source_request == COMMON_REQUEST
    assert version.model_config_json == router.snapshot()
    assert version.planner_output_json["outcome"] == "proposed"
    assert isinstance(version.validation_warnings_json, list)
    assert all(isinstance(item, str) for item in version.validation_warnings_json)


async def test_name_override_wins_over_proposal_name(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner, project = await _project(db_session, make_user)
    router, _provider = build_mock_router(structured=[COMMON_PROPOSAL])
    registry = default_registry()

    result = await plan_workflow(
        db_session,
        model_router=router,
        tool_registry=registry,
        project=project,
        user=owner,
        request=COMMON_REQUEST,
        name="My Custom Name",
    )

    assert result.workflow_id is not None
    workflow = await WorkflowsRepository(db_session).get_for_owner(result.workflow_id, owner.id)
    assert workflow is not None
    assert workflow.name == "My Custom Name"


async def test_replan_creates_version_two_and_leaves_current_version_unchanged(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner, project = await _project(db_session, make_user)
    router, _provider = build_mock_router(structured=[COMMON_PROPOSAL, COMMON_PROPOSAL])
    registry = default_registry()

    first = await plan_workflow(
        db_session,
        model_router=router,
        tool_registry=registry,
        project=project,
        user=owner,
        request=COMMON_REQUEST,
    )
    assert first.workflow_id is not None
    workflow = await WorkflowsRepository(db_session).get_for_owner(first.workflow_id, owner.id)
    assert workflow is not None
    first_current_version_id = workflow.current_version_id

    second = await plan_workflow(
        db_session,
        model_router=router,
        tool_registry=registry,
        project=project,
        user=owner,
        request=None,
        answers=["Only Acme"],
        workflow=workflow,
    )

    assert second.workflow_id == first.workflow_id
    await db_session.refresh(workflow)
    assert workflow.current_version_id == first_current_version_id

    repo = WorkflowsRepository(db_session)
    latest = await repo.latest_version(workflow.id)
    assert latest is not None
    assert latest.version_number == 2
    assert latest.id == second.version_id
    assert latest.source_request is not None
    assert "Only Acme" in latest.source_request


async def test_needs_clarification_proposal_still_persists_a_draft(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner, project = await _project(db_session, make_user)
    router, _provider = build_mock_router(structured=[AMBIGUOUS_PROPOSAL])
    registry = default_registry()

    result = await plan_workflow(
        db_session,
        model_router=router,
        tool_registry=registry,
        project=project,
        user=owner,
        request=COMMON_REQUEST,
    )

    assert result.outcome == "needs_clarification"
    assert result.workflow_id is not None
    assert result.version_id is not None


async def test_rejected_proposal_persists_nothing(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner, project = await _project(db_session, make_user)
    router, _provider = build_mock_router(structured=[REJECTED_PROPOSAL])
    registry = default_registry()

    result = await plan_workflow(
        db_session,
        model_router=router,
        tool_registry=registry,
        project=project,
        user=owner,
        request=COMMON_REQUEST,
    )

    assert result.outcome == "rejected"
    assert result.workflow_id is None
    assert result.version_id is None
    assert result.planner_output is not None


async def test_no_request_and_no_answers_on_new_workflow_raises_value_error(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner, project = await _project(db_session, make_user)
    router, _provider = build_mock_router(structured=[COMMON_PROPOSAL])
    registry = default_registry()

    with pytest.raises(ValueError):
        await plan_workflow(
            db_session,
            model_router=router,
            tool_registry=registry,
            project=project,
            user=owner,
            request=None,
        )
