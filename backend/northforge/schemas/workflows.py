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

from pydantic import BaseModel, ConfigDict, Field


class WorkflowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    definition: dict[str, Any]


class VersionCreate(BaseModel):
    definition: dict[str, Any]
    source_request: str | None = None


class VersionUpdate(BaseModel):
    definition: dict[str, Any]


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
