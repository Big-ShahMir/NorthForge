"""Runs only against a live MinIO/S3 endpoint (``NORTHFORGE_INTEGRATION=1``)."""

from __future__ import annotations

import os
import uuid

import pytest

from northforge.core.config import load_settings
from northforge.storage.base import ObjectNotFoundError
from northforge.storage.s3 import S3ObjectStorage

pytestmark = pytest.mark.integration

if os.environ.get("NORTHFORGE_INTEGRATION") != "1":
    pytest.skip("set NORTHFORGE_INTEGRATION=1 to run", allow_module_level=True)


@pytest.fixture
def storage() -> S3ObjectStorage:
    settings = load_settings()
    return S3ObjectStorage(settings)


@pytest.fixture
def key_prefix() -> str:
    return f"tests/storage/{uuid.uuid4().hex}"


async def test_health_reports_bucket_reachable(storage: S3ObjectStorage) -> None:
    await storage.ensure_bucket()

    assert await storage.health() is True


async def test_put_get_exists_round_trip(storage: S3ObjectStorage, key_prefix: str) -> None:
    await storage.ensure_bucket()
    key = f"{key_prefix}/doc.md"

    assert await storage.exists(key) is False

    written_key = await storage.put_text(key, "# Sample document\n\nHello, MinIO.")

    assert written_key == key
    assert await storage.exists(key) is True
    assert await storage.get_text(key) == "# Sample document\n\nHello, MinIO."


async def test_get_missing_key_raises_object_not_found(
    storage: S3ObjectStorage, key_prefix: str
) -> None:
    await storage.ensure_bucket()

    with pytest.raises(ObjectNotFoundError):
        await storage.get_text(f"{key_prefix}/does-not-exist.md")
