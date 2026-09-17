"""``ProviderEmbedder`` and ``ProviderReranker`` over the mock provider (no network)."""

from __future__ import annotations

import pytest

from northforge.core.config import Settings, load_settings
from northforge.providers.factory import build_model_router
from northforge.providers.mock import MockProvider
from northforge.providers.router import ModelRouter
from northforge.providers.types import EmbeddingRequest
from northforge.retrieval.embedder import Embedder, NoopEmbedder, ProviderEmbedder
from northforge.retrieval.reranker import NoopReranker, ProviderReranker, Reranker
from northforge.schemas.evidence import EvidenceChunk
from tests.conftest import set_unit_s3_env


@pytest.fixture
def mock_router(monkeypatch: pytest.MonkeyPatch) -> ModelRouter:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "mock")
    monkeypatch.setenv("RERANKER_PROVIDER", "mock")
    set_unit_s3_env(monkeypatch)
    settings: Settings = load_settings(env_file=None)
    return build_model_router(settings)


def _chunk(chunk_id: str, text: str) -> EvidenceChunk:
    return EvidenceChunk(
        document_id="doc_a",
        document_name="Doc A",
        document_type="contract",
        chunk_id=chunk_id,
        text=text,
        score=0.5,
    )


async def test_provider_embedder_uses_passage_and_query_types(mock_router: ModelRouter) -> None:
    embedder = ProviderEmbedder(mock_router)
    assert isinstance(embedder, Embedder)
    assert embedder.dimensions == 2048

    vectors = await embedder.embed(["termination clause", "renewal notice"])
    query = await embedder.embed_query("termination")

    assert len(vectors) == 2 and all(len(v) == 2048 for v in vectors)
    assert len(query) == 2048
    mock = mock_router.providers()["mock"]
    assert isinstance(mock, MockProvider)
    requests = [call.request for call in mock.calls if call.operation == "embed"]
    assert [r.input_type for r in requests if isinstance(r, EmbeddingRequest)] == [
        "passage",
        "query",
    ]
    assert await embedder.embed([]) == []


async def test_provider_embedder_rejects_dimension_mismatch(mock_router: ModelRouter) -> None:
    embedder = ProviderEmbedder(mock_router, dimensions=1536)

    with pytest.raises(ValueError, match="2048"):
        await embedder.embed(["x"])


async def test_noop_embedder_still_fails_loudly() -> None:
    with pytest.raises(NotImplementedError):
        await NoopEmbedder().embed(["x"])


async def test_provider_reranker_reorders_and_truncates(mock_router: ModelRouter) -> None:
    reranker = ProviderReranker(mock_router)
    assert isinstance(reranker, Reranker)
    chunks = [
        _chunk("c1", "payment terms net thirty days"),
        _chunk("c2", "either party may terminate for convenience"),
        _chunk("c3", "termination for convenience requires notice"),
    ]

    ranked = await reranker.rerank("terminate for convenience", chunks, top_n=2)

    assert ranked[0].chunk_id == "c2"
    assert len(ranked) == 2
    assert {c.chunk_id for c in ranked} <= {"c1", "c2", "c3"}
    assert await reranker.rerank("anything", [], top_n=2) == []


async def test_noop_reranker_keeps_order() -> None:
    chunks = [_chunk("c1", "a"), _chunk("c2", "b")]
    assert [c.chunk_id for c in await NoopReranker().rerank("q", chunks)] == ["c1", "c2"]
    assert [c.chunk_id for c in await NoopReranker().rerank("q", chunks, top_n=1)] == ["c1"]
