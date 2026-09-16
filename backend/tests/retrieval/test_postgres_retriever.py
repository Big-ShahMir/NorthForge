"""Live-database tests for ``PostgresRetriever``.

The corpus is a small, hand-written, six-document set inserted through the
ORM (not the Phase 3 generated dataset, which this agent does not depend
on): one contract accessible to everyone, one restricted to
``legal_restricted``, a near-duplicate contract-version pair sharing one
identical chunk, and a conflicting policy pair. A seventh and eighth
document (a properly-superseded policy pair) are seeded separately, inside
their own test, to keep the shared corpus's "conflicting" scenario
unambiguous (a document cannot simultaneously have empty and non-empty
``supersedes`` metadata for two different tests).
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from northforge.db.engine import create_session_factory, sqlalchemy_url
from northforge.db.models import Document, DocumentChunk, Project, User
from northforge.retrieval.postgres import PostgresRetriever
from northforge.retrieval.retriever import RetrievalQuery

pytestmark = pytest.mark.usefixtures("migrated_database")

PROCUREMENT = frozenset({"procurement"})
PROCUREMENT_AND_LEGAL = frozenset({"procurement", "legal_restricted"})

MakeUser = Callable[..., Awaitable[User]]


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _make_document(
    project_id: Any,
    *,
    external_id: str,
    name: str,
    document_type: str,
    vendor: str | None,
    effective_date: date | None,
    access_group: str,
    metadata_json: dict[str, Any] | None = None,
) -> Document:
    return Document(
        project_id=project_id,
        external_id=external_id,
        name=name,
        document_type=document_type,
        vendor=vendor,
        effective_date=effective_date,
        access_group=access_group,
        metadata_json=metadata_json or {},
        content_hash=_hash(external_id),
        dataset_version="v1",
        chunk_count=1,
    )


def _make_chunk(
    document: Document, *, chunk_id: str, sequence: int, heading: str, text: str
) -> DocumentChunk:
    return DocumentChunk(
        document=document,
        chunk_id=chunk_id,
        sequence=sequence,
        heading=heading,
        text=text,
        start_offset=0,
        end_offset=len(text),
        token_count=len(text.split()),
        content_hash=_hash(text),
        metadata_json={},
    )


GOVERNING_LAW_TEXT = (
    "This Agreement shall be governed by and construed in accordance with the "
    "laws of the State of Delaware without regard to conflict of law principles."
)


async def _seed_project(session: AsyncSession, make_user: MakeUser) -> Any:
    user = await make_user(session, f"user_{uuid4().hex}")
    project = Project(
        owner_id=user.id, name="Retrieval corpus", description="", vertical="contract_review"
    )
    session.add(project)
    await session.flush()
    return project


async def _seed_base_corpus(session: AsyncSession, project_id: Any) -> None:
    doc1 = _make_document(
        project_id,
        external_id="doc_acme_msa",
        name="Acme Cloud Services -- MSA",
        document_type="contract",
        vendor="Acme Cloud Services",
        effective_date=date(2024, 1, 15),
        access_group="procurement",
    )
    session.add(doc1)
    session.add_all(
        [
            _make_chunk(
                doc1,
                chunk_id="c01",
                sequence=1,
                heading="Scope of Services",
                text=(
                    "Vendor shall provide cloud infrastructure hosting services to "
                    "Customer under this Master Service Agreement."
                ),
            ),
            _make_chunk(
                doc1,
                chunk_id="c02",
                sequence=2,
                heading="Renewal Term",
                text=(
                    "This Agreement renews automatically for successive twelve month "
                    "terms unless Customer provides written notice of non-renewal at "
                    "least thirty days before the end of the term."
                ),
            ),
            _make_chunk(
                doc1,
                chunk_id="c03",
                sequence=3,
                heading="Limitation of Liability",
                text=(
                    "Each party's aggregate liability under this Agreement shall not "
                    "exceed the fees paid in the preceding twelve months."
                ),
            ),
        ]
    )

    doc2 = _make_document(
        project_id,
        external_id="doc_blueharbor_dpa",
        name="Blue Harbor Analytics -- DPA",
        document_type="contract",
        vendor="Blue Harbor Analytics",
        effective_date=date(2024, 3, 1),
        access_group="legal_restricted",
    )
    session.add(doc2)
    session.add_all(
        [
            _make_chunk(
                doc2,
                chunk_id="c01",
                sequence=1,
                heading="Data Processing",
                text=(
                    "Processor shall process personal data solely on behalf of "
                    "Controller and implement appropriate technical safeguards."
                ),
            ),
            _make_chunk(
                doc2,
                chunk_id="c02",
                sequence=2,
                heading="Liability for Data Incidents",
                text=(
                    "Processor liability for a data incident arising from breach of "
                    "data protection obligations shall be uncapped."
                ),
            ),
        ]
    )

    doc3 = _make_document(
        project_id,
        external_id="doc_northwind_msa_v1",
        name="Northwind Logistics -- MSA (2023)",
        document_type="contract",
        vendor="Northwind Logistics",
        effective_date=date(2023, 6, 1),
        access_group="procurement",
    )
    session.add(doc3)
    session.add_all(
        [
            _make_chunk(
                doc3, chunk_id="c01", sequence=1, heading="Governing Law", text=GOVERNING_LAW_TEXT
            ),
            _make_chunk(
                doc3,
                chunk_id="c02",
                sequence=2,
                heading="Renewal Term",
                text=(
                    "This Agreement renews for successive twenty four month terms "
                    "with ninety days notice of non-renewal required from Customer "
                    "for the two thousand twenty three version."
                ),
            ),
        ]
    )

    doc4 = _make_document(
        project_id,
        external_id="doc_northwind_msa_v2",
        name="Northwind Logistics -- MSA (2024)",
        document_type="contract",
        vendor="Northwind Logistics",
        effective_date=date(2024, 6, 1),
        access_group="procurement",
    )
    session.add(doc4)
    session.add_all(
        [
            _make_chunk(
                doc4, chunk_id="c01", sequence=1, heading="Governing Law", text=GOVERNING_LAW_TEXT
            ),
            _make_chunk(
                doc4,
                chunk_id="c02",
                sequence=2,
                heading="Renewal Term",
                text=(
                    "This Agreement renews for successive twenty four month terms "
                    "with ninety days notice of non-renewal required from Customer "
                    "for the two thousand twenty four version."
                ),
            ),
        ]
    )

    doc5 = _make_document(
        project_id,
        external_id="doc_policy_renewal_2024",
        name="Procurement Policy Handbook (2024)",
        document_type="policy",
        vendor=None,
        effective_date=date(2024, 1, 1),
        access_group="procurement",
        metadata_json={"policy_area": "renewal", "supersedes": []},
    )
    session.add(doc5)
    session.add(
        _make_chunk(
            doc5,
            chunk_id="c01",
            sequence=1,
            heading="Renewal Notice Requirement",
            text=(
                "Any vendor contract containing an automatic renewal clause must "
                "provide Customer with a notice period of at least thirty days to "
                "decline renewal."
            ),
        )
    )

    doc6 = _make_document(
        project_id,
        external_id="doc_policy_renewal_2025",
        name="Procurement Policy Handbook (2025)",
        document_type="policy",
        vendor=None,
        effective_date=date(2025, 1, 1),
        access_group="procurement",
        metadata_json={"policy_area": "renewal", "supersedes": []},
    )
    session.add(doc6)
    session.add(
        _make_chunk(
            doc6,
            chunk_id="c01",
            sequence=1,
            heading="Renewal Notice Requirement",
            text=(
                "Any vendor contract containing an automatic renewal clause must "
                "provide Customer with a notice period of at least forty five days "
                "to decline renewal."
            ),
        )
    )

    await session.flush()


@pytest.fixture
async def project(db_session: AsyncSession, make_user: MakeUser) -> Any:
    project = await _seed_project(db_session, make_user)
    await _seed_base_corpus(db_session, project.id)
    return project


async def test_access_filter_excludes_restricted_chunk_without_group(
    db_session: AsyncSession, project: Any
) -> None:
    retriever = PostgresRetriever(db_session)
    query = RetrievalQuery(
        project_id=str(project.id),
        query="processor personal data safeguards controller",
        access_groups=PROCUREMENT,
    )

    outcome = await retriever.search(query)

    # Without the legal_restricted group, this query (which only matches a
    # chunk in the restricted document) must abstain, not silently return
    # nothing while claiming success -- and must never return a restricted
    # chunk regardless of outcome status.
    assert outcome.status == "insufficient_evidence"
    assert all(c.metadata["access_group"] != "legal_restricted" for c in outcome.chunks)


async def test_access_filter_includes_restricted_chunk_with_group(
    db_session: AsyncSession, project: Any
) -> None:
    retriever = PostgresRetriever(db_session)
    query = RetrievalQuery(
        project_id=str(project.id),
        query="processor personal data safeguards controller",
        access_groups=PROCUREMENT_AND_LEGAL,
    )

    outcome = await retriever.search(query)

    assert outcome.status == "ok"
    assert any(c.document_id == "doc_blueharbor_dpa" for c in outcome.chunks)


async def test_document_type_filter(db_session: AsyncSession, project: Any) -> None:
    retriever = PostgresRetriever(db_session)
    query = RetrievalQuery(
        project_id=str(project.id),
        query="renewal notice period days decline",
        access_groups=PROCUREMENT,
        document_types=["policy"],
    )

    outcome = await retriever.search(query)

    assert outcome.chunks
    assert all(c.document_type == "policy" for c in outcome.chunks)


async def test_vendor_filter(db_session: AsyncSession, project: Any) -> None:
    retriever = PostgresRetriever(db_session)
    query = RetrievalQuery(
        project_id=str(project.id),
        query="renewal term notice non-renewal customer",
        access_groups=PROCUREMENT,
        vendor="Acme Cloud Services",
    )

    outcome = await retriever.search(query)

    assert outcome.chunks
    assert all(c.metadata.get("vendor") == "Acme Cloud Services" for c in outcome.chunks)


async def test_dedupe_keeps_newest_effective_date(db_session: AsyncSession, project: Any) -> None:
    retriever = PostgresRetriever(db_session)
    query = RetrievalQuery(
        project_id=str(project.id),
        query="governing law Delaware conflict of law principles",
        access_groups=PROCUREMENT,
    )

    outcome = await retriever.search(query)

    governing_law_matches = [c for c in outcome.chunks if c.text == GOVERNING_LAW_TEXT]
    assert len(governing_law_matches) == 1
    assert governing_law_matches[0].document_id == "doc_northwind_msa_v2"


async def test_insufficient_evidence_on_nonsense_query(
    db_session: AsyncSession, project: Any
) -> None:
    retriever = PostgresRetriever(db_session)
    query = RetrievalQuery(
        project_id=str(project.id),
        query="xyzzy quux frobnicate wibblewobble",
        access_groups=PROCUREMENT_AND_LEGAL,
    )

    outcome = await retriever.search(query)

    assert outcome.status == "insufficient_evidence"
    assert outcome.chunks == []


async def test_conflicting_evidence_on_policy_pair(db_session: AsyncSession, project: Any) -> None:
    retriever = PostgresRetriever(db_session)
    query = RetrievalQuery(
        project_id=str(project.id),
        query="renewal notice period vendor contract decline",
        access_groups=PROCUREMENT,
        limit=10,
    )

    outcome = await retriever.search(query)

    assert outcome.status == "conflicting_evidence"
    matched_docs = {c.document_id for c in outcome.chunks}
    assert {"doc_policy_renewal_2024", "doc_policy_renewal_2025"} <= matched_docs


async def test_no_conflict_on_properly_superseded_pair(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    project = await _seed_project(db_session, make_user)

    older = _make_document(
        project.id,
        external_id="doc_policy_termination_2024",
        name="Termination Policy (2024)",
        document_type="policy",
        vendor=None,
        effective_date=date(2024, 6, 1),
        access_group="procurement",
        metadata_json={"policy_area": "termination", "supersedes": []},
    )
    db_session.add(older)
    db_session.add(
        _make_chunk(
            older,
            chunk_id="c01",
            sequence=1,
            heading="Termination Notice",
            text=(
                "Vendor contracts must permit termination for convenience with ninety days notice."
            ),
        )
    )

    newer = _make_document(
        project.id,
        external_id="doc_policy_termination_2025",
        name="Termination Policy (2025)",
        document_type="policy",
        vendor=None,
        effective_date=date(2025, 6, 1),
        access_group="procurement",
        metadata_json={
            "policy_area": "termination",
            "supersedes": ["doc_policy_termination_2024"],
        },
    )
    db_session.add(newer)
    db_session.add(
        _make_chunk(
            newer,
            chunk_id="c01",
            sequence=1,
            heading="Termination Notice",
            text="Vendor contracts must permit termination for convenience with sixty days notice.",
        )
    )
    await db_session.flush()

    retriever = PostgresRetriever(db_session)
    query = RetrievalQuery(
        project_id=str(project.id),
        query="termination convenience notice vendor contracts",
        access_groups=PROCUREMENT,
        limit=10,
    )

    outcome = await retriever.search(query)

    assert outcome.status == "ok"


async def test_get_chunk_denied_outside_access_group(
    db_session: AsyncSession, project: Any
) -> None:
    retriever = PostgresRetriever(db_session)

    chunk = await retriever.get_chunk(str(project.id), "doc_blueharbor_dpa", "c01", PROCUREMENT)

    assert chunk is None


async def test_get_chunk_allowed_within_access_group(
    db_session: AsyncSession, project: Any
) -> None:
    retriever = PostgresRetriever(db_session)

    chunk = await retriever.get_chunk(
        str(project.id), "doc_blueharbor_dpa", "c01", PROCUREMENT_AND_LEGAL
    )

    assert chunk is not None
    assert chunk.chunk_id == "c01"
    assert chunk.document_id == "doc_blueharbor_dpa"


async def test_get_chunk_returns_none_for_unknown_chunk(
    db_session: AsyncSession, project: Any
) -> None:
    retriever = PostgresRetriever(db_session)

    chunk = await retriever.get_chunk(str(project.id), "doc_acme_msa", "c99", PROCUREMENT)

    assert chunk is None


async def test_retriever_accepts_a_session_factory(
    migrated_database: str, make_user: MakeUser
) -> None:
    """The constructor also accepts a session factory, opening its own session per call."""
    engine = create_async_engine(sqlalchemy_url(migrated_database))
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as setup_session:
            project = await _seed_project(setup_session, make_user)
            project_id = project.id
            owner_id = project.owner_id
            doc = _make_document(
                project_id,
                external_id="doc_factory_probe",
                name="Factory Probe Contract",
                document_type="contract",
                vendor="Factory Vendor",
                effective_date=date(2024, 1, 1),
                access_group="procurement",
            )
            setup_session.add(doc)
            setup_session.add(
                _make_chunk(
                    doc,
                    chunk_id="c01",
                    sequence=1,
                    heading="Scope",
                    text="This factory-probe clause covers a distinctive uncommon obligation.",
                )
            )
            await setup_session.commit()

        retriever = PostgresRetriever(session_factory)
        query = RetrievalQuery(
            project_id=str(project_id),
            query="distinctive uncommon obligation",
            access_groups=PROCUREMENT,
        )

        outcome = await retriever.search(query)

        assert outcome.status == "ok"
        assert any(c.document_id == "doc_factory_probe" for c in outcome.chunks)
    finally:
        async with session_factory() as cleanup_session:
            existing = await cleanup_session.scalar(
                select(Document).where(Document.external_id == "doc_factory_probe")
            )
            if existing is not None:
                await cleanup_session.execute(
                    delete(DocumentChunk).where(DocumentChunk.document_id == existing.id)
                )
                await cleanup_session.delete(existing)
            existing_project = await cleanup_session.get(Project, project_id)
            if existing_project is not None:
                await cleanup_session.delete(existing_project)
            existing_user = await cleanup_session.get(User, owner_id)
            if existing_user is not None:
                await cleanup_session.delete(existing_user)
            await cleanup_session.commit()
        await engine.dispose()
