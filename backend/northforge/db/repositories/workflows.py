"""Workflows and workflow-versions repository."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import set_committed_value

from northforge.core.errors import ConflictError, InvalidWorkflowError
from northforge.db.models import Project, Workflow, WorkflowVersion, WorkflowVersionStatus
from northforge.schemas.workflow import WorkflowDefinition, parse_definition
from northforge.schemas.workflow_validation import ValidationReport, validate_workflow

_MUTABLE_STATUSES = {WorkflowVersionStatus.DRAFT.value, WorkflowVersionStatus.VALIDATED.value}


class WorkflowsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        project: Project,
        name: str,
        description: str,
        definition: WorkflowDefinition,
        created_by: uuid.UUID,
        *,
        source_request: str | None = None,
        model_config_json: dict[str, Any] | None = None,
        planner_output_json: dict[str, Any] | None = None,
        validation_warnings: list[str] | None = None,
    ) -> Workflow:
        workflow = Workflow(
            project_id=project.id, name=name, description=description, created_by=created_by
        )
        self._session.add(workflow)
        await self._session.flush()

        version_kwargs: dict[str, Any] = {}
        if source_request is not None:
            version_kwargs["source_request"] = source_request
        if model_config_json is not None:
            version_kwargs["model_config_json"] = model_config_json
        if planner_output_json is not None:
            version_kwargs["planner_output_json"] = planner_output_json
        if validation_warnings is not None:
            version_kwargs["validation_warnings_json"] = validation_warnings

        version = WorkflowVersion(
            workflow_id=workflow.id,
            version_number=1,
            definition_json=definition.model_dump(mode="json"),
            status=WorkflowVersionStatus.DRAFT.value,
            created_by=created_by,
            **version_kwargs,
        )
        self._session.add(version)
        await self._session.flush()

        # ``workflow`` just became persistent above; mutating or reading its
        # ``versions``/``current_version`` relationship attributes here would
        # trigger a lazy load (forbidden in async code). Both are already
        # fully known, so set them directly instead of querying for them.
        workflow.current_version_id = version.id
        await self._session.flush()
        # ``updated_at`` has a server-side ``onupdate``: the flush above
        # (an UPDATE for ``current_version_id``) expires it, and a
        # synchronous read of an expired attribute would trigger a lazy
        # load, which is forbidden in async code. Refresh it explicitly.
        await self._session.refresh(workflow, attribute_names=["updated_at"])
        set_committed_value(workflow, "versions", [version])
        set_committed_value(workflow, "current_version", version)
        return workflow

    async def list_for_project(
        self, project_id: uuid.UUID, limit: int = 20, offset: int = 0
    ) -> tuple[list[Workflow], int]:
        """Newest first, with the current version loaded; returns (items, total)."""
        base = select(Workflow).where(Workflow.project_id == project_id)
        total = (
            await self._session.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
        stmt = (
            base.order_by(Workflow.created_at.desc(), Workflow.id.desc())
            .options(selectinload(Workflow.current_version))
            .limit(limit)
            .offset(offset)
        )
        return list((await self._session.execute(stmt)).scalars().all()), int(total)

    async def get_for_owner(self, workflow_id: uuid.UUID, owner_id: uuid.UUID) -> Workflow | None:
        stmt = (
            select(Workflow)
            .join(Project, Workflow.project_id == Project.id)
            .where(Workflow.id == workflow_id, Project.owner_id == owner_id)
            .options(selectinload(Workflow.versions))
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_version_for_owner(
        self, version_id: uuid.UUID, owner_id: uuid.UUID
    ) -> WorkflowVersion | None:
        stmt = (
            select(WorkflowVersion)
            .join(Workflow, WorkflowVersion.workflow_id == Workflow.id)
            .join(Project, Workflow.project_id == Project.id)
            .where(WorkflowVersion.id == version_id, Project.owner_id == owner_id)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def create_version(
        self,
        workflow: Workflow,
        definition: WorkflowDefinition,
        created_by: uuid.UUID,
        source_request: str | None = None,
        *,
        model_config_json: dict[str, Any] | None = None,
        planner_output_json: dict[str, Any] | None = None,
        validation_warnings: list[str] | None = None,
    ) -> WorkflowVersion:
        """A new draft version. ``workflow.current_version_id`` is left unchanged."""
        next_number = await self._next_version_number(workflow.id)
        version_kwargs: dict[str, Any] = {}
        if model_config_json is not None:
            version_kwargs["model_config_json"] = model_config_json
        if planner_output_json is not None:
            version_kwargs["planner_output_json"] = planner_output_json
        if validation_warnings is not None:
            version_kwargs["validation_warnings_json"] = validation_warnings

        version = WorkflowVersion(
            workflow_id=workflow.id,
            version_number=next_number,
            definition_json=definition.model_dump(mode="json"),
            status=WorkflowVersionStatus.DRAFT.value,
            source_request=source_request,
            created_by=created_by,
            **version_kwargs,
        )
        self._session.add(version)
        await self._session.flush()
        return version

    async def latest_version(self, workflow_id: uuid.UUID) -> WorkflowVersion | None:
        """The version with the highest ``version_number`` for ``workflow_id``, if any."""
        stmt = (
            select(WorkflowVersion)
            .where(WorkflowVersion.workflow_id == workflow_id)
            .order_by(WorkflowVersion.version_number.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def update_definition(
        self, version: WorkflowVersion, definition: WorkflowDefinition
    ) -> WorkflowVersion:
        """Overwrite the definition, resetting the version to draft.

        Only allowed while the version is ``draft`` or ``validated``; an
        approved or archived version is immutable.
        """
        self._require_mutable(version)
        version.definition_json = definition.model_dump(mode="json")
        version.status = WorkflowVersionStatus.DRAFT.value
        version.validation_warnings_json = []
        await self._session.flush()
        return version

    async def validate(
        self, version: WorkflowVersion, *, known_tools: frozenset[str] | set[str]
    ) -> WorkflowVersion:
        self._require_mutable(version)
        report = self._run_semantic_validation(version, known_tools=known_tools)
        version.validation_warnings_json = [
            f"{problem.code}: {problem.message}" for problem in report.warnings
        ]
        version.status = WorkflowVersionStatus.VALIDATED.value
        await self._session.flush()
        return version

    async def approve(
        self, version: WorkflowVersion, *, known_tools: frozenset[str] | set[str]
    ) -> WorkflowVersion:
        self._run_semantic_validation(version, known_tools=known_tools)
        if version.status != WorkflowVersionStatus.VALIDATED.value:
            raise ConflictError(
                "Workflow version must be validated before it can be approved.",
                code="VERSION_NOT_VALIDATED",
            )
        version.status = WorkflowVersionStatus.APPROVED.value
        version.approved_at = datetime.now(UTC)
        await self._session.execute(
            update(Workflow)
            .where(Workflow.id == version.workflow_id)
            .values(current_version_id=version.id)
        )
        await self._session.flush()
        return version

    @staticmethod
    def _run_semantic_validation(
        version: WorkflowVersion, *, known_tools: frozenset[str] | set[str]
    ) -> ValidationReport:
        """Run semantic validation, raising ``InvalidWorkflowError`` (422) on error.

        Used by both ``validate`` and ``approve`` -- ``approve`` re-runs this
        because the tool registry may have changed since the version was
        last validated. Returns the full report (including warnings) on
        success so ``validate`` can store them without validating twice.
        """
        definition = parse_definition(version.definition_json)
        report = validate_workflow(definition, known_tools=known_tools)
        if not report.ok:
            raise InvalidWorkflowError(
                f"Workflow definition has {len(report.errors)} semantic error(s).",
                details=[asdict(problem) for problem in report.errors],
            )
        return report

    async def restore(
        self, workflow: Workflow, source_version: WorkflowVersion, created_by: uuid.UUID
    ) -> WorkflowVersion:
        """A new draft version copying ``source_version``'s definition verbatim."""
        next_number = await self._next_version_number(workflow.id)
        version = WorkflowVersion(
            workflow_id=workflow.id,
            version_number=next_number,
            definition_json=source_version.definition_json,
            status=WorkflowVersionStatus.DRAFT.value,
            source_request=f"Restored from version {source_version.version_number}",
            created_by=created_by,
        )
        self._session.add(version)
        await self._session.flush()
        return version

    async def _next_version_number(self, workflow_id: uuid.UUID) -> int:
        stmt = select(func.max(WorkflowVersion.version_number)).where(
            WorkflowVersion.workflow_id == workflow_id
        )
        current_max = (await self._session.execute(stmt)).scalar_one()
        return (current_max or 0) + 1

    @staticmethod
    def _require_mutable(version: WorkflowVersion) -> None:
        if version.status not in _MUTABLE_STATUSES:
            raise ConflictError(
                "Workflow version is immutable once approved or archived.",
                code="VERSION_IMMUTABLE",
            )
