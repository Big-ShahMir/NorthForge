"""Live-database tests for ``ingest_dataset`` against the real synthetic corpus.

Every test ingests the committed dataset at ``backend/data/synthetic`` (the
same directory ``python -m northforge.data.synthetic`` writes and CI checks
for drift), not a hand-written sample -- a full ingest of the 48-document
corpus takes a few hundred milliseconds against the test database, so each
test simply ingests its own project via ``db_session`` rather than sharing
one ingestion across the module.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from northforge.data.synthetic.models import load_dataset
from northforge.db.models import Document, DocumentChunk, Project, User
from northforge.ingestion.pipeline import ingest_dataset
from northforge.storage.memory import MemoryObjectStorage

pytestmark = pytest.mark.usefixtures("migrated_database")

DATASET_DIR = Path(__file__).resolve().parents[2] / "data" / "synthetic"
DATASET = load_dataset(DATASET_DIR)


async def _make_project(session: AsyncSession, subject: str) -> Project:
    user = User(clerk_user_id=subject)
    session.add(user)
    await session.flush()
    project = Project(
        owner_id=user.id, name="ingestion-test", description="", vertical="contract_review"
    )
    session.add(project)
    await session.flush()
    return project


async def test_document_and_rule_counts_match_the_dataset(db_session: AsyncSession) -> None:
    project = await _make_project(db_session, "dev|ingest-counts")
    storage = MemoryObjectStorage()

    report = await ingest_dataset(db_session, storage, project.id, DATASET_DIR, "v1")

    assert report.documents_created == len(DATASET.documents) == DATASET.manifest.document_count
    assert report.documents_updated == 0
    assert report.documents_skipped == 0
    assert report.rules_created == len(DATASET.policy_rules)
    assert report.chunks_created > 0

    document_count = (
        (await db_session.execute(select(Document).where(Document.project_id == project.id)))
        .scalars()
        .all()
    )
    assert len(document_count) == len(DATASET.documents)


async def test_all_policy_rule_sections_resolve_without_a_warning(
    db_session: AsyncSession,
) -> None:
    project = await _make_project(db_session, "dev|ingest-rules")
    storage = MemoryObjectStorage()

    report = await ingest_dataset(db_session, storage, project.id, DATASET_DIR, "v1")

    unresolved = [w for w in report.warnings if "could not resolve section" in w]
    assert unresolved == []
    assert report.rules_created == 11


async def test_idempotent_rerun_reports_everything_skipped(db_session: AsyncSession) -> None:
    project = await _make_project(db_session, "dev|ingest-idempotent")
    storage = MemoryObjectStorage()

    first = await ingest_dataset(db_session, storage, project.id, DATASET_DIR, "v1")
    second = await ingest_dataset(db_session, storage, project.id, DATASET_DIR, "v1")

    assert first.documents_created == len(DATASET.documents)
    assert second.documents_created == 0
    assert second.documents_updated == 0
    assert second.documents_skipped == len(DATASET.documents)
    assert second.chunks_created == 0
    assert second.rules_created == 0
    assert second.warnings == []


async def test_modifying_one_document_replaces_only_its_chunks(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    working_dir = tmp_path / "synthetic"
    shutil.copytree(DATASET_DIR, working_dir)

    project = await _make_project(db_session, "dev|ingest-modify")
    storage = MemoryObjectStorage()

    # First ingest the unmodified copy, then modify one document on disk and
    # re-ingest -- the modification must happen strictly between the two
    # ingest calls for this to exercise "changed content replaces chunks"
    # rather than "identical content is skipped twice".
    first = await ingest_dataset(db_session, storage, project.id, working_dir, "v1")
    assert first.documents_created == len(DATASET.documents)

    target_external_id = "doc_acme_cloud_msa"
    target_path = working_dir / "documents" / f"{target_external_id}.json"
    payload = json.loads(target_path.read_text(encoding="utf-8"))
    original_content = payload["content"]
    payload["content"] = original_content + "\n\n## Amendment\nThis is a test amendment clause.\n"
    target_path.write_text(json.dumps(payload), encoding="utf-8")

    second = await ingest_dataset(db_session, storage, project.id, working_dir, "v1")
    assert second.documents_created == 0
    assert second.documents_updated == 1
    assert second.documents_skipped == len(DATASET.documents) - 1
    assert second.chunks_created > 0

    document = (
        await db_session.execute(
            select(Document).where(
                Document.project_id == project.id, Document.external_id == target_external_id
            )
        )
    ).scalar_one()
    chunks = (
        (
            await db_session.execute(
                select(DocumentChunk).where(DocumentChunk.document_id == document.id)
            )
        )
        .scalars()
        .all()
    )
    assert any("Amendment" in (chunk.heading or "") for chunk in chunks)
    assert any("test amendment clause" in chunk.text for chunk in chunks)

    stored_content = await storage.get_text(document.storage_key)  # type: ignore[arg-type]
    assert "test amendment clause" in stored_content


async def test_storage_keys_are_written_for_every_document(db_session: AsyncSession) -> None:
    project = await _make_project(db_session, "dev|ingest-storage")
    storage = MemoryObjectStorage()

    await ingest_dataset(db_session, storage, project.id, DATASET_DIR, "v1")

    documents = (
        (await db_session.execute(select(Document).where(Document.project_id == project.id)))
        .scalars()
        .all()
    )
    assert documents
    for document in documents:
        assert document.storage_key == f"projects/{project.id}/documents/{document.external_id}.md"
        assert await storage.exists(document.storage_key)
        stored = await storage.get_text(document.storage_key)
        original = next(d for d in DATASET.documents if d.external_id == document.external_id)
        assert stored == original.content


async def test_malformed_document_is_ingested_with_a_warning(db_session: AsyncSession) -> None:
    project = await _make_project(db_session, "dev|ingest-malformed")
    storage = MemoryObjectStorage()

    report = await ingest_dataset(db_session, storage, project.id, DATASET_DIR, "v1")

    malformed_warnings = [w for w in report.warnings if w.startswith("doc_saltmarsh_msa")]
    assert malformed_warnings, report.warnings

    document = (
        await db_session.execute(
            select(Document).where(
                Document.project_id == project.id, Document.external_id == "doc_saltmarsh_msa"
            )
        )
    ).scalar_one()
    assert document.chunk_count > 0
