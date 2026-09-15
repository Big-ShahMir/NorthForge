"""Workflow runs, step runs, and trace events repository."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from northforge.core.errors import ConflictError
from northforge.db.models import (
    Project,
    RunStatus,
    StepRun,
    StepRunStatus,
    TraceEvent,
    WorkflowRun,
    WorkflowVersion,
    WorkflowVersionStatus,
)

# Allowed run-status transitions. Missing source statuses (completed, failed,
# cancelled) are terminal and permit no further transition.
_RUN_TRANSITIONS: dict[str, set[str]] = {
    RunStatus.QUEUED.value: {RunStatus.RUNNING.value, RunStatus.CANCELLED.value},
    RunStatus.RUNNING.value: {
        RunStatus.PAUSED.value,
        RunStatus.COMPLETED.value,
        RunStatus.FAILED.value,
        RunStatus.CANCELLED.value,
    },
    RunStatus.PAUSED.value: {RunStatus.RUNNING.value, RunStatus.CANCELLED.value},
}

_TERMINAL_STATUSES = {
    RunStatus.COMPLETED.value,
    RunStatus.FAILED.value,
    RunStatus.CANCELLED.value,
}


class RunsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        version: WorkflowVersion,
        project: Project,
        input_json: dict[str, Any],
        created_by: uuid.UUID,
        idempotency_key: str | None = None,
    ) -> tuple[WorkflowRun, bool]:
        """Create a run, or return the existing one for a repeated idempotency key.

        Returns ``(run, created)``: ``created`` is ``False`` when a run with
        the same ``idempotency_key`` already exists for this version.
        """
        if version.status != WorkflowVersionStatus.APPROVED.value:
            raise ConflictError(
                "Workflow version must be approved before it can be run.",
                code="VERSION_NOT_APPROVED",
            )

        if idempotency_key is not None:
            existing_stmt = select(WorkflowRun).where(
                WorkflowRun.workflow_version_id == version.id,
                WorkflowRun.idempotency_key == idempotency_key,
            )
            existing = (await self._session.execute(existing_stmt)).scalar_one_or_none()
            if existing is not None:
                return existing, False

        run = WorkflowRun(
            workflow_version_id=version.id,
            project_id=project.id,
            status=RunStatus.QUEUED.value,
            input_json=input_json,
            created_by=created_by,
            idempotency_key=idempotency_key,
        )
        self._session.add(run)
        await self._session.flush()
        return run, True

    async def get_for_owner(self, run_id: uuid.UUID, owner_id: uuid.UUID) -> WorkflowRun | None:
        stmt = (
            select(WorkflowRun)
            .join(Project, WorkflowRun.project_id == Project.id)
            .where(WorkflowRun.id == run_id, Project.owner_id == owner_id)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def transition(
        self,
        run: WorkflowRun,
        new_status: str,
        *,
        error_json: dict[str, Any] | None = None,
        result_json: dict[str, Any] | None = None,
        checkpoint_ref: str | None = None,
    ) -> WorkflowRun:
        allowed = _RUN_TRANSITIONS.get(run.status, set())
        if new_status not in allowed:
            raise ConflictError(
                f"Cannot transition run from '{run.status}' to '{new_status}'.",
                code="INVALID_RUN_TRANSITION",
            )

        run.status = new_status
        if new_status == RunStatus.RUNNING.value and run.started_at is None:
            run.started_at = datetime.now(UTC)
        if new_status in _TERMINAL_STATUSES:
            run.completed_at = datetime.now(UTC)
        if error_json is not None:
            run.error_json = error_json
        if result_json is not None:
            run.result_json = result_json
        if checkpoint_ref is not None:
            run.checkpoint_ref = checkpoint_ref
        await self._session.flush()
        return run

    async def create_step_run(
        self,
        run: WorkflowRun,
        step_id: str,
        attempt: int = 1,
        input_json: dict[str, Any] | None = None,
    ) -> StepRun:
        step_run = StepRun(
            run_id=run.id,
            step_id=step_id,
            attempt=attempt,
            status=StepRunStatus.PENDING.value,
            input_json=input_json,
        )
        self._session.add(step_run)
        await self._session.flush()
        return step_run

    async def finish_step_run(
        self,
        step_run: StepRun,
        status: str,
        output_json: dict[str, Any] | None = None,
        error_json: dict[str, Any] | None = None,
    ) -> StepRun:
        step_run.status = status
        step_run.completed_at = datetime.now(UTC)
        if output_json is not None:
            step_run.output_json = output_json
        if error_json is not None:
            step_run.error_json = error_json
        await self._session.flush()
        return step_run

    async def append_event(
        self,
        run: WorkflowRun,
        event_type: str,
        payload_json: dict[str, Any],
        step_run: StepRun | None = None,
    ) -> TraceEvent:
        """Append a trace event with the next sequence number for ``run``.

        Locks the run row (``SELECT ... FOR UPDATE``) for the remainder of
        the transaction so concurrent appends for the same run serialize
        rather than racing on the same sequence number.
        """
        await self._session.execute(
            select(WorkflowRun.id).where(WorkflowRun.id == run.id).with_for_update()
        )

        max_stmt = select(func.max(TraceEvent.sequence_number)).where(TraceEvent.run_id == run.id)
        current_max = (await self._session.execute(max_stmt)).scalar_one()
        next_sequence = (current_max or 0) + 1

        event = TraceEvent(
            run_id=run.id,
            step_run_id=step_run.id if step_run is not None else None,
            event_type=event_type,
            payload_json=payload_json,
            sequence_number=next_sequence,
        )
        self._session.add(event)
        await self._session.flush()
        return event

    async def list_events(
        self, run_id: uuid.UUID, after_sequence: int = 0, limit: int = 500
    ) -> list[TraceEvent]:
        stmt = (
            select(TraceEvent)
            .where(TraceEvent.run_id == run_id, TraceEvent.sequence_number > after_sequence)
            .order_by(TraceEvent.sequence_number)
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars().all())
