"""API tests for document, search, job-status, and ``/api/me`` routes.

Documents are ingested directly through ``ingest_dataset`` against the
shared ``db_session`` (the same session ``api_client``'s requests run
against, via the ``get_session`` override in ``tests/api/conftest.py``)
rather than through the (not-yet-triggered) ``ingest`` endpoint, so most
tests do not depend on a real worker or Redis. The ingest and job-status
endpoints are tested separately with a fake arq pool and a fake ``Job``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx2 as httpx
import pytest
from arq.jobs import JobStatus as ArqJobStatus
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from northforge.auth.principal import Principal
from northforge.db.models import Document, User
from northforge.ingestion.pipeline import ingest_dataset
from northforge.storage.memory import MemoryObjectStorage
from tests.api.conftest import AsUser

ALICE = Principal(subject="dev|alice")
BOB = Principal(subject="dev|bob")

DATASET_DIR = Path(__file__).resolve().parents[2] / "data" / "synthetic"

BLUE_HARBOR_DOCUMENT_IDS = {"doc_blue_harbor_dpa", "doc_blue_harbor_msa"}


async def _create_project(
    api_client: httpx.AsyncClient, as_user: AsUser, principal: Principal
) -> str:
    as_user(principal)
    response = await api_client.post(
        "/api/projects", json={"name": f"{principal.subject}'s project"}
    )
    assert response.status_code == 201
    project_id: str = response.json()["data"]["id"]
    return project_id


async def _ingest(db_session: AsyncSession, project_id: str) -> None:
    await ingest_dataset(
        db_session, MemoryObjectStorage(), uuid.UUID(project_id), DATASET_DIR, "v1"
    )
    await db_session.flush()


async def _grant_group(db_session: AsyncSession, subject: str, group: str) -> None:
    user = (
        await db_session.execute(select(User).where(User.clerk_user_id == subject))
    ).scalar_one()
    user.access_groups_json = [*user.access_groups_json, group]
    await db_session.flush()


async def test_list_documents_requires_authentication(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get(f"/api/projects/{uuid.uuid4()}/documents")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_list_documents_404_for_a_non_owner(
    api_client: httpx.AsyncClient, as_user: AsUser, db_session: AsyncSession
) -> None:
    project_id = await _create_project(api_client, as_user, ALICE)
    await _ingest(db_session, project_id)

    as_user(BOB)
    response = await api_client.get(f"/api/projects/{project_id}/documents")

    assert response.status_code == 404


async def test_list_documents_returns_the_whole_corpus_for_procurement(
    api_client: httpx.AsyncClient, as_user: AsUser, db_session: AsyncSession
) -> None:
    project_id = await _create_project(api_client, as_user, ALICE)
    await _ingest(db_session, project_id)

    as_user(ALICE)
    response = await api_client.get(f"/api/projects/{project_id}/documents", params={"limit": 100})

    assert response.status_code == 200
    data = response.json()["data"]
    # 46 of 48 documents are "procurement"; 2 are "legal_restricted" (Blue Harbor).
    external_ids = {item["external_id"] for item in data["items"]}
    assert external_ids.isdisjoint(BLUE_HARBOR_DOCUMENT_IDS)
    assert data["total"] == len(external_ids)


async def test_list_documents_filters_by_vendor_and_access_group(
    api_client: httpx.AsyncClient, as_user: AsUser, db_session: AsyncSession
) -> None:
    project_id = await _create_project(api_client, as_user, ALICE)
    await _ingest(db_session, project_id)

    as_user(ALICE)
    await api_client.get("/api/me")  # ensure the User row exists before granting a group

    without_access = await api_client.get(
        f"/api/projects/{project_id}/documents",
        params={"vendor": "Blue Harbor Analytics"},
    )
    assert without_access.status_code == 200
    assert without_access.json()["data"]["total"] == 0
    assert without_access.json()["data"]["items"] == []

    await _grant_group(db_session, "dev|alice", "legal_restricted")

    with_access = await api_client.get(
        f"/api/projects/{project_id}/documents",
        params={"vendor": "Blue Harbor Analytics"},
    )
    assert with_access.status_code == 200
    body = with_access.json()["data"]
    assert body["total"] == 2
    assert {item["external_id"] for item in body["items"]} == BLUE_HARBOR_DOCUMENT_IDS


async def test_get_document_404_outside_access_group(
    api_client: httpx.AsyncClient, as_user: AsUser, db_session: AsyncSession
) -> None:
    project_id = await _create_project(api_client, as_user, ALICE)
    await _ingest(db_session, project_id)

    as_user(ALICE)
    listing = await api_client.get(
        f"/api/projects/{project_id}/documents",
        params={"vendor": "Blue Harbor Analytics", "document_type": "msa"},
    )
    assert listing.json()["data"]["total"] == 0

    document_row = (
        await db_session.execute(
            select(Document).where(Document.external_id == "doc_blue_harbor_msa")
        )
    ).scalar_one()

    response = await api_client.get(f"/api/documents/{document_row.id}")
    assert response.status_code == 404


async def test_get_document_and_chunk_after_granting_access(
    api_client: httpx.AsyncClient, as_user: AsUser, db_session: AsyncSession
) -> None:
    project_id = await _create_project(api_client, as_user, ALICE)
    await _ingest(db_session, project_id)

    document_row = (
        await db_session.execute(
            select(Document).where(Document.external_id == "doc_blue_harbor_msa")
        )
    ).scalar_one()

    as_user(ALICE)
    await api_client.get("/api/me")
    await _grant_group(db_session, "dev|alice", "legal_restricted")

    detail_response = await api_client.get(f"/api/documents/{document_row.id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()["data"]
    assert detail["external_id"] == "doc_blue_harbor_msa"
    assert len(detail["chunks"]) == document_row.chunk_count
    for chunk in detail["chunks"]:
        assert len(chunk["preview"]) <= 200

    first_chunk_id = detail["chunks"][0]["chunk_id"]
    chunk_response = await api_client.get(
        f"/api/documents/{document_row.id}/chunks/{first_chunk_id}"
    )
    assert chunk_response.status_code == 200
    chunk_body = chunk_response.json()["data"]
    assert chunk_body["chunk_id"] == first_chunk_id
    assert chunk_body["document_external_id"] == "doc_blue_harbor_msa"
    assert chunk_body["text"]


async def test_get_document_chunk_404_outside_access_group(
    api_client: httpx.AsyncClient, as_user: AsUser, db_session: AsyncSession
) -> None:
    project_id = await _create_project(api_client, as_user, ALICE)
    await _ingest(db_session, project_id)

    document_row = (
        await db_session.execute(
            select(Document).where(Document.external_id == "doc_blue_harbor_msa")
        )
    ).scalar_one()

    as_user(ALICE)
    response = await api_client.get(f"/api/documents/{document_row.id}/chunks/c01")

    assert response.status_code == 404


async def test_search_never_returns_restricted_chunks(
    api_client: httpx.AsyncClient, as_user: AsUser, db_session: AsyncSession
) -> None:
    project_id = await _create_project(api_client, as_user, ALICE)
    await _ingest(db_session, project_id)

    as_user(ALICE)
    response = await api_client.post(
        f"/api/projects/{project_id}/search",
        json={"query": "data processing agreement personal data subprocessor", "limit": 20},
    )

    assert response.status_code == 200
    body = response.json()["data"]
    for chunk in body["chunks"]:
        assert chunk["metadata"]["access_group"] == "procurement"
        assert chunk["document_id"] not in BLUE_HARBOR_DOCUMENT_IDS


async def test_search_returns_ok_outcome_for_a_real_query(
    api_client: httpx.AsyncClient, as_user: AsUser, db_session: AsyncSession
) -> None:
    project_id = await _create_project(api_client, as_user, ALICE)
    await _ingest(db_session, project_id)

    as_user(ALICE)
    response = await api_client.post(
        f"/api/projects/{project_id}/search", json={"query": "notice period for renewal"}
    )

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["status"] == "ok"
    assert len(body["chunks"]) > 0


async def test_search_abstains_for_a_nonsense_query(
    api_client: httpx.AsyncClient, as_user: AsUser, db_session: AsyncSession
) -> None:
    project_id = await _create_project(api_client, as_user, ALICE)
    await _ingest(db_session, project_id)

    as_user(ALICE)
    response = await api_client.post(
        f"/api/projects/{project_id}/search",
        json={"query": "zzqxv nonexistent gibberish flibbertigibbet"},
    )

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["status"] == "insufficient_evidence"
    assert body["chunks"] == []


async def test_search_404_for_a_non_owned_project(
    api_client: httpx.AsyncClient, as_user: AsUser, db_session: AsyncSession
) -> None:
    project_id = await _create_project(api_client, as_user, ALICE)
    await _ingest(db_session, project_id)

    as_user(BOB)
    response = await api_client.post(
        f"/api/projects/{project_id}/search", json={"query": "renewal notice"}
    )

    assert response.status_code == 404


class _FakeArqPool:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, tuple[Any, ...]]] = []

    async def enqueue_job(self, name: str, *args: Any) -> SimpleNamespace:
        self.enqueued.append((name, args))
        return SimpleNamespace(job_id="fake-job-123")

    async def aclose(self) -> None:
        """No-op: the app's lifespan closes ``app.state.arq_pool`` on shutdown."""


async def test_ingest_endpoint_returns_202_and_enqueues_the_job(
    api_client: httpx.AsyncClient, api_app: FastAPI, as_user: AsUser
) -> None:
    project_id = await _create_project(api_client, as_user, ALICE)
    pool = _FakeArqPool()
    api_app.state.arq_pool = pool

    response = await api_client.post(
        f"/api/projects/{project_id}/documents/ingest", json={"dataset_version": "v1"}
    )

    assert response.status_code == 202
    assert response.json()["data"]["job_id"] == "fake-job-123"
    assert pool.enqueued == [("ingest_synthetic_dataset", (project_id, "v1"))]


async def test_ingest_endpoint_404_for_a_non_owned_project(
    api_client: httpx.AsyncClient, as_user: AsUser
) -> None:
    project_id = await _create_project(api_client, as_user, ALICE)

    as_user(BOB)
    response = await api_client.post(
        f"/api/projects/{project_id}/documents/ingest", json={"dataset_version": "v1"}
    )

    assert response.status_code == 404


class _FakeJobResult:
    def __init__(self, success: bool, result: Any) -> None:
        self.success = success
        self.result = result


class _FakeJob:
    def __init__(self, status: ArqJobStatus, result_info: _FakeJobResult | None = None) -> None:
        self._status = status
        self._result_info = result_info

    async def status(self) -> ArqJobStatus:
        return self._status

    async def result_info(self) -> _FakeJobResult | None:
        return self._result_info


def _fake_job_class(preset: _FakeJob) -> Callable[..., _FakeJob]:
    """A stand-in for ``arq.jobs.Job`` that always returns ``preset``.

    ``northforge.api.routes.documents.get_job`` constructs ``Job(job_id,
    redis=..., _queue_name=...)`` directly, so tests monkeypatch the ``Job``
    name in that module with the callable this returns rather than a real
    class with a real Redis connection.
    """

    def _factory(*args: Any, **kwargs: Any) -> _FakeJob:
        return preset

    return _factory


async def test_jobs_endpoint_maps_queued_status(
    api_client: httpx.AsyncClient, as_user: AsUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    as_user(ALICE)
    monkeypatch.setattr(
        "northforge.api.routes.documents.Job",
        _fake_job_class(_FakeJob(ArqJobStatus.queued)),
    )

    response = await api_client.get("/api/jobs/some-job-id")

    assert response.status_code == 200
    body = response.json()["data"]
    assert body == {"status": "queued", "result": None, "error": None}


async def test_jobs_endpoint_maps_complete_success(
    api_client: httpx.AsyncClient, as_user: AsUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    as_user(ALICE)
    result_info = _FakeJobResult(success=True, result={"documents_created": 48})
    monkeypatch.setattr(
        "northforge.api.routes.documents.Job",
        _fake_job_class(_FakeJob(ArqJobStatus.complete, result_info)),
    )

    response = await api_client.get("/api/jobs/some-job-id")

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["status"] == "complete"
    assert body["result"] == {"documents_created": 48}
    assert body["error"] is None


async def test_jobs_endpoint_maps_complete_failure_without_leaking_exception_text(
    api_client: httpx.AsyncClient, as_user: AsUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    as_user(ALICE)
    result_info = _FakeJobResult(success=False, result=RuntimeError("a secret connection string"))
    monkeypatch.setattr(
        "northforge.api.routes.documents.Job",
        _fake_job_class(_FakeJob(ArqJobStatus.complete, result_info)),
    )

    response = await api_client.get("/api/jobs/some-job-id")

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["status"] == "failed"
    assert body["error"] == "RuntimeError"
    assert "secret" not in (body["error"] or "")
    assert body["result"] is None


async def test_jobs_endpoint_maps_not_found(
    api_client: httpx.AsyncClient, as_user: AsUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    as_user(ALICE)
    monkeypatch.setattr(
        "northforge.api.routes.documents.Job",
        _fake_job_class(_FakeJob(ArqJobStatus.not_found)),
    )

    response = await api_client.get("/api/jobs/does-not-exist")

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "not_found"


async def test_me_returns_the_callers_identity_and_access_groups(
    api_client: httpx.AsyncClient, as_user: AsUser, db_session: AsyncSession
) -> None:
    as_user(ALICE)

    response = await api_client.get("/api/me")

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["subject"] == "dev|alice"
    assert body["access_groups"] == ["procurement"]

    await _grant_group(db_session, "dev|alice", "legal_restricted")

    response = await api_client.get("/api/me")
    assert response.json()["data"]["access_groups"] == ["procurement", "legal_restricted"]


async def test_me_requires_authentication(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get("/api/me")

    assert response.status_code == 401
