"""An in-memory ``ObjectStorage`` for unit tests and offline fixtures.

Never used in production: nothing here persists across process restarts,
and there is no bucket to be unreachable, so ``health`` always returns
``True``.
"""

from __future__ import annotations

from northforge.storage.base import ObjectNotFoundError, ObjectStorage


class MemoryObjectStorage(ObjectStorage):
    """A dict-backed object store."""

    def __init__(self) -> None:
        self._objects: dict[str, str] = {}

    async def put_text(self, key: str, text: str, content_type: str = "text/plain") -> str:
        self._objects[key] = text
        return key

    async def get_text(self, key: str) -> str:
        try:
            return self._objects[key]
        except KeyError:
            raise ObjectNotFoundError(f"No object at key {key!r}.") from None

    async def exists(self, key: str) -> bool:
        return key in self._objects

    async def health(self) -> bool:
        return True

    async def ensure_bucket(self) -> None:
        return None
