"""ORM entities for the NorthForge Phase 1 domain model.

Every table follows the conventions in ``docs/DATABASE_SPEC.md``: UUID
primary keys, timezone-aware timestamps, JSONB for flexible or versioned
payloads, and status columns stored as ``String`` with a ``CheckConstraint``
enumerating the allowed values (the Python-side allowed values are the
``StrEnum``s below). No native PostgreSQL enum types are used so that adding
a status value only requires a migration that adjusts the check constraint.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from northforge.db.base import Base, created_at_column, updated_at_column, uuid_pk


class WorkflowVersionStatus(StrEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    APPROVED = "approved"
    ARCHIVED = "archived"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class FeedbackLabelValue(StrEnum):
    SUCCESSFUL = "successful"
    INCORRECT = "incorrect"
    UNSUPPORTED = "unsupported"
    UNSAFE = "unsafe"
    INCOMPLETE = "incomplete"
    AMBIGUOUS = "ambiguous"


class EvaluationRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    clerk_user_id: Mapped[str] = mapped_column(String(191), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    display_name: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    __table_args__ = (UniqueConstraint("clerk_user_id"),)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    vertical: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="contract_review"
    )
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    owner: Mapped[User] = relationship()
    workflows: Mapped[list[Workflow]] = relationship(back_populates="project")

    __table_args__ = (Index("ix_projects_owner_id", "owner_id"),)


class Workflow(Base):
    __tablename__ = "workflows"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "workflow_versions.id",
            use_alter=True,
            name="fk_workflows_current_version_id_workflow_versions",
        )
    )
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    project: Mapped[Project] = relationship(back_populates="workflows")
    creator: Mapped[User] = relationship(foreign_keys=[created_by])
    current_version: Mapped[WorkflowVersion | None] = relationship(
        foreign_keys=[current_version_id],
        post_update=True,
    )
    versions: Mapped[list[WorkflowVersion]] = relationship(
        back_populates="workflow",
        foreign_keys="WorkflowVersion.workflow_id",
        order_by="WorkflowVersion.version_number",
    )

    __table_args__ = (Index("ix_workflows_project_id", "project_id"),)


class WorkflowVersion(Base):
    __tablename__ = "workflow_versions"

    id: Mapped[uuid.UUID] = uuid_pk()
    workflow_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflows.id"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    definition_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=WorkflowVersionStatus.DRAFT.value
    )
    source_request: Mapped[str | None] = mapped_column(Text)
    validation_warnings_json: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    model_config_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = created_at_column()
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    workflow: Mapped[Workflow] = relationship(back_populates="versions", foreign_keys=[workflow_id])
    creator: Mapped[User] = relationship()

    __table_args__ = (
        UniqueConstraint("workflow_id", "version_number"),
        Index("ix_workflow_versions_status", "status"),
        CheckConstraint(
            "status IN ('draft', 'validated', 'approved', 'archived')",
            name="status_valid_values",
        ),
    )


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    workflow_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_versions.id"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=RunStatus.QUEUED.value
    )
    input_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    checkpoint_ref: Mapped[str | None] = mapped_column(String(255))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = created_at_column()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    workflow_version: Mapped[WorkflowVersion] = relationship()
    project: Mapped[Project] = relationship()
    step_runs: Mapped[list[StepRun]] = relationship(back_populates="run")
    trace_events: Mapped[list[TraceEvent]] = relationship(
        back_populates="run", foreign_keys="TraceEvent.run_id"
    )

    __table_args__ = (
        Index(
            "uq_workflow_runs_version_idempotency_key",
            "workflow_version_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index("ix_workflow_runs_project_id_created_at", "project_id", "created_at"),
        Index("ix_workflow_runs_status", "status"),
        CheckConstraint(
            "status IN ('queued', 'running', 'paused', 'completed', 'failed', 'cancelled')",
            name="status_valid_values",
        ),
    )


class StepRun(Base):
    __tablename__ = "step_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow_runs.id"), nullable=False)
    step_id: Mapped[str] = mapped_column(String(100), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=StepRunStatus.PENDING.value
    )
    input_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    run: Mapped[WorkflowRun] = relationship(back_populates="step_runs")

    __table_args__ = (
        UniqueConstraint("run_id", "step_id", "attempt"),
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'cancelled')",
            name="status_valid_values",
        ),
    )


class TraceEvent(Base):
    __tablename__ = "trace_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow_runs.id"), nullable=False)
    step_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("step_runs.id"))
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = created_at_column()

    run: Mapped[WorkflowRun] = relationship(back_populates="trace_events", foreign_keys=[run_id])
    step_run: Mapped[StepRun | None] = relationship()

    __table_args__ = (UniqueConstraint("run_id", "sequence_number"),)


class FeedbackLabel(Base):
    __tablename__ = "feedback_labels"

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow_runs.id"), nullable=False)
    label: Mapped[str] = mapped_column(String(32), nullable=False)
    failure_category: Mapped[str | None] = mapped_column(String(64))
    notes: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    corrected_output_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    promoted_evaluation_case_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("evaluation_cases.id")
    )
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = created_at_column()

    run: Mapped[WorkflowRun] = relationship()

    __table_args__ = (
        Index("ix_feedback_labels_run_id", "run_id"),
        CheckConstraint(
            "label IN ('successful', 'incorrect', 'unsupported', 'unsafe', "
            "'incomplete', 'ambiguous')",
            name="label_valid_values",
        ),
    )


class EvaluationCase(Base):
    __tablename__ = "evaluation_cases"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    input_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    expected_output_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    required_evidence_json: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    allowed_tools_json: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    labels_json: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow_runs.id"))
    dataset_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = created_at_column()

    project: Mapped[Project] = relationship()

    __table_args__ = (
        Index("ix_evaluation_cases_project_id_dataset_version", "project_id", "dataset_version"),
    )


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    workflow_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_versions.id"), nullable=False
    )
    dataset_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=EvaluationRunStatus.QUEUED.value
    )
    config_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_column()

    project: Mapped[Project] = relationship()
    workflow_version: Mapped[WorkflowVersion] = relationship()

    __table_args__ = (
        Index("ix_evaluation_runs_project_id", "project_id"),
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="status_valid_values",
        ),
    )


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"

    id: Mapped[uuid.UUID] = uuid_pk()
    evaluation_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_runs.id"), nullable=False
    )
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evaluation_cases.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    trace_ref: Mapped[str | None] = mapped_column(String(255))
    failure_category: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = created_at_column()

    evaluation_run: Mapped[EvaluationRun] = relationship()
    case: Mapped[EvaluationCase] = relationship()

    __table_args__ = (Index("ix_evaluation_results_evaluation_run_id", "evaluation_run_id"),)


__all__ = [
    "Base",
    "EvaluationCase",
    "EvaluationResult",
    "EvaluationRun",
    "EvaluationRunStatus",
    "FeedbackLabel",
    "FeedbackLabelValue",
    "Project",
    "RunStatus",
    "StepRun",
    "StepRunStatus",
    "TraceEvent",
    "User",
    "Workflow",
    "WorkflowRun",
    "WorkflowVersion",
    "WorkflowVersionStatus",
]
