"""Live smoke tests against the real hosted NVIDIA API.

Skipped unless ``NORTHFORGE_LIVE_MODELS=1`` and a real ``NVIDIA_API_KEY`` are
present (loaded from the repo ``.env`` via ``load_settings()`` with its
default env file). These make real network calls and count against the
hosted free tier's 40-requests-per-minute limit, so they never run as part
of the default test suite.
"""

from __future__ import annotations

import os

import pytest
from pydantic import BaseModel

from northforge.core.config import load_settings
from northforge.providers.capabilities import CapabilityRegistry
from northforge.providers.nvidia import NvidiaProvider
from northforge.providers.types import EmbeddingRequest, GenerationRequest, Message


def _has_nvidia_api_key() -> bool:
    """Best-effort check: an incomplete ``.env`` must skip, not crash collection."""
    try:
        return load_settings().nvidia_api_key is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    os.environ.get("NORTHFORGE_LIVE_MODELS") != "1" or not _has_nvidia_api_key(),
    reason="set NORTHFORGE_LIVE_MODELS=1 and NVIDIA_API_KEY to run live NVIDIA smoke tests",
)


class Ping(BaseModel):
    answer: str
    number: int


@pytest.mark.asyncio
async def test_live_structured_ping_on_extractor_model() -> None:
    settings = load_settings()
    registry = CapabilityRegistry.load(settings.model_capabilities_file)
    model = settings.nvidia_model_extraction or registry.default_routes["extractor"].primary
    provider = NvidiaProvider(settings, registry)
    try:
        request = GenerationRequest(
            messages=[Message(role="user", content="Reply with answer='pong' and number=7.")]
        )
        response = await provider.generate_structured(request, model, Ping)
        assert response.parsed is not None
        assert response.parsed.answer
        assert isinstance(response.parsed.number, int)
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_live_embed_two_strings_returns_2048_dims() -> None:
    settings = load_settings()
    registry = CapabilityRegistry.load(settings.model_capabilities_file)
    model = settings.embedding_model or registry.default_routes["embedding"].primary
    provider = NvidiaProvider(settings, registry)
    try:
        response = await provider.embed(
            EmbeddingRequest(texts=["north forge policy review", "contract clause extraction"]),
            model,
        )
        assert response.dimensions == 2048
        assert len(response.vectors) == 2
        assert all(len(vector) == 2048 for vector in response.vectors)
    finally:
        await provider.aclose()
