"""documents and retrieval

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "access_groups_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[\"procurement\"]'::jsonb"),
            nullable=False,
        ),
    )

    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("document_type", sa.String(length=64), nullable=False),
        sa.Column("vendor", sa.String(length=200), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("expires_at", sa.Date(), nullable=True),
        sa.Column("access_group", sa.String(length=64), nullable=False),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=True),
        sa.Column("dataset_version", sa.String(length=64), nullable=False),
        sa.Column("chunk_count", sa.Integer(), server_default="0", nullable=False),
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
            ["project_id"], ["projects.id"], name="fk_documents_project_id_projects"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
        sa.UniqueConstraint("project_id", "external_id", name="uq_documents_project_id"),
    )
    op.create_index(
        "ix_documents_project_id_document_type", "documents", ["project_id", "document_type"]
    )
    op.create_index("ix_documents_project_id_vendor", "documents", ["project_id", "vendor"])

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.String(length=32), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("heading", sa.String(length=300), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('english', coalesce(heading, '') || ' ' || text)", persisted=True
            ),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"], name="fk_document_chunks_document_id_documents"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_chunks"),
        sa.UniqueConstraint("document_id", "chunk_id", name="uq_document_chunks_document_id"),
    )
    op.create_index("ix_document_chunks_content_hash", "document_chunks", ["content_hash"])
    op.create_index(
        "ix_document_chunks_search_vector",
        "document_chunks",
        ["search_vector"],
        postgresql_using="gin",
    )

    op.create_table(
        "policy_rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("rule_id", sa.String(length=100), nullable=False),
        sa.Column("policy_document_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.String(length=32), nullable=False),
        sa.Column("policy_area", sa.String(length=64), nullable=False),
        sa.Column("condition", sa.Text(), nullable=False),
        sa.Column("requirement", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "severity IN ('low', 'medium', 'high')",
            name="ck_policy_rules_severity_valid_values",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_policy_rules_project_id_projects"
        ),
        sa.ForeignKeyConstraint(
            ["policy_document_id"],
            ["documents.id"],
            name="fk_policy_rules_policy_document_id_documents",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_policy_rules"),
        sa.UniqueConstraint("project_id", "rule_id", name="uq_policy_rules_project_id"),
    )


def downgrade() -> None:
    op.drop_table("policy_rules")
    op.drop_index("ix_document_chunks_search_vector", table_name="document_chunks")
    op.drop_index("ix_document_chunks_content_hash", table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_index("ix_documents_project_id_vendor", table_name="documents")
    op.drop_index("ix_documents_project_id_document_type", table_name="documents")
    op.drop_table("documents")
    op.drop_column("users", "access_groups_json")
