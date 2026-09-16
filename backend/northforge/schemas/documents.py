"""Request and response models for the documents, search, jobs, and me API."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from northforge.retrieval.retriever import RetrievalStatus
from northforge.schemas.evidence import EvidenceChunk

DOCUMENT_CHUNK_PREVIEW_LENGTH = 200


class DocumentOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    external_id: str
    name: str
    document_type: str
    vendor: str | None
    effective_date: date | None
    expires_at: date | None
    access_group: str
    metadata: dict[str, Any]
    dataset_version: str
    chunk_count: int
    created_at: datetime
    updated_at: datetime


class DocumentList(BaseModel):
    items: list[DocumentOut]
    total: int
    limit: int
    offset: int


class ChunkSummary(BaseModel):
    """A chunk's identity plus a preview, never its full text.

    ``preview`` is truncated to ``DOCUMENT_CHUNK_PREVIEW_LENGTH`` characters;
    the full text is only ever returned by the single-chunk endpoint.
    """

    chunk_id: str
    sequence: int
    heading: str | None
    preview: str
    token_count: int


class DocumentDetail(DocumentOut):
    chunks: list[ChunkSummary]


class ChunkOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: uuid.UUID
    document_external_id: str
    chunk_id: str
    sequence: int
    heading: str | None
    text: str
    start_offset: int
    end_offset: int
    token_count: int
    metadata: dict[str, Any]


class IngestRequest(BaseModel):
    dataset_version: str = "v1"


class IngestAccepted(BaseModel):
    job_id: str


JobStatusValue = Literal["queued", "deferred", "in_progress", "complete", "failed", "not_found"]


class JobStatusOut(BaseModel):
    status: JobStatusValue
    result: dict[str, Any] | None = None
    error: str | None = None


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    document_types: list[str] = Field(default_factory=list)
    vendor: str | None = None
    limit: int = Field(default=8, ge=1, le=20)


class SearchResult(BaseModel):
    status: RetrievalStatus
    chunks: list[EvidenceChunk]
    reason: str | None
    total_candidates: int


class MeOut(BaseModel):
    id: uuid.UUID
    subject: str
    email: str | None
    display_name: str | None
    access_groups: list[str]
