"""Repository tests for ``ProjectsRepository``, run against a migrated database."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from northforge.db.models import User
from northforge.db.repositories.projects import ProjectsRepository

MakeUser = Callable[..., Awaitable[User]]


async def test_create_persists_project_with_given_fields(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner = await make_user(db_session, "user_a")
    repo = ProjectsRepository(db_session)

    project = await repo.create(owner.id, "Contract review", "desc", "contract_review")

    assert project.id is not None
    assert project.owner_id == owner.id
    assert project.name == "Contract review"
    assert project.description == "desc"
    assert project.vertical == "contract_review"
    assert project.archived_at is None


async def test_get_for_owner_returns_none_for_missing_project(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner = await make_user(db_session, "user_a")

    result = await ProjectsRepository(db_session).get_for_owner(uuid.uuid4(), owner.id)

    assert result is None


async def test_get_for_owner_returns_none_for_other_owner(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner_a = await make_user(db_session, "user_a")
    owner_b = await make_user(db_session, "user_b")
    repo = ProjectsRepository(db_session)
    project = await repo.create(owner_a.id, "Alice's project", "", "contract_review")

    result = await repo.get_for_owner(project.id, owner_b.id)

    assert result is None


async def test_get_for_owner_returns_project_for_correct_owner(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner = await make_user(db_session, "user_a")
    repo = ProjectsRepository(db_session)
    project = await repo.create(owner.id, "Contract review", "", "contract_review")

    result = await repo.get_for_owner(project.id, owner.id)

    assert result is not None
    assert result.id == project.id


async def test_list_for_owner_orders_newest_first_and_excludes_archived(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    # Postgres' ``now()`` (the ``created_at`` server default) returns the
    # transaction start time, so rows created back-to-back in this shared
    # test transaction would otherwise tie; set explicit, distinct
    # timestamps to make "newest first" observable.
    owner = await make_user(db_session, "user_a")
    repo = ProjectsRepository(db_session)
    base = datetime.now(UTC)
    first = await repo.create(owner.id, "First", "", "contract_review")
    first.created_at = base
    second = await repo.create(owner.id, "Second", "", "contract_review")
    second.created_at = base + timedelta(seconds=1)
    archived = await repo.create(owner.id, "Archived", "", "contract_review")
    archived.created_at = base + timedelta(seconds=2)
    archived.archived_at = datetime.now(UTC)
    await db_session.flush()

    projects, total = await repo.list_for_owner(owner.id, limit=20, offset=0)

    assert total == 2
    assert [p.id for p in projects] == [second.id, first.id]


async def test_list_for_owner_respects_limit_and_offset(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner = await make_user(db_session, "user_a")
    repo = ProjectsRepository(db_session)
    for index in range(3):
        await repo.create(owner.id, f"Project {index}", "", "contract_review")

    page, total = await repo.list_for_owner(owner.id, limit=1, offset=1)

    assert total == 3
    assert len(page) == 1


async def test_list_for_owner_only_returns_own_projects(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner_a = await make_user(db_session, "user_a")
    owner_b = await make_user(db_session, "user_b")
    repo = ProjectsRepository(db_session)
    await repo.create(owner_a.id, "Alice's project", "", "contract_review")
    await repo.create(owner_b.id, "Bob's project", "", "contract_review")

    projects, total = await repo.list_for_owner(owner_b.id, limit=20, offset=0)

    assert total == 1
    assert projects[0].owner_id == owner_b.id


async def test_update_changes_only_provided_fields(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner = await make_user(db_session, "user_a")
    repo = ProjectsRepository(db_session)
    project = await repo.create(owner.id, "Name", "Description", "contract_review")

    updated = await repo.update(project, name="New name")

    assert updated.name == "New name"
    assert updated.description == "Description"


async def test_update_with_no_fields_leaves_project_unchanged(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    owner = await make_user(db_session, "user_a")
    repo = ProjectsRepository(db_session)
    project = await repo.create(owner.id, "Name", "Description", "contract_review")

    updated = await repo.update(project)

    assert updated.name == "Name"
    assert updated.description == "Description"
