"""Object storage protocol shared by the S3 and in-memory implementations.

Raw document content is stored in an S3-compatible object store (MinIO
locally) rather than only in PostgreSQL, so the exact bytes ingested are
always recoverable independent of chunking, and so large content never
bloats the primary database. ``ObjectStorage`` is intentionally small --
text in, text out -- because nothing in NorthForge stores document content
as anything other than UTF-8 markdown.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from northforge.core.errors import AppError


class ObjectNotFoundError(AppError):
    """Raised when ``get_text`` is called for a key that does not exist."""

    code = "STORAGE_OBJECT_NOT_FOUND"
    status_code = 404


@runtime_checkable
class ObjectStorage(Protocol):
    """A minimal async object store: put, get, existence, and health."""

    async def put_text(self, key: str, text: str, content_type: str = "text/plain") -> str:
        """Write ``text`` to ``key`` and return the key."""
        ...

    async def get_text(self, key: str) -> str:
        """Read the text stored at ``key``.

        Raises ``ObjectNotFoundError`` when ``key`` does not exist.
        """
        ...

    async def exists(self, key: str) -> bool:
        """Whether an object exists at ``key``."""
        ...

    async def health(self) -> bool:
        """Whether the store is reachable and the configured bucket usable."""
        ...

    async def ensure_bucket(self) -> None:
        """Create the backing bucket or container when it does not exist."""
        ...
