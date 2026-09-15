"""Request and response models for the projects API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    vertical: str = "contract_review"


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None

    @model_validator(mode="after")
    def _require_at_least_one_field(self) -> Self:
        if self.name is None and self.description is None:
            raise ValueError("at least one field (name, description) must be provided")
        return self


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    description: str
    vertical: str
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None


class ProjectList(BaseModel):
    items: list[ProjectOut]
    total: int
    limit: int
    offset: int
