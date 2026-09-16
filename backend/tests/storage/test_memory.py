from __future__ import annotations

import pytest

from northforge.storage.base import ObjectNotFoundError
from northforge.storage.memory import MemoryObjectStorage


async def test_put_then_get_round_trips() -> None:
    storage = MemoryObjectStorage()

    key = await storage.put_text("projects/p1/documents/doc.md", "hello world")

    assert key == "projects/p1/documents/doc.md"
    assert await storage.get_text(key) == "hello world"


async def test_exists_reflects_writes() -> None:
    storage = MemoryObjectStorage()

    assert await storage.exists("missing") is False

    await storage.put_text("missing", "now it exists")

    assert await storage.exists("missing") is True


async def test_get_missing_key_raises_object_not_found() -> None:
    storage = MemoryObjectStorage()

    with pytest.raises(ObjectNotFoundError) as excinfo:
        await storage.get_text("does-not-exist")

    assert excinfo.value.code == "STORAGE_OBJECT_NOT_FOUND"
    assert excinfo.value.status_code == 404


async def test_health_is_always_true() -> None:
    storage = MemoryObjectStorage()

    assert await storage.health() is True


async def test_overwrite_replaces_content() -> None:
    storage = MemoryObjectStorage()
    await storage.put_text("key", "first")

    await storage.put_text("key", "second")

    assert await storage.get_text("key") == "second"
