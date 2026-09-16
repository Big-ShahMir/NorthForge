"""Documents, search, job-status, and current-user routes.

Every route requires an authenticated caller. Document and chunk reads go
through ``DocumentsRepository``, which filters by project ownership and the
caller's access groups in SQL (see that module's docstring); search goes
directly through ``PostgresRetriever`` rather than the tool-invocation
pipeline, since this endpoint *is* the evidence-browsing surface, not a tool
call. Ingestion is asynchronous: the route only enqueues the worker job and
returns its id, never waits for it.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from arq.jobs import Job
from arq.jobs import JobStatus as ArqJobStatus
from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from northforge.api.deps import get_session
from northforge.api.envelope import Envelope, request_id_of
from northforge.auth.dependencies import get_current_user
from northforge.core.errors import NotFoundError
from northforge.core.queue import QUEUE_NAME
from northforge.db.models import Document, DocumentChunk, User
from northforge.db.repositories.documents import DocumentsRepository
from northforge.db.repositories.projects import ProjectsRepository
from northforge.retrieval.postgres import PostgresRetriever
from northforge.retrieval.retriever import RetrievalQuery
from northforge.schemas.documents import (
    DOCUMENT_CHUNK_PREVIEW_LENGTH,
    ChunkOut,
    ChunkSummary,
    DocumentDetail,
    DocumentList,
    DocumentOut,
    IngestAccepted,
    IngestRequest,
    JobStatusOut,
    JobStatusValue,
    MeOut,
    SearchRequest,
    SearchResult,
)

router = APIRouter(tags=["documents"])

CurrentUser = Annotated[User, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(get_session)]

_ARQ_STATUS_MAP: dict[ArqJobStatus, JobStatusValue] = {
    ArqJobStatus.deferred: "deferred",
    ArqJobStatus.queued: "queued",
    ArqJobStatus.in_progress: "in_progress",
    ArqJobStatus.complete: "complete",
    ArqJobStatus.not_found: "not_found",
}


def _access_groups(user: User) -> frozenset[str]:
    return frozenset(str(group) for group in user.access_groups_json)


def _document_out(document: Document) -> DocumentOut:
    return DocumentOut(
        id=document.id,
        external_id=document.external_id,
        name=document.name,
        document_type=document.document_type,
        vendor=document.vendor,
        effective_date=document.effective_date,
        expires_at=document.expires_at,
        access_group=document.access_group,
        metadata=document.metadata_json,
        dataset_version=document.dataset_version,
        chunk_count=document.chunk_count,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


def _chunk_summary(chunk: DocumentChunk) -> ChunkSummary:
    return ChunkSummary(
        chunk_id=chunk.chunk_id,
        sequence=chunk.sequence,
        heading=chunk.heading,
        preview=chunk.text[:DOCUMENT_CHUNK_PREVIEW_LENGTH],
        token_count=chunk.token_count,
    )


def _chunk_out(document: Document, chunk: DocumentChunk) -> ChunkOut:
    return ChunkOut(
        document_id=document.id,
        document_external_id=document.external_id,
        chunk_id=chunk.chunk_id,
        sequence=chunk.sequence,
        heading=chunk.heading,
        text=chunk.text,
        start_offset=chunk.start_offset,
        end_offset=chunk.end_offset,
        token_count=chunk.token_count,
        metadata=chunk.metadata_json,
    )


@router.get("/api/projects/{project_id}/documents", response_model=Envelope[DocumentList])
async def list_documents(
    request: Request,
    project_id: uuid.UUID,
    user: CurrentUser,
    session: Session,
    document_type: str | None = None,
    vendor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Envelope[DocumentList]:
    result = await DocumentsRepository(session).list_for_project(
        project_id,
        user.id,
        _access_groups(user),
        limit,
        offset,
        document_type=document_type,
        vendor=vendor,
    )
    if result is None:
        raise NotFoundError("Project not found.")
    documents, total = result
    return Envelope[DocumentList](
        data=DocumentList(
            items=[_document_out(document) for document in documents],
            total=total,
            limit=limit,
            offset=offset,
        ),
        request_id=request_id_of(request),
    )


@router.get("/api/documents/{document_id}", response_model=Envelope[DocumentDetail])
async def get_document(
    request: Request, document_id: uuid.UUID, user: CurrentUser, session: Session
) -> Envelope[DocumentDetail]:
    document = await DocumentsRepository(session).get_for_owner(
        document_id, user.id, _access_groups(user)
    )
    if document is None:
        raise NotFoundError("Document not found.")
    detail = DocumentDetail(
        **_document_out(document).model_dump(),
        chunks=[_chunk_summary(chunk) for chunk in document.chunks],
    )
    return Envelope[DocumentDetail](data=detail, request_id=request_id_of(request))


@router.get("/api/documents/{document_id}/chunks/{chunk_id}", response_model=Envelope[ChunkOut])
async def get_document_chunk_route(
    request: Request,
    document_id: uuid.UUID,
    chunk_id: str,
    user: CurrentUser,
    session: Session,
) -> Envelope[ChunkOut]:
    result = await DocumentsRepository(session).get_chunk_for_owner(
        document_id, chunk_id, user.id, _access_groups(user)
    )
    if result is None:
        raise NotFoundError("Document chunk not found.")
    document, chunk = result
    return Envelope[ChunkOut](data=_chunk_out(document, chunk), request_id=request_id_of(request))


@router.post(
    "/api/projects/{project_id}/documents/ingest",
    response_model=Envelope[IngestAccepted],
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_project_documents(
    request: Request,
    project_id: uuid.UUID,
    body: IngestRequest,
    user: CurrentUser,
    session: Session,
) -> Envelope[IngestAccepted]:
    project = await ProjectsRepository(session).get_for_owner(project_id, user.id)
    if project is None:
        raise NotFoundError("Project not found.")

    pool = request.app.state.arq_pool
    job = await pool.enqueue_job("ingest_synthetic_dataset", str(project_id), body.dataset_version)
    job_id = job.job_id if job is not None else ""
    return Envelope[IngestAccepted](
        data=IngestAccepted(job_id=job_id), request_id=request_id_of(request)
    )


@router.get("/api/jobs/{job_id}", response_model=Envelope[JobStatusOut])
async def get_job(request: Request, job_id: str, user: CurrentUser) -> Envelope[JobStatusOut]:
    del user  # job ids are unguessable arq identifiers; no per-user ownership to check
    redis = request.app.state.redis
    job = Job(job_id, redis=redis, _queue_name=QUEUE_NAME)
    arq_status = await job.status()
    mapped: JobStatusValue = _ARQ_STATUS_MAP.get(arq_status, "not_found")

    result_value: dict[str, Any] | None = None
    error_value: str | None = None
    if mapped == "complete":
        info = await job.result_info()
        if info is not None and not info.success:
            mapped = "failed"
            error_value = type(info.result).__name__ if info.result is not None else "JobFailed"
        elif info is not None:
            result_value = info.result if isinstance(info.result, dict) else {"value": info.result}

    return Envelope[JobStatusOut](
        data=JobStatusOut(status=mapped, result=result_value, error=error_value),
        request_id=request_id_of(request),
    )


@router.post("/api/projects/{project_id}/search", response_model=Envelope[SearchResult])
async def search_project(
    request: Request,
    project_id: uuid.UUID,
    body: SearchRequest,
    user: CurrentUser,
    session: Session,
) -> Envelope[SearchResult]:
    project = await ProjectsRepository(session).get_for_owner(project_id, user.id)
    if project is None:
        raise NotFoundError("Project not found.")

    retriever = PostgresRetriever(session)
    query = RetrievalQuery(
        project_id=str(project_id),
        query=body.query,
        access_groups=_access_groups(user),
        document_types=body.document_types,
        vendor=body.vendor,
        limit=body.limit,
    )
    outcome = await retriever.search(query)
    return Envelope[SearchResult](
        data=SearchResult(
            status=outcome.status,
            chunks=outcome.chunks,
            reason=outcome.reason,
            total_candidates=outcome.total_candidates,
        ),
        request_id=request_id_of(request),
    )


@router.get("/api/me", response_model=Envelope[MeOut])
async def get_me(request: Request, user: CurrentUser) -> Envelope[MeOut]:
    return Envelope[MeOut](
        data=MeOut(
            id=user.id,
            subject=user.clerk_user_id,
            email=user.email,
            display_name=user.display_name,
            access_groups=[str(group) for group in user.access_groups_json],
        ),
        request_id=request_id_of(request),
    )
