"""Documents repository: owner-scoped through projects, access-group-filtered reads.

Every read here is scoped two ways at once: the document's project must be
owned by the caller (``Project.owner_id``), and the document's
``access_group`` must be one of the caller's groups. Both conditions are
applied in the SQL ``WHERE`` clause, not filtered in Python after the fact,
so a restricted document is indistinguishable from a nonexistent one to a
caller without the group -- consistent with how the retrieval tools treat
access denial.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from northforge.db.models import Document, DocumentChunk, Project


class DocumentsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_project(
        self,
        project_id: uuid.UUID,
        owner_id: uuid.UUID,
        access_groups: frozenset[str],
        limit: int,
        offset: int,
        *,
        document_type: str | None = None,
        vendor: str | None = None,
    ) -> tuple[list[Document], int] | None:
        """Documents in ``project_id`` visible to ``access_groups``.

        Returns ``None`` when ``project_id`` does not exist or is not owned
        by ``owner_id``, so the route can 404 rather than return an empty
        (and misleading) list.
        """
        owned = (
            await self._session.execute(
                select(Project.id).where(Project.id == project_id, Project.owner_id == owner_id)
            )
        ).scalar_one_or_none()
        if owned is None:
            return None

        base = select(Document).where(
            Document.project_id == project_id,
            Document.access_group.in_(access_groups),
        )
        if document_type is not None:
            base = base.where(Document.document_type == document_type)
        if vendor is not None:
            base = base.where(Document.vendor == vendor)

        total = (
            await self._session.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()

        rows_stmt = base.order_by(Document.name).limit(limit).offset(offset)
        rows = (await self._session.execute(rows_stmt)).scalars().all()
        return list(rows), total

    async def get_for_owner(
        self, document_id: uuid.UUID, owner_id: uuid.UUID, access_groups: frozenset[str]
    ) -> Document | None:
        stmt = (
            select(Document)
            .join(Project, Project.id == Document.project_id)
            .where(
                Document.id == document_id,
                Project.owner_id == owner_id,
                Document.access_group.in_(access_groups),
            )
            .options(selectinload(Document.chunks))
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_chunk_for_owner(
        self,
        document_id: uuid.UUID,
        chunk_id: str,
        owner_id: uuid.UUID,
        access_groups: frozenset[str],
    ) -> tuple[Document, DocumentChunk] | None:
        stmt = (
            select(Document, DocumentChunk)
            .join(Project, Project.id == Document.project_id)
            .join(DocumentChunk, DocumentChunk.document_id == Document.id)
            .where(
                Document.id == document_id,
                DocumentChunk.chunk_id == chunk_id,
                Project.owner_id == owner_id,
                Document.access_group.in_(access_groups),
            )
        )
        row = (await self._session.execute(stmt)).first()
        if row is None:
            return None
        document, chunk = row
        return document, chunk
