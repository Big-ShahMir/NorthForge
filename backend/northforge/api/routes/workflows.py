"""Workflow and workflow-version routes. Every route requires an authenticated caller."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from northforge.api.deps import get_session
from northforge.api.envelope import Envelope, request_id_of
from northforge.auth.dependencies import get_current_user
from northforge.core.errors import NotFoundError
from northforge.db.models import User, Workflow, WorkflowVersion
from northforge.db.repositories.projects import ProjectsRepository
from northforge.db.repositories.workflows import WorkflowsRepository
from northforge.schemas.workflow import validate_definition
from northforge.schemas.workflows import (
    VersionCreate,
    VersionOut,
    VersionSummary,
    VersionUpdate,
    WorkflowCreate,
    WorkflowDetail,
    WorkflowList,
    WorkflowSummary,
)

router = APIRouter(prefix="/api", tags=["workflows"])

CurrentUser = Annotated[User, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(get_session)]


def _version_out(version: WorkflowVersion) -> VersionOut:
    return VersionOut.model_validate(version)


def _workflow_summary(workflow: Workflow) -> WorkflowSummary:
    current = workflow.current_version
    return WorkflowSummary(
        id=workflow.id,
        project_id=workflow.project_id,
        name=workflow.name,
        description=workflow.description,
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
        current_version_number=current.version_number if current else None,
        current_version_status=current.status if current else None,
    )


def _workflow_detail(workflow: Workflow) -> WorkflowDetail:
    return WorkflowDetail(
        id=workflow.id,
        project_id=workflow.project_id,
        name=workflow.name,
        description=workflow.description,
        current_version_id=workflow.current_version_id,
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
        versions=[VersionSummary.model_validate(version) for version in workflow.versions],
    )


@router.get("/projects/{project_id}/workflows", response_model=Envelope[WorkflowList])
async def list_workflows(
    request: Request,
    project_id: uuid.UUID,
    user: CurrentUser,
    session: Session,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Envelope[WorkflowList]:
    project = await ProjectsRepository(session).get_for_owner(project_id, user.id)
    if project is None:
        raise NotFoundError("Project not found.")
    workflows, total = await WorkflowsRepository(session).list_for_project(
        project.id, limit, offset
    )
    items = [_workflow_summary(workflow) for workflow in workflows]
    return Envelope[WorkflowList](
        data=WorkflowList(items=items, total=total, limit=limit, offset=offset),
        request_id=request_id_of(request),
    )


@router.post(
    "/projects/{project_id}/workflows",
    response_model=Envelope[WorkflowDetail],
    status_code=status.HTTP_201_CREATED,
)
async def create_workflow(
    request: Request,
    project_id: uuid.UUID,
    body: WorkflowCreate,
    user: CurrentUser,
    session: Session,
) -> Envelope[WorkflowDetail]:
    project = await ProjectsRepository(session).get_for_owner(project_id, user.id)
    if project is None:
        raise NotFoundError("Project not found.")
    definition, _warnings = validate_definition(body.definition)
    workflow = await WorkflowsRepository(session).create(
        project, body.name, body.description, definition, user.id
    )
    return Envelope[WorkflowDetail](
        data=_workflow_detail(workflow), request_id=request_id_of(request)
    )


@router.get("/workflows/{workflow_id}", response_model=Envelope[WorkflowDetail])
async def get_workflow(
    request: Request, workflow_id: uuid.UUID, user: CurrentUser, session: Session
) -> Envelope[WorkflowDetail]:
    workflow = await WorkflowsRepository(session).get_for_owner(workflow_id, user.id)
    if workflow is None:
        raise NotFoundError("Workflow not found.")
    return Envelope[WorkflowDetail](
        data=_workflow_detail(workflow), request_id=request_id_of(request)
    )


@router.post(
    "/workflows/{workflow_id}/versions",
    response_model=Envelope[VersionOut],
    status_code=status.HTTP_201_CREATED,
)
async def create_version(
    request: Request,
    workflow_id: uuid.UUID,
    body: VersionCreate,
    user: CurrentUser,
    session: Session,
) -> Envelope[VersionOut]:
    repo = WorkflowsRepository(session)
    workflow = await repo.get_for_owner(workflow_id, user.id)
    if workflow is None:
        raise NotFoundError("Workflow not found.")
    definition, _warnings = validate_definition(body.definition)
    version = await repo.create_version(workflow, definition, user.id, body.source_request)
    return Envelope[VersionOut](data=_version_out(version), request_id=request_id_of(request))


@router.get("/workflow-versions/{version_id}", response_model=Envelope[VersionOut])
async def get_version(
    request: Request, version_id: uuid.UUID, user: CurrentUser, session: Session
) -> Envelope[VersionOut]:
    version = await WorkflowsRepository(session).get_version_for_owner(version_id, user.id)
    if version is None:
        raise NotFoundError("Workflow version not found.")
    return Envelope[VersionOut](data=_version_out(version), request_id=request_id_of(request))


@router.patch("/workflow-versions/{version_id}", response_model=Envelope[VersionOut])
async def update_version(
    request: Request,
    version_id: uuid.UUID,
    body: VersionUpdate,
    user: CurrentUser,
    session: Session,
) -> Envelope[VersionOut]:
    repo = WorkflowsRepository(session)
    version = await repo.get_version_for_owner(version_id, user.id)
    if version is None:
        raise NotFoundError("Workflow version not found.")
    definition, _warnings = validate_definition(body.definition)
    version = await repo.update_definition(version, definition)
    return Envelope[VersionOut](data=_version_out(version), request_id=request_id_of(request))


@router.post("/workflow-versions/{version_id}/validate", response_model=Envelope[VersionOut])
async def validate_version(
    request: Request, version_id: uuid.UUID, user: CurrentUser, session: Session
) -> Envelope[VersionOut]:
    repo = WorkflowsRepository(session)
    version = await repo.get_version_for_owner(version_id, user.id)
    if version is None:
        raise NotFoundError("Workflow version not found.")
    known_tools = request.app.state.tool_registry.names()
    version = await repo.validate(version, known_tools=known_tools)
    return Envelope[VersionOut](data=_version_out(version), request_id=request_id_of(request))


@router.post("/workflow-versions/{version_id}/approve", response_model=Envelope[VersionOut])
async def approve_version(
    request: Request, version_id: uuid.UUID, user: CurrentUser, session: Session
) -> Envelope[VersionOut]:
    repo = WorkflowsRepository(session)
    version = await repo.get_version_for_owner(version_id, user.id)
    if version is None:
        raise NotFoundError("Workflow version not found.")
    known_tools = request.app.state.tool_registry.names()
    version = await repo.approve(version, known_tools=known_tools)
    return Envelope[VersionOut](data=_version_out(version), request_id=request_id_of(request))


@router.post(
    "/workflow-versions/{version_id}/restore",
    response_model=Envelope[VersionOut],
    status_code=status.HTTP_201_CREATED,
)
async def restore_version(
    request: Request, version_id: uuid.UUID, user: CurrentUser, session: Session
) -> Envelope[VersionOut]:
    repo = WorkflowsRepository(session)
    source_version = await repo.get_version_for_owner(version_id, user.id)
    if source_version is None:
        raise NotFoundError("Workflow version not found.")
    workflow = await repo.get_for_owner(source_version.workflow_id, user.id)
    if workflow is None:
        raise NotFoundError("Workflow not found.")
    new_version = await repo.restore(workflow, source_version, user.id)
    return Envelope[VersionOut](data=_version_out(new_version), request_id=request_id_of(request))
