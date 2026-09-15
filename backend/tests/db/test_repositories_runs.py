"""Repository tests for ``RunsRepository``, run against a migrated database."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from northforge.core.errors import ConflictError
from northforge.db.models import Project, RunStatus, StepRunStatus, User, WorkflowVersion
from northforge.db.repositories.projects import ProjectsRepository
from northforge.db.repositories.runs import RunsRepository
from northforge.db.repositories.workflows import WorkflowsRepository
from northforge.schemas.workflow import validate_definition

MakeUser = Callable[..., Awaitable[User]]

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


async def _approved_version(
    db_session: AsyncSession, make_user: MakeUser, subject: str = "user_a"
) -> tuple[User, Project, WorkflowVersion]:
    owner = await make_user(db_session, subject)
    project = await ProjectsRepository(db_session).create(
        owner.id, "Project", "", "contract_review"
    )
    wf_repo = WorkflowsRepository(db_session)
    definition, _warnings = validate_definition(_VALID_RAW)
    workflow = await wf_repo.create(project, "Review", "", definition, owner.id)
    version = workflow.versions[0]
    await wf_repo.validate(version)
    await wf_repo.approve(version)
    return owner, project, version


async def test_create_requires_approved_version(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner = await make_user(db_session, "user_a")
    project = await ProjectsRepository(db_session).create(
        owner.id, "Project", "", "contract_review"
    )
    definition, _warnings = validate_definition(_VALID_RAW)
    workflow = await WorkflowsRepository(db_session).create(
        project, "Review", "", definition, owner.id
    )
    version = workflow.versions[0]  # still draft

    with pytest.raises(ConflictError) as exc_info:
        await RunsRepository(db_session).create(version, project, {}, owner.id)
    assert exc_info.value.code == "VERSION_NOT_APPROVED"


async def test_create_returns_new_queued_run(db_session: AsyncSession, make_user: MakeUser) -> None:
    owner, project, version = await _approved_version(db_session, make_user)

    run, created = await RunsRepository(db_session).create(version, project, {"doc": "a"}, owner.id)

    assert created is True
    assert run.status == RunStatus.QUEUED.value
    assert run.workflow_version_id == version.id
    assert run.project_id == project.id


async def test_create_is_idempotent_for_repeated_key(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner, project, version = await _approved_version(db_session, make_user)
    repo = RunsRepository(db_session)

    first, created_first = await repo.create(version, project, {}, owner.id, idempotency_key="k1")
    second, created_second = await repo.create(version, project, {}, owner.id, idempotency_key="k1")

    assert created_first is True
    assert created_second is False
    assert first.id == second.id


async def test_create_with_different_idempotency_keys_makes_two_runs(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner, project, version = await _approved_version(db_session, make_user)
    repo = RunsRepository(db_session)

    first, _ = await repo.create(version, project, {}, owner.id, idempotency_key="k1")
    second, _ = await repo.create(version, project, {}, owner.id, idempotency_key="k2")

    assert first.id != second.id


async def test_get_for_owner_returns_none_for_other_owner(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner, project, version = await _approved_version(db_session, make_user)
    other = await make_user(db_session, "user_b")
    run, _ = await RunsRepository(db_session).create(version, project, {}, owner.id)

    result = await RunsRepository(db_session).get_for_owner(run.id, other.id)

    assert result is None


async def test_get_for_owner_returns_run_for_correct_owner(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner, project, version = await _approved_version(db_session, make_user)
    run, _ = await RunsRepository(db_session).create(version, project, {}, owner.id)

    result = await RunsRepository(db_session).get_for_owner(run.id, owner.id)

    assert result is not None
    assert result.id == run.id


@pytest.mark.parametrize(
    ("start", "target", "valid"),
    [
        (RunStatus.QUEUED.value, RunStatus.RUNNING.value, True),
        (RunStatus.QUEUED.value, RunStatus.CANCELLED.value, True),
        (RunStatus.QUEUED.value, RunStatus.COMPLETED.value, False),
        (RunStatus.QUEUED.value, RunStatus.PAUSED.value, False),
        (RunStatus.RUNNING.value, RunStatus.PAUSED.value, True),
        (RunStatus.RUNNING.value, RunStatus.COMPLETED.value, True),
        (RunStatus.RUNNING.value, RunStatus.FAILED.value, True),
        (RunStatus.RUNNING.value, RunStatus.CANCELLED.value, True),
        (RunStatus.PAUSED.value, RunStatus.RUNNING.value, True),
        (RunStatus.PAUSED.value, RunStatus.CANCELLED.value, True),
        (RunStatus.PAUSED.value, RunStatus.COMPLETED.value, False),
        (RunStatus.COMPLETED.value, RunStatus.RUNNING.value, False),
        (RunStatus.FAILED.value, RunStatus.RUNNING.value, False),
        (RunStatus.CANCELLED.value, RunStatus.RUNNING.value, False),
    ],
)
async def test_transition_table(
    db_session: AsyncSession, make_user: MakeUser, start: str, target: str, valid: bool
) -> None:
    owner, project, version = await _approved_version(db_session, make_user)
    run, _ = await RunsRepository(db_session).create(version, project, {}, owner.id)
    run.status = start
    await db_session.flush()
    repo = RunsRepository(db_session)

    if valid:
        updated = await repo.transition(run, target)
        assert updated.status == target
    else:
        with pytest.raises(ConflictError) as exc_info:
            await repo.transition(run, target)
        assert exc_info.value.code == "INVALID_RUN_TRANSITION"


async def test_transition_sets_started_and_completed_timestamps(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner, project, version = await _approved_version(db_session, make_user)
    run, _ = await RunsRepository(db_session).create(version, project, {}, owner.id)
    repo = RunsRepository(db_session)

    running = await repo.transition(run, RunStatus.RUNNING.value)
    assert running.started_at is not None
    assert running.completed_at is None

    completed = await repo.transition(run, RunStatus.COMPLETED.value, result_json={"ok": True})
    assert completed.completed_at is not None
    assert completed.result_json == {"ok": True}


async def test_step_run_lifecycle(db_session: AsyncSession, make_user: MakeUser) -> None:
    owner, project, version = await _approved_version(db_session, make_user)
    run, _ = await RunsRepository(db_session).create(version, project, {}, owner.id)
    repo = RunsRepository(db_session)

    step_run = await repo.create_step_run(run, "retrieve", input_json={"q": "x"})
    assert step_run.status == StepRunStatus.PENDING.value

    finished = await repo.finish_step_run(
        step_run, StepRunStatus.COMPLETED.value, output_json={"n": 1}
    )
    assert finished.status == StepRunStatus.COMPLETED.value
    assert finished.completed_at is not None
    assert finished.output_json == {"n": 1}


async def test_append_event_assigns_sequential_numbers(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner, project, version = await _approved_version(db_session, make_user)
    run, _ = await RunsRepository(db_session).create(version, project, {}, owner.id)
    repo = RunsRepository(db_session)

    first = await repo.append_event(run, "run.queued", {})
    second = await repo.append_event(run, "run.started", {})
    third = await repo.append_event(run, "run.completed", {"result": "ok"})

    assert [first.sequence_number, second.sequence_number, third.sequence_number] == [1, 2, 3]


async def test_list_events_filters_by_after_sequence_and_orders(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner, project, version = await _approved_version(db_session, make_user)
    run, _ = await RunsRepository(db_session).create(version, project, {}, owner.id)
    repo = RunsRepository(db_session)
    await repo.append_event(run, "a", {})
    await repo.append_event(run, "b", {})
    await repo.append_event(run, "c", {})

    events = await repo.list_events(run.id, after_sequence=1)

    assert [event.event_type for event in events] == ["b", "c"]


async def test_list_events_respects_limit(db_session: AsyncSession, make_user: MakeUser) -> None:
    owner, project, version = await _approved_version(db_session, make_user)
    run, _ = await RunsRepository(db_session).create(version, project, {}, owner.id)
    repo = RunsRepository(db_session)
    for i in range(5):
        await repo.append_event(run, f"event-{i}", {})

    events = await repo.list_events(run.id, limit=2)

    assert len(events) == 2
    assert [event.sequence_number for event in events] == [1, 2]
