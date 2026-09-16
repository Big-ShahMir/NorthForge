"""Object storage: an ``ObjectStorage`` protocol plus S3 and in-memory backends."""

from __future__ import annotations

from northforge.storage.base import ObjectNotFoundError, ObjectStorage
from northforge.storage.memory import MemoryObjectStorage
from northforge.storage.s3 import S3ObjectStorage

__all__ = [
    "MemoryObjectStorage",
    "ObjectNotFoundError",
    "ObjectStorage",
    "S3ObjectStorage",
]
