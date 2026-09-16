"""Repository tests for ``WorkflowsRepository``, run against a migrated database."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from northforge.core.errors import ConflictError, InvalidWorkflowError
from northforge.db.models import User, WorkflowVersionStatus
from northforge.db.repositories.projects import ProjectsRepository
from northforge.db.repositories.workflows import WorkflowsRepository
from northforge.schemas.workflow import WorkflowDefinition, validate_definition
from tests.fixtures_workflows import COMPLETE_DEFINITION, REAL_TOOL_NAMES

MakeUser = Callable[..., Awaitable[User]]

#: The real tool names (see ``tests.fixtures_workflows``), used wherever a
#: test needs a semantic-layer ``known_tools`` set.
KNOWN_TOOLS = frozenset(REAL_TOOL_NAMES)

#: Parses cleanly but is semantically incomplete: its ``retrieve_documents``
#: step has no ``query`` and declares an unregistered tool. Fine for tests
#: that only exercise ``create``/``create_version`` (the parse layer, which
#: never blocks on semantic errors); tests that call ``validate``/``approve``
#: need a semantically complete definition instead (``_complete_definition``).
_VALID_RAW: dict[str, Any] = {
    "schema_version": 1,
    "name": "Contract review",
    "steps": [
        {"id": "retrieve", "type": "retrieve_documents", "label": "Retrieve"},
        {"id": "finish", "type": "finish", "label": "Finish"},
    ],
    "edges": [{"source": "retrieve", "target": "finish"}],
    "tools": ["doc_search"],
}

#: A minimal definition with zero semantic errors and exactly one warning
#: (``no_human_review``), for the one test that asserts warnings are stored.
_WARNING_ONLY_RAW: dict[str, Any] = {
    "schema_version": 1,
    "name": "Minimal",
    "steps": [
        {
            "id": "retrieve",
            "type": "retrieve_documents",
            "label": "Retrieve",
            "query": "acme master service agreement renewal terms",
        },
        {
            "id": "finish",
            "type": "finish",
            "label": "Finish",
            "result": {"chunks": "$step.retrieve.chunks"},
        },
    ],
    "edges": [{"source": "retrieve", "target": "finish"}],
    "tools": ["search_documents"],
}


def _definition(name: str = "Contract review") -> WorkflowDefinition:
    raw = dict(_VALID_RAW, name=name)
    definition, _warnings = validate_definition(raw)
    return definition


def _complete_definition(name: str = "Contract review") -> WorkflowDefinition:
    raw = dict(COMPLETE_DEFINITION, name=name)
    definition, _warnings = validate_definition(raw)
    return definition


async def _project(db_session: AsyncSession, make_user: MakeUser, subject: str = "user_a") -> Any:
    owner = await make_user(db_session, subject)
    project = await ProjectsRepository(db_session).create(
        owner.id, "Project", "", "contract_review"
    )
    return owner, project


async def test_create_makes_version_one_draft_and_sets_current_version(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner, project = await _project(db_session, make_user)
    repo = WorkflowsRepository(db_session)

    workflow = await repo.create(project, "Review", "", _definition(), owner.id)

    assert len(workflow.versions) == 1
    version = workflow.versions[0]
    assert version.version_number == 1
    assert version.status == WorkflowVersionStatus.DRAFT.value
    assert workflow.current_version_id == version.id


async def test_get_for_owner_returns_none_for_missing_workflow(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner, _ = await _project(db_session, make_user)

    result = await WorkflowsRepository(db_session).get_for_owner(uuid.uuid4(), owner.id)

    assert result is None


async def test_get_for_owner_returns_none_for_other_owner(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner_a, project_a = await _project(db_session, make_user, "user_a")
    owner_b, _project_b = await _project(db_session, make_user, "user_b")
    workflow = await WorkflowsRepository(db_session).create(
        project_a, "Review", "", _definition(), owner_a.id
    )

    result = await WorkflowsRepository(db_session).get_for_owner(workflow.id, owner_b.id)

    assert result is None


async def test_get_for_owner_returns_workflow_with_versions_loaded(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner, project = await _project(db_session, make_user)
    created = await WorkflowsRepository(db_session).create(
        project, "Review", "", _definition(), owner.id
    )

    result = await WorkflowsRepository(db_session).get_for_owner(created.id, owner.id)

    assert result is not None
    assert len(result.versions) == 1


async def test_get_version_for_owner_returns_none_for_other_owner(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner_a, project_a = await _project(db_session, make_user, "user_a")
    owner_b, _project_b = await _project(db_session, make_user, "user_b")
    workflow = await WorkflowsRepository(db_session).create(
        project_a, "Review", "", _definition(), owner_a.id
    )
    version = workflow.versions[0]

    result = await WorkflowsRepository(db_session).get_version_for_owner(version.id, owner_b.id)

    assert result is None


async def test_list_for_project_returns_workflows_with_current_version_loaded(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner, project = await _project(db_session, make_user)
    repo = WorkflowsRepository(db_session)
    await repo.create(project, "Review A", "", _definition(), owner.id)
    await repo.create(project, "Review B", "", _definition(), owner.id)

    workflows, total = await repo.list_for_project(project.id)

    assert len(workflows) == 2
    assert total == 2
    assert all(workflow.current_version is not None for workflow in workflows)

    page, total = await repo.list_for_project(project.id, limit=1, offset=1)
    assert len(page) == 1
    assert total == 2


async def test_create_version_increments_number_and_leaves_current_version_unchanged(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner, project = await _project(db_session, make_user)
    repo = WorkflowsRepository(db_session)
    workflow = await repo.create(project, "Review", "", _definition(), owner.id)
    first_version_id = workflow.current_version_id

    v2 = await repo.create_version(workflow, _definition("Review v2"), owner.id, "please update")

    assert v2.version_number == 2
    assert v2.status == WorkflowVersionStatus.DRAFT.value
    assert v2.source_request == "please update"
    await db_session.refresh(workflow)
    assert workflow.current_version_id == first_version_id


async def test_update_definition_resets_status_to_draft(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner, project = await _project(db_session, make_user)
    repo = WorkflowsRepository(db_session)
    workflow = await repo.create(project, "Review", "", _complete_definition(), owner.id)
    version = workflow.versions[0]
    await repo.validate(version, known_tools=KNOWN_TOOLS)
    assert version.status == WorkflowVersionStatus.VALIDATED.value

    updated = await repo.update_definition(version, _complete_definition("Review updated"))

    assert updated.status == WorkflowVersionStatus.DRAFT.value
    assert updated.validation_warnings_json == []


async def test_update_definition_raises_conflict_when_approved(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner, project = await _project(db_session, make_user)
    repo = WorkflowsRepository(db_session)
    workflow = await repo.create(project, "Review", "", _complete_definition(), owner.id)
    version = workflow.versions[0]
    await repo.validate(version, known_tools=KNOWN_TOOLS)
    await repo.approve(version, known_tools=KNOWN_TOOLS)

    with pytest.raises(ConflictError) as exc_info:
        await repo.update_definition(version, _complete_definition())
    assert exc_info.value.code == "VERSION_IMMUTABLE"


async def test_validate_stores_soft_warnings(db_session: AsyncSession, make_user: MakeUser) -> None:
    owner, project = await _project(db_session, make_user)
    repo = WorkflowsRepository(db_session)
    minimal, _warnings = validate_definition(_WARNING_ONLY_RAW)
    workflow = await repo.create(project, "Review", "", minimal, owner.id)
    version = workflow.versions[0]

    validated = await repo.validate(version, known_tools=KNOWN_TOOLS)

    assert validated.status == WorkflowVersionStatus.VALIDATED.value
    assert len(validated.validation_warnings_json) > 0


async def test_validate_raises_invalid_workflow_for_semantic_errors(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner, project = await _project(db_session, make_user)
    repo = WorkflowsRepository(db_session)
    workflow = await repo.create(project, "Review", "", _definition(), owner.id)
    version = workflow.versions[0]

    with pytest.raises(InvalidWorkflowError) as exc_info:
        await repo.validate(version, known_tools=KNOWN_TOOLS)
    assert exc_info.value.details
    assert version.status == WorkflowVersionStatus.DRAFT.value


async def test_validate_raises_conflict_when_immutable(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner, project = await _project(db_session, make_user)
    repo = WorkflowsRepository(db_session)
    workflow = await repo.create(project, "Review", "", _complete_definition(), owner.id)
    version = workflow.versions[0]
    await repo.validate(version, known_tools=KNOWN_TOOLS)
    await repo.approve(version, known_tools=KNOWN_TOOLS)

    with pytest.raises(ConflictError) as exc_info:
        await repo.validate(version, known_tools=KNOWN_TOOLS)
    assert exc_info.value.code == "VERSION_IMMUTABLE"


async def test_approve_raises_conflict_before_validated(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner, project = await _project(db_session, make_user)
    repo = WorkflowsRepository(db_session)
    workflow = await repo.create(project, "Review", "", _complete_definition(), owner.id)
    version = workflow.versions[0]

    with pytest.raises(ConflictError) as exc_info:
        await repo.approve(version, known_tools=KNOWN_TOOLS)
    assert exc_info.value.code == "VERSION_NOT_VALIDATED"


async def test_approve_raises_invalid_workflow_for_semantic_errors(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner, project = await _project(db_session, make_user)
    repo = WorkflowsRepository(db_session)
    workflow = await repo.create(project, "Review", "", _definition(), owner.id)
    version = workflow.versions[0]

    with pytest.raises(InvalidWorkflowError) as exc_info:
        await repo.approve(version, known_tools=KNOWN_TOOLS)
    assert exc_info.value.details


async def test_approve_sets_status_approved_at_and_current_version(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner, project = await _project(db_session, make_user)
    repo = WorkflowsRepository(db_session)
    workflow = await repo.create(project, "Review", "", _complete_definition(), owner.id)
    v2 = await repo.create_version(workflow, _complete_definition("v2"), owner.id)
    await repo.validate(v2, known_tools=KNOWN_TOOLS)

    approved = await repo.approve(v2, known_tools=KNOWN_TOOLS)

    assert approved.status == WorkflowVersionStatus.APPROVED.value
    assert approved.approved_at is not None
    await db_session.refresh(workflow)
    assert workflow.current_version_id == v2.id


async def test_restore_creates_new_draft_copying_definition(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner, project = await _project(db_session, make_user)
    repo = WorkflowsRepository(db_session)
    workflow = await repo.create(project, "Review", "", _definition(), owner.id)
    v1 = workflow.versions[0]

    restored = await repo.restore(workflow, v1, owner.id)

    assert restored.version_number == 2
    assert restored.status == WorkflowVersionStatus.DRAFT.value
    assert restored.definition_json == v1.definition_json
    assert restored.source_request == "Restored from version 1"
