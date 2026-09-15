"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("clerk_user_id", sa.String(length=191), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("display_name", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("clerk_user_id", name="uq_users_clerk_user_id"),
    )

    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "vertical", sa.String(length=64), server_default="contract_review", nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_projects_owner_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_projects"),
    )
    op.create_index("ix_projects_owner_id", "projects", ["owner_id"])

    op.create_table(
        "workflows",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_workflows_project_id_projects"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_workflows_created_by_users"),
        sa.PrimaryKeyConstraint("id", name="pk_workflows"),
    )
    op.create_index("ix_workflows_project_id", "workflows", ["project_id"])

    op.create_table(
        "workflow_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("definition_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="draft", nullable=False),
        sa.Column("source_request", sa.Text(), nullable=True),
        sa.Column(
            "validation_warnings_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "model_config_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('draft', 'validated', 'approved', 'archived')",
            name="ck_workflow_versions_status_valid_values",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"], ["workflows.id"], name="fk_workflow_versions_workflow_id_workflows"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_workflow_versions_created_by_users"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_versions"),
        sa.UniqueConstraint(
            "workflow_id", "version_number", name="uq_workflow_versions_workflow_id"
        ),
    )
    op.create_index("ix_workflow_versions_status", "workflow_versions", ["status"])

    # Deferred FK: workflows.current_version_id -> workflow_versions.id. Added
    # after both tables exist because the two tables reference each other.
    op.create_foreign_key(
        "fk_workflows_current_version_id_workflow_versions",
        "workflows",
        "workflow_versions",
        ["current_version_id"],
        ["id"],
    )

    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_version_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="queued", nullable=False),
        sa.Column(
            "config_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("summary_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_evaluation_runs_status_valid_values",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_evaluation_runs_project_id_projects"
        ),
        sa.ForeignKeyConstraint(
            ["workflow_version_id"],
            ["workflow_versions.id"],
            name="fk_evaluation_runs_workflow_version_id_workflow_versions",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_evaluation_runs"),
    )
    op.create_index("ix_evaluation_runs_project_id", "evaluation_runs", ["project_id"])

    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workflow_version_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="queued", nullable=False),
        sa.Column("input_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("checkpoint_ref", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'paused', 'completed', 'failed', 'cancelled')",
            name="ck_workflow_runs_status_valid_values",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_version_id"],
            ["workflow_versions.id"],
            name="fk_workflow_runs_workflow_version_id_workflow_versions",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_workflow_runs_project_id_projects"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_workflow_runs_created_by_users"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_runs"),
    )
    op.create_index(
        "uq_workflow_runs_version_idempotency_key",
        "workflow_runs",
        ["workflow_version_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "ix_workflow_runs_project_id_created_at", "workflow_runs", ["project_id", "created_at"]
    )
    op.create_index("ix_workflow_runs_status", "workflow_runs", ["status"])

    op.create_table(
        "evaluation_cases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("input_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("expected_output_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "required_evidence_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "allowed_tools_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "labels_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("source_run_id", sa.Uuid(), nullable=True),
        sa.Column("dataset_version", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_evaluation_cases_project_id_projects"
        ),
        sa.ForeignKeyConstraint(
            ["source_run_id"],
            ["workflow_runs.id"],
            name="fk_evaluation_cases_source_run_id_workflow_runs",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_evaluation_cases"),
    )
    op.create_index(
        "ix_evaluation_cases_project_id_dataset_version",
        "evaluation_cases",
        ["project_id", "dataset_version"],
    )

    op.create_table(
        "step_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("step_id", sa.String(length=100), nullable=False),
        sa.Column("attempt", sa.Integer(), server_default="1", nullable=False),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("input_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("output_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_step_runs_status_valid_values",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["workflow_runs.id"], name="fk_step_runs_run_id_workflow_runs"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_step_runs"),
        sa.UniqueConstraint("run_id", "step_id", "attempt", name="uq_step_runs_run_id"),
    )

    op.create_table(
        "evaluation_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("evaluation_run_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "metrics_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("output_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("trace_ref", sa.String(length=255), nullable=True),
        sa.Column("failure_category", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"],
            ["evaluation_runs.id"],
            name="fk_evaluation_results_evaluation_run_id_evaluation_runs",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["evaluation_cases.id"],
            name="fk_evaluation_results_case_id_evaluation_cases",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_evaluation_results"),
    )
    op.create_index(
        "ix_evaluation_results_evaluation_run_id", "evaluation_results", ["evaluation_run_id"]
    )

    op.create_table(
        "feedback_labels",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=32), nullable=False),
        sa.Column("failure_category", sa.String(length=64), nullable=True),
        sa.Column("notes", sa.Text(), server_default="", nullable=False),
        sa.Column("corrected_output_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("promoted_evaluation_case_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "label IN ('successful', 'incorrect', 'unsupported', 'unsafe', "
            "'incomplete', 'ambiguous')",
            name="ck_feedback_labels_label_valid_values",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["workflow_runs.id"], name="fk_feedback_labels_run_id_workflow_runs"
        ),
        sa.ForeignKeyConstraint(
            ["promoted_evaluation_case_id"],
            ["evaluation_cases.id"],
            name="fk_feedback_labels_promoted_evaluation_case_id_evaluation_cases",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_feedback_labels_created_by_users"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_feedback_labels"),
    )
    op.create_index("ix_feedback_labels_run_id", "feedback_labels", ["run_id"])

    op.create_table(
        "trace_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("step_run_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["workflow_runs.id"], name="fk_trace_events_run_id_workflow_runs"
        ),
        sa.ForeignKeyConstraint(
            ["step_run_id"], ["step_runs.id"], name="fk_trace_events_step_run_id_step_runs"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_trace_events"),
        sa.UniqueConstraint("run_id", "sequence_number", name="uq_trace_events_run_id"),
    )


def downgrade() -> None:
    op.drop_table("trace_events")
    op.drop_index("ix_feedback_labels_run_id", table_name="feedback_labels")
    op.drop_table("feedback_labels")
    op.drop_index("ix_evaluation_results_evaluation_run_id", table_name="evaluation_results")
    op.drop_table("evaluation_results")
    op.drop_table("step_runs")
    op.drop_index("ix_evaluation_cases_project_id_dataset_version", table_name="evaluation_cases")
    op.drop_table("evaluation_cases")
    op.drop_index("ix_workflow_runs_status", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_project_id_created_at", table_name="workflow_runs")
    op.drop_index("uq_workflow_runs_version_idempotency_key", table_name="workflow_runs")
    op.drop_table("workflow_runs")
    op.drop_index("ix_evaluation_runs_project_id", table_name="evaluation_runs")
    op.drop_table("evaluation_runs")
    op.drop_constraint(
        "fk_workflows_current_version_id_workflow_versions", "workflows", type_="foreignkey"
    )
    op.drop_index("ix_workflow_versions_status", table_name="workflow_versions")
    op.drop_table("workflow_versions")
    op.drop_index("ix_workflows_project_id", table_name="workflows")
    op.drop_table("workflows")
    op.drop_index("ix_projects_owner_id", table_name="projects")
    op.drop_table("projects")
    op.drop_table("users")
