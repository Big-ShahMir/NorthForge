"""Project CRUD routes. Every route requires an authenticated caller."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from northforge.api.deps import get_session
from northforge.api.envelope import Envelope, request_id_of
from northforge.auth.dependencies import get_current_user
from northforge.core.errors import NotFoundError
from northforge.db.models import User
from northforge.db.repositories.projects import ProjectsRepository
from northforge.schemas.projects import ProjectCreate, ProjectList, ProjectOut, ProjectUpdate

router = APIRouter(prefix="/api", tags=["projects"])

CurrentUser = Annotated[User, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/projects", response_model=Envelope[ProjectList])
async def list_projects(
    request: Request,
    user: CurrentUser,
    session: Session,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Envelope[ProjectList]:
    projects, total = await ProjectsRepository(session).list_for_owner(user.id, limit, offset)
    return Envelope[ProjectList](
        data=ProjectList(
            items=[ProjectOut.model_validate(project) for project in projects],
            total=total,
            limit=limit,
            offset=offset,
        ),
        request_id=request_id_of(request),
    )


@router.post("/projects", response_model=Envelope[ProjectOut], status_code=status.HTTP_201_CREATED)
async def create_project(
    request: Request, body: ProjectCreate, user: CurrentUser, session: Session
) -> Envelope[ProjectOut]:
    project = await ProjectsRepository(session).create(
        user.id, body.name, body.description, body.vertical
    )
    return Envelope[ProjectOut](
        data=ProjectOut.model_validate(project), request_id=request_id_of(request)
    )


@router.get("/projects/{project_id}", response_model=Envelope[ProjectOut])
async def get_project(
    request: Request, project_id: uuid.UUID, user: CurrentUser, session: Session
) -> Envelope[ProjectOut]:
    project = await ProjectsRepository(session).get_for_owner(project_id, user.id)
    if project is None:
        raise NotFoundError("Project not found.")
    return Envelope[ProjectOut](
        data=ProjectOut.model_validate(project), request_id=request_id_of(request)
    )


@router.patch("/projects/{project_id}", response_model=Envelope[ProjectOut])
async def update_project(
    request: Request,
    project_id: uuid.UUID,
    body: ProjectUpdate,
    user: CurrentUser,
    session: Session,
) -> Envelope[ProjectOut]:
    repo = ProjectsRepository(session)
    project = await repo.get_for_owner(project_id, user.id)
    if project is None:
        raise NotFoundError("Project not found.")
    project = await repo.update(project, name=body.name, description=body.description)
    return Envelope[ProjectOut](
        data=ProjectOut.model_validate(project), request_id=request_id_of(request)
    )
