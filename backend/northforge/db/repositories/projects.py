"""Projects repository."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from northforge.db.models import Project


class ProjectsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, owner_id: uuid.UUID, name: str, description: str, vertical: str
    ) -> Project:
        project = Project(owner_id=owner_id, name=name, description=description, vertical=vertical)
        self._session.add(project)
        await self._session.flush()
        return project

    async def list_for_owner(
        self, owner_id: uuid.UUID, limit: int, offset: int
    ) -> tuple[list[Project], int]:
        """Projects owned by ``owner_id``, excluding archived ones, newest first."""
        base = select(Project).where(Project.owner_id == owner_id, Project.archived_at.is_(None))

        total = (
            await self._session.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()

        rows_stmt = base.order_by(Project.created_at.desc()).limit(limit).offset(offset)
        rows = (await self._session.execute(rows_stmt)).scalars().all()
        return list(rows), total

    async def get_for_owner(self, project_id: uuid.UUID, owner_id: uuid.UUID) -> Project | None:
        stmt = select(Project).where(Project.id == project_id, Project.owner_id == owner_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def update(
        self, project: Project, *, name: str | None = None, description: str | None = None
    ) -> Project:
        changed = False
        if name is not None:
            project.name = name
            changed = True
        if description is not None:
            project.description = description
            changed = True
        await self._session.flush()
        if changed:
            # ``updated_at`` has a server-side ``onupdate``: the flush above
            # expires it (its new value is only known to Postgres), and a
            # synchronous read of an expired attribute would trigger a lazy
            # load, which is forbidden in async code. Refresh it explicitly.
            await self._session.refresh(project, attribute_names=["updated_at"])
        return project
