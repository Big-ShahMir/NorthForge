"""S3-compatible object storage (MinIO locally, any S3 API in production).

Every method opens its own client via ``session.create_client(...)`` inside
an ``async with`` block rather than holding a long-lived client, trading a
small per-call connection cost for never needing lifecycle management of a
shared client alongside the app's other resources. Credentials are read
once from ``Settings`` and passed to ``create_client``; they are never
logged, including in error paths (exceptions are logged with ``repr()`` of
botocore's own message, which does not echo the access key or secret).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from aiobotocore.config import AioConfig
from aiobotocore.session import get_session
from botocore.exceptions import ClientError

from northforge.storage.base import ObjectNotFoundError, ObjectStorage

if TYPE_CHECKING:
    from types_aiobotocore_s3.client import S3Client

    from northforge.core.config import Settings

logger = logging.getLogger(__name__)


def _error_code(exc: ClientError) -> str | None:
    return exc.response.get("Error", {}).get("Code")


class S3ObjectStorage(ObjectStorage):
    """An ``ObjectStorage`` backed by an S3-compatible endpoint."""

    def __init__(self, settings: Settings) -> None:
        self._endpoint_url = settings.s3_endpoint
        self._bucket = settings.s3_bucket
        self._access_key = settings.s3_access_key.get_secret_value()
        self._secret_key = settings.s3_secret_key.get_secret_value()
        self._region = settings.s3_region
        self._use_ssl = settings.s3_use_ssl
        self._config = AioConfig(
            connect_timeout=settings.s3_timeout_seconds,
            read_timeout=settings.s3_timeout_seconds,
            retries={"max_attempts": 1},
        )
        self._session = get_session()

    def _client_context(self) -> Any:
        return self._session.create_client(
            "s3",
            endpoint_url=self._endpoint_url,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
            region_name=self._region,
            use_ssl=self._use_ssl,
            config=self._config,
        )

    async def ensure_bucket(self) -> None:
        """Create the configured bucket if it does not already exist."""
        async with self._client_context() as raw_client:
            client = cast("S3Client", raw_client)
            try:
                await client.head_bucket(Bucket=self._bucket)
            except ClientError as exc:
                if _error_code(exc) not in {"404", "NoSuchBucket"}:
                    raise
                create_kwargs: dict[str, Any] = {"Bucket": self._bucket}
                if self._region != "us-east-1":
                    create_kwargs["CreateBucketConfiguration"] = {
                        "LocationConstraint": self._region
                    }
                await client.create_bucket(**create_kwargs)
                logger.info("created object storage bucket", extra={"bucket": self._bucket})

    async def put_text(self, key: str, text: str, content_type: str = "text/plain") -> str:
        async with self._client_context() as raw_client:
            client = cast("S3Client", raw_client)
            await client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=text.encode("utf-8"),
                ContentType=content_type,
            )
        return key

    async def get_text(self, key: str) -> str:
        async with self._client_context() as raw_client:
            client = cast("S3Client", raw_client)
            try:
                response = await client.get_object(Bucket=self._bucket, Key=key)
            except ClientError as exc:
                if _error_code(exc) in {"NoSuchKey", "404"}:
                    raise ObjectNotFoundError(f"No object at key {key!r}.") from exc
                raise
            body: bytes = await response["Body"].read()
        return body.decode("utf-8")

    async def exists(self, key: str) -> bool:
        async with self._client_context() as raw_client:
            client = cast("S3Client", raw_client)
            try:
                await client.head_object(Bucket=self._bucket, Key=key)
            except ClientError as exc:
                if _error_code(exc) in {"404", "NoSuchKey"}:
                    return False
                raise
        return True

    async def health(self) -> bool:
        try:
            async with self._client_context() as raw_client:
                client = cast("S3Client", raw_client)
                await client.head_bucket(Bucket=self._bucket)
        except (ClientError, OSError) as exc:
            logger.warning("object storage health check failed", extra={"error": repr(exc)})
            return False
        return True
