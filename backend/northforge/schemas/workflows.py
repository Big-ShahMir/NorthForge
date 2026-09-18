"""Request and response models for the workflow and workflow-version API.

Request bodies carry the workflow definition as a raw ``dict`` so routes can
run it through ``schemas.workflow.validate_definition`` and surface a
uniform ``INVALID_WORKFLOW`` error on structural problems, rather than a
generic Pydantic validation error.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WorkflowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    definition: dict[str, Any]


class VersionCreate(BaseModel):
    definition: dict[str, Any]
    source_request: str | None = None


class VersionUpdate(BaseModel):
    definition: dict[str, Any]


class PlanRequest(BaseModel):
    """Body of the planning endpoints (Phase 5).

    ``request`` is required when planning a new workflow. On a re-plan
    (``POST /api/workflows/{id}/plan``) it may be omitted to reuse the base
    version's request, in which case ``answers`` must be non-empty. ``answers``
    are free-text replies to the previous version's clarifying questions.
    """

    request: str | None = Field(default=None, min_length=1, max_length=4000)
    answers: list[str] = Field(default_factory=list, max_length=10)
    name: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("answers")
    @classmethod
    def _answers_non_empty(cls, value: list[str]) -> list[str]:
        cleaned = [answer.strip() for answer in value]
        if any(not answer for answer in cleaned):
            raise ValueError("answers must not be empty strings")
        if any(len(answer) > 1000 for answer in cleaned):
            raise ValueError("each answer must be at most 1000 characters")
        return cleaned


class PlanAccepted(BaseModel):
    """202 body: poll ``GET /api/jobs/{job_id}``; its ``result`` is a ``PlanJobResult``."""

    job_id: str


class VersionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version_number: int
    status: str
    created_at: datetime
    approved_at: datetime | None = None


class VersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workflow_id: uuid.UUID
    version_number: int
    status: str
    definition: dict[str, Any] = Field(validation_alias="definition_json")
    validation_warnings: list[str] = Field(validation_alias="validation_warnings_json")
    source_request: str | None = None
    created_at: datetime
    approved_at: datetime | None = None


class WorkflowSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    description: str
    created_at: datetime
    updated_at: datetime
    current_version_number: int | None = None
    current_version_status: str | None = None


class WorkflowList(BaseModel):
    items: list[WorkflowSummary]
    total: int
    limit: int
    offset: int


class WorkflowDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    description: str
    current_version_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime
    versions: list[VersionSummary]
