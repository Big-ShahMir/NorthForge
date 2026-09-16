"""``ingest_dataset``: load the synthetic dataset into one project.

Idempotent by content hash: a document whose ``content`` hashes the same as
what is already stored is skipped entirely (no storage write, no chunk
churn); a document whose hash differs (new document, or changed content) has
its raw markdown rewritten to object storage and every one of its chunks
replaced in the same transaction the caller commits. Nothing here ever
crashes on malformed input -- a document with stray control characters or
duplicated headings is ingested normally, with a note appended to
``IngestionReport.warnings`` explaining what was found.

This module never commits: both callers (the seed CLI and the worker job)
open one session, call ``ingest_dataset``, and commit themselves, so the
pipeline can also be called repeatedly against one uncommitted test
transaction (see ``tests/ingestion/test_pipeline.py``).
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from northforge.data.synthetic.models import Dataset, DocumentRecord, load_dataset
from northforge.db.models import Document, DocumentChunk, PolicyRuleRow
from northforge.retrieval.chunking import ChunkSpan, chunk_markdown
from northforge.storage.base import ObjectStorage

# The exact set of control characters ``chunk_markdown`` strips before
# chunking (see ``northforge.retrieval.chunking``): C0 controls other than
# tab/newline, plus DEL. Detecting them here -- independently, not by
# importing the chunker's private pattern -- lets the pipeline warn that a
# document needed sanitizing without coupling to the chunker's internals.
_CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


@dataclass
class IngestionReport:
    """Counts and warnings from one ``ingest_dataset`` call."""

    documents_created: int = 0
    documents_updated: int = 0
    documents_skipped: int = 0
    chunks_created: int = 0
    rules_created: int = 0
    warnings: list[str] = field(default_factory=list)


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    return date.fromisoformat(value)


def _sanitization_warning(document: DocumentRecord) -> str | None:
    stray = _CONTROL_CHAR_PATTERN.findall(document.content)
    if not stray:
        return None
    return (
        f"{document.external_id}: content contained {len(stray)} stray control "
        "character(s) that the chunker removed before chunking."
    )


def _duplicate_heading_warning(document: DocumentRecord, spans: list[ChunkSpan]) -> str | None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for span in spans:
        if span.heading is None:
            continue
        if span.heading in seen:
            duplicates.add(span.heading)
        seen.add(span.heading)
    if not duplicates:
        return None
    names = ", ".join(sorted(duplicates))
    return (
        f"{document.external_id}: duplicate heading(s) {names!r} encountered; only the "
        "first chunk with each heading is resolvable by policy_rules.section."
    )


async def _load_existing_documents(
    session: AsyncSession, project_id: uuid.UUID
) -> dict[str, Document]:
    stmt = select(Document).where(Document.project_id == project_id)
    rows = (await session.execute(stmt)).scalars().all()
    return {row.external_id: row for row in rows}


async def _heading_map_for(session: AsyncSession, document: Document) -> dict[str, str]:
    stmt = select(DocumentChunk).where(DocumentChunk.document_id == document.id)
    rows = (await session.execute(stmt)).scalars().all()
    heading_map: dict[str, str] = {}
    for row in rows:
        if row.heading is not None:
            heading_map.setdefault(row.heading, row.chunk_id)
    return heading_map


async def _ingest_document(
    session: AsyncSession,
    storage: ObjectStorage,
    project_id: uuid.UUID,
    document: DocumentRecord,
    existing: Document | None,
    dataset_version: str,
    report: IngestionReport,
) -> tuple[Document, dict[str, str]]:
    content_hash = _content_hash(document.content)

    if existing is not None and existing.content_hash == content_hash:
        report.documents_skipped += 1
        heading_map = await _heading_map_for(session, existing)
        return existing, heading_map

    storage_key = f"projects/{project_id}/documents/{document.external_id}.md"
    await storage.put_text(storage_key, document.content, content_type="text/markdown")

    spans = chunk_markdown(document.content)

    sanitization_warning = _sanitization_warning(document)
    if sanitization_warning is not None:
        report.warnings.append(sanitization_warning)
    duplicate_warning = _duplicate_heading_warning(document, spans)
    if duplicate_warning is not None:
        report.warnings.append(duplicate_warning)

    effective_date = _parse_date(document.effective_date)
    expires_at = _parse_date(document.expires_at)

    if existing is not None:
        row = existing
        row.name = document.name
        row.document_type = document.document_type
        row.vendor = document.vendor
        row.effective_date = effective_date
        row.expires_at = expires_at
        row.access_group = document.access_group
        row.metadata_json = document.metadata
        row.content_hash = content_hash
        row.storage_key = storage_key
        row.dataset_version = dataset_version
        row.chunk_count = len(spans)
        await session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == row.id))
        report.documents_updated += 1
    else:
        row = Document(
            project_id=project_id,
            external_id=document.external_id,
            name=document.name,
            document_type=document.document_type,
            vendor=document.vendor,
            effective_date=effective_date,
            expires_at=expires_at,
            access_group=document.access_group,
            metadata_json=document.metadata,
            content_hash=content_hash,
            storage_key=storage_key,
            dataset_version=dataset_version,
            chunk_count=len(spans),
        )
        session.add(row)
        report.documents_created += 1

    await session.flush()

    new_heading_map: dict[str, str] = {}
    for span in spans:
        chunk_row = DocumentChunk(
            document_id=row.id,
            chunk_id=span.chunk_id,
            sequence=span.sequence,
            heading=span.heading,
            text=span.text,
            start_offset=span.start_offset,
            end_offset=span.end_offset,
            token_count=span.token_count,
            content_hash=_content_hash(span.text),
            metadata_json={},
        )
        session.add(chunk_row)
        report.chunks_created += 1
        if span.heading is not None:
            new_heading_map.setdefault(span.heading, span.chunk_id)

    return row, new_heading_map


async def _ingest_policy_rules(
    session: AsyncSession,
    project_id: uuid.UUID,
    dataset: Dataset,
    documents_by_external_id: dict[str, Document],
    heading_maps: dict[str, dict[str, str]],
    report: IngestionReport,
) -> None:
    existing_stmt = select(PolicyRuleRow.rule_id).where(PolicyRuleRow.project_id == project_id)
    existing_rule_ids = set((await session.execute(existing_stmt)).scalars().all())

    for rule in dataset.policy_rules:
        policy_document = documents_by_external_id.get(rule.policy_document_id)
        if policy_document is None:
            report.warnings.append(
                f"{rule.rule_id}: policy document {rule.policy_document_id!r} was not ingested; "
                "rule skipped."
            )
            continue

        chunk_id = heading_maps.get(rule.policy_document_id, {}).get(rule.section)
        if chunk_id is None:
            report.warnings.append(
                f"{rule.rule_id}: could not resolve section {rule.section!r} to a chunk of "
                f"{rule.policy_document_id!r}; rule skipped."
            )
            continue

        if rule.rule_id in existing_rule_ids:
            update_stmt = select(PolicyRuleRow).where(
                PolicyRuleRow.project_id == project_id,
                PolicyRuleRow.rule_id == rule.rule_id,
            )
            row = (await session.execute(update_stmt)).scalar_one()
            row.policy_document_id = policy_document.id
            row.chunk_id = chunk_id
            row.policy_area = rule.policy_area
            row.condition = rule.condition
            row.requirement = rule.requirement
            row.severity = rule.severity
            continue

        session.add(
            PolicyRuleRow(
                project_id=project_id,
                rule_id=rule.rule_id,
                policy_document_id=policy_document.id,
                chunk_id=chunk_id,
                policy_area=rule.policy_area,
                condition=rule.condition,
                requirement=rule.requirement,
                severity=rule.severity,
            )
        )
        report.rules_created += 1


async def ingest_dataset(
    session: AsyncSession,
    storage: ObjectStorage,
    project_id: uuid.UUID | str,
    dataset_dir: Path,
    dataset_version: str,
) -> IngestionReport:
    """Ingest every document, chunk, and policy rule of the dataset at ``dataset_dir``.

    Idempotent by ``Document.content_hash``: an unchanged document is
    skipped (no storage write, no chunk churn); a new or changed document
    has its content rewritten to storage and its chunks replaced. Never
    raises on malformed content -- warnings are appended to the returned
    report instead.
    """
    resolved_project_id = project_id if isinstance(project_id, uuid.UUID) else uuid.UUID(project_id)
    dataset = load_dataset(dataset_dir)
    report = IngestionReport()

    existing_documents = await _load_existing_documents(session, resolved_project_id)

    documents_by_external_id: dict[str, Document] = {}
    heading_maps: dict[str, dict[str, str]] = {}

    for document in dataset.documents:
        row, heading_map = await _ingest_document(
            session,
            storage,
            resolved_project_id,
            document,
            existing_documents.get(document.external_id),
            dataset_version,
            report,
        )
        documents_by_external_id[document.external_id] = row
        heading_maps[document.external_id] = heading_map

    await session.flush()

    await _ingest_policy_rules(
        session, resolved_project_id, dataset, documents_by_external_id, heading_maps, report
    )
    await session.flush()

    return report


__all__ = ["IngestionReport", "ingest_dataset"]
