"""Schema and constraint tests run against a migrated PostgreSQL database.

These tests exercise the actual database (via the ``db_session`` fixture,
which wraps every test in a rolled-back transaction) rather than the ORM in
isolation, so they catch drift between ``northforge/db/models.py`` and the
hand-written Alembic migration.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from northforge.db.models import (
    Project,
    User,
    Workflow,
    WorkflowRun,
    WorkflowVersion,
    WorkflowVersionStatus,
)

pytestmark = pytest.mark.usefixtures("migrated_database")

EXPECTED_TABLES = {
    "users",
    "projects",
    "workflows",
    "workflow_versions",
    "workflow_runs",
    "step_runs",
    "trace_events",
    "feedback_labels",
    "evaluation_cases",
    "evaluation_runs",
    "evaluation_results",
}

MakeUser = Callable[..., Awaitable[User]]


async def _seed_workflow(
    session: AsyncSession, make_user: MakeUser
) -> tuple[User, Project, Workflow]:
    user = await make_user(session, f"user_{uuid4().hex}")
    project = Project(
        owner_id=user.id, name="Contract review", description="", vertical="contract_review"
    )
    session.add(project)
    await session.flush()
    workflow = Workflow(
        project_id=project.id, name="Review workflow", description="", created_by=user.id
    )
    session.add(workflow)
    await session.flush()
    return user, project, workflow


async def _seed_version(
    session: AsyncSession,
    make_user: MakeUser,
    *,
    definition_json: dict[str, Any] | None = None,
) -> tuple[User, Project, Workflow, WorkflowVersion]:
    user, project, workflow = await _seed_workflow(session, make_user)
    version = WorkflowVersion(
        workflow_id=workflow.id,
        version_number=1,
        definition_json=definition_json or {"schema_version": 1, "name": "Review"},
        status=WorkflowVersionStatus.DRAFT.value,
        created_by=user.id,
    )
    session.add(version)
    await session.flush()
    return user, project, workflow, version


@pytest.mark.asyncio
async def test_schema_has_all_eleven_tables(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
    )
    table_names = {row[0] for row in result.all()}
    assert EXPECTED_TABLES <= table_names


@pytest.mark.asyncio
async def test_check_constraint_rejects_invalid_workflow_version_status(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    user, _project, workflow = await _seed_workflow(db_session, make_user)
    bad_version = WorkflowVersion(
        workflow_id=workflow.id,
        version_number=1,
        definition_json={},
        status="not_a_real_status",
        created_by=user.id,
    )
    db_session.add(bad_version)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_check_constraint_rejects_invalid_run_status(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    user, project, _workflow, version = await _seed_version(db_session, make_user)
    bad_run = WorkflowRun(
        workflow_version_id=version.id,
        project_id=project.id,
        status="not_a_real_status",
        input_json={},
        created_by=user.id,
    )
    db_session.add(bad_run)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_unique_constraint_on_workflow_id_and_version_number(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    user, _project, workflow = await _seed_workflow(db_session, make_user)
    first = WorkflowVersion(
        workflow_id=workflow.id,
        version_number=1,
        definition_json={},
        status=WorkflowVersionStatus.DRAFT.value,
        created_by=user.id,
    )
    db_session.add(first)
    await db_session.flush()

    duplicate = WorkflowVersion(
        workflow_id=workflow.id,
        version_number=1,
        definition_json={},
        status=WorkflowVersionStatus.DRAFT.value,
        created_by=user.id,
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_partial_unique_index_allows_multiple_null_idempotency_keys(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    user, project, _workflow, version = await _seed_version(db_session, make_user)
    run_a = WorkflowRun(
        workflow_version_id=version.id,
        project_id=project.id,
        status="queued",
        input_json={},
        created_by=user.id,
        idempotency_key=None,
    )
    run_b = WorkflowRun(
        workflow_version_id=version.id,
        project_id=project.id,
        status="queued",
        input_json={},
        created_by=user.id,
        idempotency_key=None,
    )
    db_session.add_all([run_a, run_b])
    # Two NULL idempotency keys for the same version must both be allowed.
    await db_session.flush()


@pytest.mark.asyncio
async def test_partial_unique_index_rejects_duplicate_idempotency_key(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    user, project, _workflow, version = await _seed_version(db_session, make_user)
    run_a = WorkflowRun(
        workflow_version_id=version.id,
        project_id=project.id,
        status="queued",
        input_json={},
        created_by=user.id,
        idempotency_key="dup-key",
    )
    db_session.add(run_a)
    await db_session.flush()

    run_b = WorkflowRun(
        workflow_version_id=version.id,
        project_id=project.id,
        status="queued",
        input_json={},
        created_by=user.id,
        idempotency_key="dup-key",
    )
    db_session.add(run_b)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_deferred_fk_current_version_id_works(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    user, _project, workflow = await _seed_workflow(db_session, make_user)
    version = WorkflowVersion(
        workflow_id=workflow.id,
        version_number=1,
        definition_json={},
        status=WorkflowVersionStatus.DRAFT.value,
        created_by=user.id,
    )
    db_session.add(version)
    await db_session.flush()

    workflow.current_version_id = version.id
    # Flushing the deferred FK (added via ALTER TABLE in the migration)
    # must succeed now that the referenced version row exists.
    await db_session.flush()

    assert workflow.current_version_id == version.id


@pytest.mark.asyncio
async def test_planner_output_json_defaults_to_empty_object(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    _user, _project, _workflow, version = await _seed_version(db_session, make_user)

    assert version.planner_output_json == {}


@pytest.mark.asyncio
async def test_jsonb_round_trips_nested_dict(db_session: AsyncSession, make_user: MakeUser) -> None:
    nested_definition = {
        "schema_version": 1,
        "name": "Nested",
        "steps": [
            {
                "id": "retrieve",
                "type": "retrieve_documents",
                "label": "Retrieve",
                "meta": {"a": [1, 2, 3]},
            }
        ],
        "policies": {"nested": {"deeply": {"value": True, "list": [1, "two", 3.0, None]}}},
    }
    _user, _project, _workflow, version = await _seed_version(
        db_session, make_user, definition_json=nested_definition
    )
    await db_session.flush()
    version_id = version.id
    db_session.expire_all()

    fetched = await db_session.get(WorkflowVersion, version_id)
    assert fetched is not None
    assert fetched.definition_json == nested_definition
