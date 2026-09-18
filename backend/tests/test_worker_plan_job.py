"""Database test for the ``plan_workflow`` arq job (``northforge.worker.main``).

NOTE (Stage C agent, 2026-09-18): like ``tests/planner/test_service.py``,
this depends on ``tests.planner.conftest.build_mock_router`` (Stage A,
concurrent) and on ``northforge.planner.service.plan_workflow`` actually
being implemented (currently ``raise NotImplementedError``). It could not
be executed at the time this file was written; see the handback report.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from northforge.db.models import Project, User
from northforge.db.repositories.projects import ProjectsRepository
from northforge.providers.mock import ScriptedFailure
from northforge.worker.main import plan_workflow
from tests.planner.conftest import build_mock_router
from tests.planner.fixtures import COMMON_PROPOSAL, COMMON_REQUEST

MakeUser = Callable[..., Awaitable[User]]


async def _project(db_session: AsyncSession, make_user: MakeUser) -> tuple[User, Project]:
    owner = await make_user(db_session, f"user_{uuid.uuid4().hex}")
    project = await ProjectsRepository(db_session).create(
        owner.id, "Project", "", "contract_review"
    )
    return owner, project


async def _worker_session_factory(
    db_session: AsyncSession,
) -> Callable[[], AsyncIterator[AsyncSession]]:
    """A ``session_factory``-shaped callable bound to ``db_session``'s connection.

    Mirrors the shape ``ctx["session_factory"]`` has in production (an
    ``async_sessionmaker``): calling it returns something usable as
    ``async with session_factory() as session``. Every session it creates
    joins the same connection-level transaction as ``db_session`` (as a
    nested savepoint), so writes made by the job are visible to assertions
    made through ``db_session`` afterwards, and everything is rolled back by
    the ``db_session`` fixture's teardown regardless of what the job commits.
    """
    connection = await db_session.connection()

    def _factory() -> AsyncSession:
        return AsyncSession(
            bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        )

    return _factory  # type: ignore[return-value]


async def test_plan_workflow_job_returns_outcome_and_ids(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner, project = await _project(db_session, make_user)
    router, _provider = build_mock_router(structured=[COMMON_PROPOSAL])
    session_factory = await _worker_session_factory(db_session)

    ctx = {"session_factory": session_factory, "model_router": router, "job_id": "test"}

    result = await plan_workflow(
        ctx, str(project.id), str(owner.id), COMMON_REQUEST, [], None, None
    )

    assert result["outcome"] == "proposed"
    assert result["workflow_id"] is not None
    assert result["version_id"] is not None


async def test_plan_workflow_job_maps_provider_error_to_failed(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner, project = await _project(db_session, make_user)
    router, _provider = build_mock_router(failures=[ScriptedFailure(kind="rate_limited", times=10)])
    session_factory = await _worker_session_factory(db_session)

    ctx = {"session_factory": session_factory, "model_router": router, "job_id": "test"}

    result = await plan_workflow(
        ctx, str(project.id), str(owner.id), COMMON_REQUEST, [], None, None
    )

    assert result["outcome"] == "failed"
    assert result["error_code"] == "PROVIDER_RATE_LIMITED"


async def test_plan_workflow_job_returns_failed_for_missing_project(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner = await make_user(db_session, f"user_{uuid.uuid4().hex}")
    router, _provider = build_mock_router(structured=[COMMON_PROPOSAL])
    session_factory = await _worker_session_factory(db_session)

    ctx = {"session_factory": session_factory, "model_router": router, "job_id": "test"}

    result = await plan_workflow(
        ctx, str(uuid.uuid4()), str(owner.id), COMMON_REQUEST, [], None, None
    )

    assert result["outcome"] == "failed"
    assert result["error_code"] == "NOT_FOUND"


@pytest.mark.usefixtures("db_session")
async def test_module_imports_are_valid() -> None:
    """Sanity check that the job function signature matches the arq registration."""
    assert callable(plan_workflow)
